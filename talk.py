from config import ensure_venv
ensure_venv()
if __name__ == "__main__":
    from main import main
    raise SystemExit(main("talk"))

import queue
import time

from config import ASR_RATE, GEMMA_CONTEXT, GEMMA_GEN, Paths, load_settings, system_prompt
from asr import Capture, classify_utterance, transcribe
from generation import SPOKEN_TURN_CHARS, SPOKEN_TURN_WORDS, Segmenter, fit_spoken_unit, gemma_stream, spoken
from journal import finish_cleanup, join_or_fail
from runtime import CancelableHTTP, Residents
from tts import Synthesis

_EOF = object()


class Conversation(Synthesis):
    def __init__(self, paths: Paths, residents: Residents, settings: dict, language: str) -> None:
        super().__init__(paths, residents)
        self.settings, self.language = settings, language
        self.parakeet, self.gemma = residents.require_alive("parakeet"), residents.require_alive("gemma")
        self.asr_http, self.gemma_http = CancelableHTTP(), CancelableHTTP()
        self.recognition_q: queue.SimpleQueue = queue.SimpleQueue(); self.generation_q: queue.SimpleQueue = queue.SimpleQueue()
        self.latest_utterance = self.interruption_epoch = 0; self.stopping = False
        self.history: list[dict[str, str]] = []; self.fragment = ""
        self.capture = Capture(paths, settings, self._speech_start, self._utterance)
        self.recognition_thread = self.generation_thread = None

    def start(self) -> None:
        self.start_output()
        self.recognition_thread = self.paths.supervisor.start("recognition", self._recognition)
        self.generation_thread = self.paths.supervisor.start("generation", self._generation)
        self.capture.open()

    def _speech_start(self, utterance_id: int) -> dict:
        self.latest_utterance = utterance_id; old_epoch = self.epoch; playback = self.sink.snapshot()
        self.interruption_epoch = self.advance("request", utterance_id, preserve_playback=True)
        self.gemma_http.close(); self.asr_http.close()
        return {"old_epoch": old_epoch, "new_epoch": self.interruption_epoch, "preserve_playback": True,
            "native_advance_sent": True, **playback}

    def _utterance(self, utterance_id: int, pcm: bytes) -> None:
        self.recognition_q.put((utterance_id, pcm, time.perf_counter_ns()))
        self.journal.emit("asr", "queued", utterance_id=utterance_id, input_s=round(len(pcm) / (ASR_RATE * 4), 3))

    def _live(self, epoch: int) -> bool:
        with self.lock: return epoch == self.epoch

    def _recognition(self) -> None:
        while (item := self.recognition_q.get()) is not _EOF:
            if self.stopping: continue
            utterance_id, pcm, queued_ns = item
            dequeued_ns, duration, started = time.perf_counter_ns(), len(pcm) / (ASR_RATE * 4), time.perf_counter()
            if utterance_id != self.latest_utterance:
                self.journal.emit("asr", "completed", utterance_id=utterance_id, accepted=False, input_s=round(duration, 3), total_ms=0.0, rtf=0.0, queue_ms=round((dequeued_ns - queued_ns) / 1e6, 3), chars=0, text="")
                continue
            try: text = transcribe(self.parakeet, pcm, self.asr_http)
            except Exception:
                if self.stopping or utterance_id != self.latest_utterance: text = ""
                else: raise
            total, live = time.perf_counter() - started, utterance_id == self.latest_utterance
            accepted = live and bool(text)
            self.journal.emit("asr", "completed", utterance_id=utterance_id, accepted=accepted, input_s=round(duration, 3), total_ms=round(total * 1000, 3), rtf=round(total / duration, 3), queue_ms=round((dequeued_ns - queued_ns) / 1e6, 3), chars=len(text), text=text if accepted else "")
            if not live: continue
            if not text: self.resume(utterance_id); continue
            self.journal.transcript("user", text); print(f"\nuser: {text}", flush=True)
            intent, generation = classify_utterance(text), None
            if intent == "backchannel":
                self.resume(utterance_id); playback_action, applied = "resumed", True
            elif intent == "stop":
                playback_action, applied = "cutover", self.cutover(self.interruption_epoch, "stop", utterance_id)
            else:
                epoch = self.interruption_epoch
                playback_action, applied = "cutover", self.cutover(epoch, "request", utterance_id)
                if applied: generation = (epoch, utterance_id, text)
            self.journal.emit("conversation", "intent", utterance_id=utterance_id, intent=intent, playback_action=playback_action, action_applied=applied)
            if generation is not None: self.generation_q.put(generation)

    def _system(self) -> str:
        return system_prompt(self.language, str(self.settings.get("system_prompt") or ""))

    @staticmethod
    def _bytes(messages: list[dict[str, str]]) -> int:
        return sum(len(message["content"].encode("utf-8")) for message in messages)

    def _trim_history(self) -> None:
        budget = GEMMA_CONTEXT - int(GEMMA_GEN["max_tokens"]) - 256 - len(self._system().encode("utf-8"))
        while self.history and self._bytes(self.history) > max(0, budget): del self.history[:2]

    def _messages(self, prompt: str) -> list[dict[str, str]]:
        fixed = [{"role": "system", "content": self._system()}, {"role": "user", "content": prompt}]
        remaining = GEMMA_CONTEXT - int(GEMMA_GEN["max_tokens"]) - 256 - self._bytes(fixed)
        if remaining < 0: raise RuntimeError("accepted utterance exceeds conservative Gemma context budget")
        kept: list[dict[str, str]] = []
        for i in range(len(self.history) - 2, -1, -2):
            pair = self.history[i:i + 2]; cost = self._bytes(pair)
            if cost > remaining: break
            kept[0:0] = pair; remaining -= cost
        return [fixed[0], *kept, fixed[1]]

    def _generation(self) -> None:
        while (item := self.generation_q.get()) is not _EOF:
            if self.stopping: continue
            epoch, utterance_id, prompt = item
            if not self._live(epoch): continue
            merged = " ".join(part for part in (self.fragment, prompt) if part).strip()
            with self.lock:
                self.response_id += 1; response_id = self.response_id
            segmenter, raw, units, piece_id, started, first, budget_reached = Segmenter(), "", [], 0, time.perf_counter(), True, False
            def queue_unit(unit: str) -> bool:
                nonlocal piece_id, budget_reached
                words, chars = sum(len(part.split()) for part in units), len(" ".join(units))
                unit, truncated = fit_spoken_unit(unit, SPOKEN_TURN_WORDS - words,
                    SPOKEN_TURN_CHARS - chars - (1 if units else 0))
                if not unit:
                    budget_reached = True; return False
                piece_id += 1
                if not self.send_sentence(epoch, response_id, piece_id, unit): return False
                units.append(unit)
                budget_reached = truncated or sum(len(part.split()) for part in units) >= SPOKEN_TURN_WORDS or len(" ".join(units)) >= SPOKEN_TURN_CHARS
                return not budget_reached
            messages = self._messages(merged)
            self.journal.emit("gemma", "start", epoch=epoch, utterance_id=utterance_id, response_id=response_id, chars=len(merged), retained_turns=len(messages) - 2)
            cancelled = False
            try:
                stream = gemma_stream(self.gemma, messages, self.gemma_http)
                for delta in stream:
                    if not self._live(epoch): cancelled = True; break
                    if first:
                        first = False; self.journal.emit("gemma", "first_result", epoch=epoch, utterance_id=utterance_id, response_id=response_id, latency_ms=round((time.perf_counter() - started) * 1000, 3))
                    raw += delta
                    for unit in segmenter.take(spoken(raw)):
                        if not queue_unit(unit): break
                    if budget_reached: break
                if budget_reached: stream.close()
            except Exception:
                if self._live(epoch) and not self.stopping: raise
                cancelled = True
            live, generated = self._live(epoch), spoken(raw)
            if cancelled or not live:
                if generated: self.journal.transcript("assistant", generated)
                self.journal.emit("gemma", "cancelled", epoch=epoch, response_id=response_id, elapsed_ms=round((time.perf_counter() - started) * 1000, 3), chars=len(generated)); continue
            if not budget_reached:
                for unit in segmenter.take(generated, True):
                    if not queue_unit(unit): break
            answer = " ".join(units)
            if answer:
                self.fragment = ""; self.history.extend(({"role": "user", "content": merged}, {"role": "assistant", "content": answer})); self._trim_history(); self.journal.transcript("assistant", answer); print(f"assistant: {answer}", flush=True)
            else:
                self.fragment = merged
            self.journal.emit("gemma", "completed", epoch=epoch, utterance_id=utterance_id, response_id=response_id, empty=not answer, elapsed_ms=round((time.perf_counter() - started) * 1000, 3), chars=len(answer), generated_chars=len(generated), pieces=piece_id, budget_reached=budget_reached)

    def check(self) -> None:
        self.capture.check(); super().check()

    def stop(self, cancel: bool) -> None:
        self.stopping = True; self.asr_http.close(); self.gemma_http.close()
        primary = None
        try: self.capture.close()
        except BaseException as error:
            primary = (error, error.__traceback__); self.journal.failure("cleanup.capture", error)
        self.recognition_q.put(_EOF); self.generation_q.put(_EOF)
        finish_cleanup(self.paths, primary, [
            ("recognition", lambda: join_or_fail(self.recognition_thread, "recognition")),
            ("generation", lambda: join_or_fail(self.generation_thread, "generation")),
            ("synthesis", lambda: self.stop_output(cancel)),
        ])


def launch(paths: Paths, family: str = "nano", language: str = "en", primary: str | None = None,
           replacement: str | None = None, interrupt_after: float | None = None) -> None:
    residents, mode, failure = Residents(paths), None, None
    try:
        residents.boot(family, language)
        mode = Conversation(paths, residents, load_settings(paths.data_dir), language); mode.start()
        paths.journal.emit("main", "ready", family=family, language=language); print("trident.ready", flush=True)
        while True: mode.check(); paths.supervisor.wait(.02)
    except BaseException as error:
        failure = (error, error.__traceback__)
    finish_cleanup(paths, failure, ([("talk", lambda: mode.stop(cancel=failure is not None))] if mode is not None else []) + [("residents", residents.stop), ("supervisor", lambda: paths.supervisor.join(1))])
