from config import ensure_venv
ensure_venv()
if __name__ == "__main__":
    from main import main
    raise SystemExit(main("talk"))

import queue
import time

from config import Paths, load_settings, system_prompt
from asr import Capture, classify_utterance
from generation import (
    HELLO_TOOL, SPOKEN_TURN_CHARS, SPOKEN_TURN_WORDS, Segmenter, fit_spoken_unit,
    gemma_prefill, gemma_stream, spoken, tool_round,
)
from journal import finish_cleanup, join_or_fail
from runtime import CancelableHTTP, Residents
from tts import Synthesis

_EOF = object()


class Conversation(Synthesis):
    def __init__(self, paths: Paths, residents: Residents, settings: dict, language: str) -> None:
        super().__init__(paths, residents)
        self.settings, self.language = settings, language
        self.gemma = residents.require_alive("gemma")
        self.gemma_http, self.prefill_http = CancelableHTTP(), CancelableHTTP()
        self.prefill_q: queue.SimpleQueue = queue.SimpleQueue(); self.generation_q: queue.SimpleQueue = queue.SimpleQueue()
        self.latest_utterance = self.latest_candidate_generation = self.interruption_epoch = 0; self.stopping = False
        self.history: list[dict] = []
        self.capture = Capture(paths, settings, language, self._speech_start, self._utterance, self._partial, self._speech_resume)
        self.prefill_thread = self.generation_thread = None

    def start(self) -> None:
        self.start_output()
        self.prefill_thread = self.paths.supervisor.start("prefill", self._prefill_worker)
        self.generation_thread = self.paths.supervisor.start("generation", self._generation)
        self.capture.open()

    def _speech_start(self, utterance_id: int, generation: int) -> dict:
        self.latest_utterance = utterance_id; self.latest_candidate_generation = generation
        old_epoch, playback = self.epoch, self.sink.snapshot()
        self.interruption_epoch = self.advance("request", utterance_id, preserve_playback=True)
        self.gemma_http.close(); self.prefill_http.close()
        return {"old_epoch": old_epoch, "new_epoch": self.interruption_epoch, "preserve_playback": True,
                "native_advance_sent": True, **playback}

    def _speech_resume(self, utterance_id: int, generation: int) -> None:
        if utterance_id != self.latest_utterance: return
        self.latest_candidate_generation = generation
        self.prefill_http.close()
        self.journal.emit("conversation", "speculation.cancelled", utterance_id=utterance_id, candidate_generation=generation, reason="speech-resumed")

    def _partial(self, utterance_id: int, generation: int, text: str, flags: int) -> None:
        if utterance_id != self.latest_utterance or generation != self.latest_candidate_generation: return
        print(f"\ruser~: {text}", end="", flush=True)
        if len(text.split()) >= self.paths.prefill_min_words:
            self.prefill_q.put((utterance_id, generation, text))

    def _utterance(self, utterance_id: int, generation: int, text: str, duration: float) -> None:
        if utterance_id != self.latest_utterance: return
        self.latest_candidate_generation = generation + 1; self.prefill_http.close()
        accepted = bool(text)
        self.journal.emit("asr", "completed", utterance_id=utterance_id, candidate_generation=generation, accepted=accepted,
                          mode="capi-stream", input_s=round(duration, 3), chars=len(text), text=text if accepted else "")
        if not text:
            self.resume(utterance_id); return
        self.journal.transcript("user", text); print(f"\nuser: {text}", flush=True)
        intent, generation_item = classify_utterance(text), None
        if intent == "backchannel":
            self.resume(utterance_id); playback_action, applied = "resumed", True
        elif intent == "stop":
            playback_action, applied = "cutover", self.cutover(self.interruption_epoch, "stop", utterance_id)
        else:
            epoch = self.interruption_epoch; playback_action, applied = "cutover", self.cutover(epoch, "request", utterance_id)
            if applied: generation_item = (epoch, utterance_id, text)
        self.journal.emit("conversation", "intent", utterance_id=utterance_id, intent=intent, playback_action=playback_action, action_applied=applied)
        if generation_item is not None: self.generation_q.put(generation_item)

    def _preview_live(self, utterance_id: int, generation: int) -> bool:
        return utterance_id == self.latest_utterance and generation == self.latest_candidate_generation

    def _system(self) -> str:
        return system_prompt(self.language, self.paths.system_prompt, spoken=True)

    def _messages(self, prompt: str) -> list[dict]:
        kept = self.history[-2 * self.paths.history_turns:] if self.paths.history_mode == "conversation" else []
        return [{"role": "system", "content": self._system()}, *kept, {"role": "user", "content": prompt}]

    def _remember(self, prompt: str, answer: str) -> None:
        if self.paths.history_mode != "conversation": return
        self.history.extend(({"role": "user", "content": prompt}, {"role": "assistant", "content": answer}))
        del self.history[:-2 * self.paths.history_turns]

    def _prefill_worker(self) -> None:
        while (item := self.prefill_q.get()) is not _EOF:
            if self.stopping: continue
            utterance_id, generation, text = item
            if not self._preview_live(utterance_id, generation): continue
            started = time.perf_counter(); self.journal.emit("gemma", "prefill.start", utterance_id=utterance_id, candidate_generation=generation, chars=len(text))
            try:
                data = gemma_prefill(self.gemma, self._messages(text), self.prefill_http, "off")
            except Exception:
                if self.stopping or not self._preview_live(utterance_id, generation):
                    self.journal.emit("gemma", "prefill.cancelled", utterance_id=utterance_id, candidate_generation=generation, elapsed_ms=round((time.perf_counter() - started) * 1000, 3)); continue
                raise
            timings = data.get("timings") or {}
            self.journal.emit("gemma", "prefill.completed", utterance_id=utterance_id, candidate_generation=generation,
                              live=self._preview_live(utterance_id, generation), elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
                              prompt_tokens=int((data.get("usage") or {}).get("prompt_tokens") or 0), cached_tokens=int(timings.get("cache_n") or 0),
                              processed_tokens=int(timings.get("prompt_n") or 0))

    def _generation(self) -> None:
        while (item := self.generation_q.get()) is not _EOF:
            if self.stopping: continue
            epoch, utterance_id, prompt = item
            if not self._live(epoch): continue
            with self.lock:
                self.response_id += 1; response_id = self.response_id
            segmenter, raw, units, piece_id, started, first, budget_reached = Segmenter(), "", [], 0, time.perf_counter(), True, False

            def queue_unit(unit: str) -> bool:
                nonlocal piece_id, budget_reached
                words, chars = sum(len(part.split()) for part in units), len(" ".join(units))
                unit, truncated = fit_spoken_unit(unit, SPOKEN_TURN_WORDS - words, SPOKEN_TURN_CHARS - chars - (1 if units else 0))
                if not unit: budget_reached = True; return False
                piece_id += 1
                if not self.send_sentence(epoch, response_id, piece_id, unit): return False
                units.append(unit)
                budget_reached = truncated or sum(len(part.split()) for part in units) >= SPOKEN_TURN_WORDS or len(" ".join(units)) >= SPOKEN_TURN_CHARS
                return not budget_reached

            messages = self._messages(prompt); retained = max(0, (len(messages) - 2) // 2)
            self.journal.emit("gemma", "start", epoch=epoch, utterance_id=utterance_id, response_id=response_id, chars=len(prompt),
                              retained_turns=retained, history_mode=self.paths.history_mode, thinking=self.paths.thinking, tools=self.paths.tools_enabled)
            cancelled = False
            try:
                direct = augmented = None
                if self.paths.tools_enabled:
                    direct, augmented, results = tool_round(self.gemma, messages, self.gemma_http, self.paths.gemma_gen, self.paths.thinking, self.paths.thinking_budget)
                    for name, result in results:
                        self.journal.emit("gemma", "tool.completed", epoch=epoch, response_id=response_id, name=name, result_chars=len(result))
                if direct is not None:
                    raw = direct
                    if raw and first:
                        first = False; self.journal.emit("gemma", "first_result", epoch=epoch, utterance_id=utterance_id, response_id=response_id,
                                                        latency_ms=round((time.perf_counter() - started) * 1000, 3))
                else:
                    stream = gemma_stream(self.gemma, augmented or messages, self.gemma_http, self.paths.gemma_gen, self.paths.thinking,
                                          self.paths.thinking_budget, [HELLO_TOOL] if augmented else None, "none" if augmented else None)
                    for delta in stream:
                        if not self._live(epoch): cancelled = True; break
                        if first:
                            first = False; self.journal.emit("gemma", "first_result", epoch=epoch, utterance_id=utterance_id, response_id=response_id,
                                                            latency_ms=round((time.perf_counter() - started) * 1000, 3))
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
                self.journal.emit("gemma", "cancelled", epoch=epoch, response_id=response_id,
                                  elapsed_ms=round((time.perf_counter() - started) * 1000, 3), chars=len(generated)); continue
            if not budget_reached:
                for unit in segmenter.take(generated, True):
                    if not queue_unit(unit): break
            answer = " ".join(units)
            if answer:
                self._remember(prompt, answer); self.journal.transcript("assistant", answer); print(f"assistant: {answer}", flush=True)
            self.journal.emit("gemma", "completed", epoch=epoch, utterance_id=utterance_id, response_id=response_id, empty=not answer,
                              elapsed_ms=round((time.perf_counter() - started) * 1000, 3), chars=len(answer), generated_chars=len(generated),
                              pieces=piece_id, budget_reached=budget_reached)

    def check(self) -> None:
        self.capture.check(); super().check()

    def stop(self, cancel: bool) -> None:
        self.stopping = True; self.gemma_http.close(); self.prefill_http.close()
        primary = None
        try: self.capture.close()
        except BaseException as error:
            primary = (error, error.__traceback__); self.journal.failure("cleanup.capture", error)
        self.prefill_q.put(_EOF); self.generation_q.put(_EOF)
        finish_cleanup(self.paths, primary, [
            ("prefill", lambda: join_or_fail(self.prefill_thread, "prefill")),
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
    finish_cleanup(paths, failure, ([("talk", lambda: mode.stop(cancel=failure is not None))] if mode is not None else []) +
                   [("residents", residents.stop), ("supervisor", lambda: paths.supervisor.join(1))])
