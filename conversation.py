from __future__ import annotations

import queue
import threading
import time
import wave
from pathlib import Path

import numpy as np

from config import ASR_RATE, BRAIN_MODEL, BRAIN_RUNTIME, LANGUAGES, SHARED_MODELS, resolve_voice
from installer import require_model, runtime_server
from local_api import gemma_chat_stream
from log import clear_run_log, note, set_run_log
from main import effective_family, finish, gemma_kwargs, prepared_reference, render_system_prompt, resolved_tts, spoken_reply, start_run, stream_synthesize, synthesize_text, transcribe_wav, tts_endpoint, write_meta
from resident import ensure_gemma, ensure_parakeet
from ui_streaming import SpeechSegmenter
from vad import SileroEndpoint


class Conversation:
    def __init__(self, models_dir: Path, data_dir: Path, settings: dict, paths=None, output_audio: bool = True) -> None:
        self.models_dir = models_dir
        self.data_dir = data_dir
        self.settings = dict(settings)
        self.paths = paths
        self.owns_run = paths is None
        self.output_audio = output_audio
        self.parakeet = None
        self.vad = None
        self.gemma_base = None
        self.turn_wave = None
        self.turn_path = None
        self.turn_index = 0
        self.asr_queue = queue.SimpleQueue()
        self.llm_queue = queue.SimpleQueue()
        self.tts_queue = queue.SimpleQueue()
        self.output_queue = queue.SimpleQueue()
        self.asr_thread = None
        self.llm_thread = None
        self.tts_thread = None
        self.active = False
        self.failure = None
        self.turn = 0
        self.brain_seq = 0
        self.pending = ""
        self.transcript = ""
        self.answer = ""
        self.status = "Stopped"
        self.history = []
        self.references: dict[str, Path] = {}
        self.cancelled_through = 0
        self.tts_started_through = 0
        self.tts_done_through = 0

    def _family(self, settings: dict, streaming: bool):
        return effective_family(settings["tts_family"], {"streaming": streaming, "stream_join": settings["tts_join"]})

    def _require_language(self, settings: dict) -> None:
        family = self._family(settings, settings["tts_mode"] == "real")
        language = settings["tts_language"]
        if language not in family["TTS_LANGUAGES"]:
            raise RuntimeError(f"language {language!r} is not wired in {family['name']}")

    def _require_engine(self) -> None:
        if self.failure:
            raise RuntimeError(str(self.failure))
        if not self.active:
            raise RuntimeError("conversation is not active")

    def _reference(self, voice: str) -> Path:
        source = resolve_voice(self.data_dir, voice).resolve()
        key = str(source)
        if key not in self.references:
            self.references[key] = prepared_reference(source, self.data_dir)
        return self.references[key]

    def _emit(self, kind: str, payload=None) -> None:
        if self.output_audio or not kind.startswith("audio-"):
            self.output_queue.put((kind, payload))

    def _state(self, status: str | None = None) -> None:
        if status is not None:
            self.status = status
        self._emit("state")

    def _worker(self, target, name: str) -> threading.Thread:
        def run():
            set_run_log(self.paths.log)
            try:
                target()
            except Exception as exc:
                self.failure = exc
                self.status = f"{name} failed · {exc}"
                self._emit("error", exc)
            finally:
                clear_run_log(self.paths.log)

        return threading.Thread(target=run, name=name, daemon=True)

    def start(self) -> None:
        if self.active:
            raise RuntimeError("conversation is already active")
        if self.paths is None:
            self.paths = start_run("conversation", self.models_dir, self.data_dir)
        else:
            set_run_log(self.paths.log)
        try:
            self.parakeet = ensure_parakeet(runtime_server("parakeet"), require_model(SHARED_MODELS["parakeet"], self.models_dir))
            self.gemma_base = ensure_gemma(runtime_server("gemma"), require_model(SHARED_MODELS[BRAIN_MODEL], self.models_dir), BRAIN_RUNTIME)
            settings = dict(self.settings)
            self._require_language(settings)
            family = self._family(settings, settings["tts_mode"] == "real")
            tts_endpoint(self._reference(settings["tts_voice"]), settings["tts_language"], family, self.paths)
            self.vad = SileroEndpoint(settings["vad_threshold"], settings["vad_silence_ms"])
            self.active = True
            self.asr_thread = self._worker(self._asr_loop, "trident-asr")
            self.llm_thread = self._worker(self._llm_loop, "trident-llm")
            self.tts_thread = self._worker(self._tts_loop, "trident-tts")
            self.asr_thread.start()
            self.llm_thread.start()
            self.tts_thread.start()
            self._state("Listening · Parakeet ASR · Gemma · TTS resident")
        except Exception:
            if self.owns_run:
                finish(self.paths, "error")
            raise

    def configure(self, settings: dict) -> None:
        self._require_language(settings)
        vad_changed = settings["vad_threshold"] != self.settings["vad_threshold"] or settings["vad_silence_ms"] != self.settings["vad_silence_ms"]
        self.settings = dict(settings)
        if vad_changed:
            self.asr_queue.put(("vad-config", (self.settings["vad_threshold"], self.settings["vad_silence_ms"])))
        note(
            f"component=conversation event=configure ingestion={settings['ingestion_mode']}"
            f" tts_family={settings['tts_family']} tts_mode={settings['tts_mode']}"
            f" tts_language={settings['tts_language']} vad_threshold={settings['vad_threshold']}"
            f" vad_silence_ms={settings['vad_silence_ms']}"
        )
        self._state("Configuration applied")

    def submit(self, text: str) -> None:
        self._require_engine()
        self._interrupt()
        self.asr_queue.put(("manual", text.strip()))

    def submit_audio(self, pcm_f32: bytes) -> None:
        self._require_engine()
        if not pcm_f32:
            self._state("Push-to-talk · no audio")
            return
        self._interrupt()
        self.asr_queue.put(("feed", pcm_f32))
        self.asr_queue.put(("cut", "PTT"))
        self._state("Push-to-talk · finalizing")

    def feed_audio(self, pcm_f32: bytes) -> None:
        self._require_engine()
        if self.settings["ingestion_mode"] != "continuous":
            return
        if pcm_f32:
            self.asr_queue.put(("feed", pcm_f32))

    def _interrupt(self) -> None:
        self.brain_seq += 1
        self._interrupt_tts()

    def _interrupt_tts(self) -> None:
        target = self.turn
        if target < 1:
            return
        self._emit("audio-reset")
        if target <= self.tts_done_through or target <= self.cancelled_through:
            return
        self.cancelled_through = target
        note(f"component=tts event=interruption_requested through_turn={target}")
        self._state(f"TTS through {target} · interrupted")

    def _tts_cancelled(self, turn: int) -> bool:
        return turn <= self.cancelled_through

    def _append_turn(self, pcm_f32: bytes) -> None:
        if self.turn_wave is None:
            self.turn_index += 1
            self.turn_path = self.paths.run_dir / f".turn-{self.turn_index:04d}.wav"
            self.turn_wave = wave.open(str(self.turn_path), "wb")
            self.turn_wave.setnchannels(1)
            self.turn_wave.setsampwidth(2)
            self.turn_wave.setframerate(ASR_RATE)
        audio = np.frombuffer(pcm_f32, dtype="<f4")
        pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        self.turn_wave.writeframesraw(pcm16)

    def _take_turn(self) -> Path | None:
        if self.turn_wave is None:
            return None
        self.turn_wave.close()
        path = self.turn_path
        self.turn_wave = self.turn_path = None
        return path

    def _discard_turn(self) -> None:
        path = self._take_turn()
        if path:
            path.unlink(missing_ok=True)

    def _transcribe_turn(self, reason: str) -> None:
        path = self._take_turn()
        if not path:
            return
        try:
            self._state(f"Parakeet · transcribing · {reason}")
            text = transcribe_wav(path, self.parakeet, self.paths.run_dir / ".asr-chunks")
        finally:
            path.unlink(missing_ok=True)
        if not text:
            self._state(f"Parakeet · no speech · {reason}")
            return
        self._dispatch(reason, text)

    def _asr_loop(self) -> None:
        while True:
            op, payload = self.asr_queue.get()
            if op == "feed":
                self._append_turn(payload)
                if self.settings["ingestion_mode"] == "continuous":
                    speech_started, speech_ended = self.vad.feed(payload)
                    if speech_started:
                        self._interrupt()
                    if speech_ended:
                        self._transcribe_turn("VAD")
                        self.vad.reset()
            elif op == "cut":
                self.vad.reset()
                self._transcribe_turn(payload)
            elif op == "manual":
                if payload:
                    self.pending = ""
                    self._dispatch("MANUAL", payload)
                else:
                    self.vad.reset()
                    self._transcribe_turn("MANUAL")
            elif op == "vad-config":
                self.vad.configure(*payload)
            elif op == "finish":
                if self.vad.speech:
                    self._transcribe_turn("STOP")
                else:
                    self._discard_turn()
                return
            else:
                raise RuntimeError(f"unknown ASR queue operation: {op}")

    def _dispatch(self, reason: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self.pending = f"{self.pending} {text}".strip() if self.pending else text
        self.transcript = (self.transcript.rstrip() + " " + text).strip()
        self.brain_seq += 1
        seq = self.brain_seq
        note(f"component=conversation event=dispatch seq={seq} reason={reason} pending_chars={len(self.pending)}")
        self.llm_queue.put((seq, self.pending, dict(self.settings)))
        self._state(f"Dispatch {seq} · {reason} · {len(self.pending)} chars")

    def _llm_payload(self, text: str, settings: dict) -> dict:
        language = settings["tts_language"]
        system = render_system_prompt(settings["system_prompt"], language, LANGUAGES[language])
        return gemma_kwargs(
            [{"role": "system", "content": system}, *self.history, {"role": "user", "content": text}],
            stream=True,
        )

    def _llm_loop(self) -> None:
        base = self.gemma_base
        while True:
            item = self.llm_queue.get()
            if item is None:
                return
            seq, prompt, settings = item
            if seq != self.brain_seq:
                continue
            self._require_language(settings)
            self.answer = ""
            self._state(f"LLM {seq} · generating")
            raw = ""
            started = time.perf_counter()
            ttfa = None
            family = self._family(settings, settings["tts_mode"] == "real")
            hard_limit = int(family["TTS_CHUNK"]["chars"])
            segmenter = SpeechSegmenter(min(int(family["TTS_CHUNK"]["first_chars"]), hard_limit), hard_limit)
            speech_started = False
            turn = None

            def enqueue(units) -> None:
                nonlocal speech_started, turn
                if seq != self.brain_seq:
                    return
                for unit in units:
                    if speech_started:
                        self.tts_queue.put(("unit", turn, unit.text, None))
                    else:
                        self.turn += 1
                        turn = self.turn
                        self.tts_queue.put(("start", turn, unit.text, settings))
                        speech_started = True

            cancelled = False
            for delta in gemma_chat_stream(base, self._llm_payload(prompt, settings)):
                if seq != self.brain_seq:
                    cancelled = True
                    break
                if ttfa is None:
                    ttfa = time.perf_counter() - started
                raw += delta
                self.answer = raw
                units = segmenter.update(spoken_reply(raw, streaming=True))
                enqueue(units)
                if units:
                    self._state(f"LLM {seq} · speech ready")
            if seq != self.brain_seq:
                cancelled = True
            if cancelled:
                if speech_started:
                    self.tts_queue.put(("end", turn, "", None))
                note(f"component=llm event=cancelled seq={seq} turn={turn or 0}")
                continue
            answer = spoken_reply(raw)
            enqueue(segmenter.update(answer, flush=True))
            if seq != self.brain_seq:
                if speech_started:
                    self.tts_queue.put(("end", turn, "", None))
                note(f"component=llm event=cancelled seq={seq} turn={turn or 0}")
                continue
            if not answer:
                if speech_started:
                    raise RuntimeError("Gemma streamed speech units then returned an empty spoken reply")
                note(f"component=llm event=wait seq={seq} pending_chars={len(prompt)}")
                self._state(f"LLM {seq} · wait")
                continue
            self.answer = answer
            self.history.extend(({"role": "user", "content": prompt}, {"role": "assistant", "content": answer}))
            if self.pending == prompt:
                self.pending = ""
            if not speech_started:
                self.turn += 1
                turn = self.turn
                self.tts_queue.put(("start", turn, answer, settings))
                speech_started = True
            self.tts_queue.put(("end", turn, answer, None))
            note(
                f"component=llm event=complete seq={seq} turn={turn} ttfa_ms={(ttfa or time.perf_counter() - started) * 1000:.3f}"
                f" total_ms={(time.perf_counter() - started) * 1000:.3f} chars={len(answer)}"
            )
            self._state(f"LLM {turn} · complete · TTS finishing")

    def _tts_loop(self) -> None:
        while True:
            item = self.tts_queue.get()
            if item is None:
                return
            op, turn, text, settings = item
            if op != "start":
                raise RuntimeError(f"unexpected TTS queue operation: {op}")
            unit, index = text, 0
            interrupted = self._tts_cancelled(turn)
            if not interrupted:
                family = self._family(settings, settings["tts_mode"] == "real")
                language = settings["tts_language"]
                reference = self._reference(settings["tts_voice"])
                streaming = settings["tts_mode"] == "real"
                base = tts_endpoint(reference, language, family, self.paths)
                while unit is not None and not interrupted:
                    index += 1
                    self._state(f"TTS {turn} · {'streaming' if streaming else 'buffered'} · speech unit {index}")
                    output = self.paths.run_dir / f"tts-turn-{turn:04d}-{index:03d}.wav"
                    cancel = lambda turn=turn: self._tts_cancelled(turn)
                    if streaming:
                        first_chunk = True
                        for raw in stream_synthesize(unit, reference, output, language, family, self.paths, base=base, unit=index, cancel=cancel):
                            if first_chunk:
                                self.tts_started_through = turn
                                first_chunk = False
                            self._emit("audio-pcm", raw)
                    else:
                        result = synthesize_text(unit, reference, output, language, family, self.paths, base=base, streaming=False, unit=index, cancel=cancel)
                        if not cancel():
                            if not output.is_file():
                                raise RuntimeError(f"Chatterbox did not create buffered unit {index}: {result}")
                            self.tts_started_through = turn
                            self._emit("audio-file", str(output))
                    interrupted = cancel()
                    if not interrupted:
                        unit, _ = self._next_tts(turn)
            if interrupted:
                while unit is not None:
                    unit, _ = self._next_tts(turn)
                note(f"component=tts event=interrupted turn={turn}")
            self.tts_done_through = turn
            self._state(f"TTS {turn} · {'interrupted' if interrupted else 'complete'}")

    def _next_tts(self, turn: int) -> tuple[str | None, str | None]:
        item = self.tts_queue.get()
        if item is None:
            raise RuntimeError(f"TTS turn {turn} ended without its marker")
        op, item_turn, text, _settings = item
        if item_turn != turn:
            raise RuntimeError(f"TTS turn {item_turn} interleaved with turn {turn}")
        if op == "unit":
            return text, None
        if op == "end":
            return None, text
        raise RuntimeError(f"unexpected TTS queue operation: {op}")

    def stop(self) -> None:
        if not self.active:
            return
        self._interrupt()
        self.active = False
        self.asr_queue.put(("finish", None))
        self.asr_thread.join()
        self.llm_queue.put(None)
        self.llm_thread.join()
        self.tts_queue.put(None)
        self.tts_thread.join()
        self.paths.transcript.write_text(self.transcript + ("\n" if self.transcript else ""), encoding="utf-8")
        if self.owns_run:
            final_family = self._family(self.settings, self.settings["tts_mode"] == "real")
            write_meta(
                self.paths,
                command="conversation",
                transcript=self.paths.transcript,
                turns=self.turn,
                reply_owner="brain",
                vad_threshold=self.settings["vad_threshold"],
                vad_silence_ms=self.settings["vad_silence_ms"],
                tts_mode=self.settings["tts_mode"],
                tts_language=self.settings["tts_language"],
                resolved_tts=resolved_tts(final_family),
            )
            set_run_log(self.paths.log)
            finish(self.paths, "error" if self.failure else "ok")
        self._state("Stopped")
        self._emit("closed")

    def close(self) -> None:
        if self.active:
            self.stop()
        else:
            self._discard_turn()

    def next_output(self):
        return self.output_queue.get()
