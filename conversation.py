from __future__ import annotations

import argparse
import queue
import threading
from pathlib import Path

from asr_live import LiveASR
from config import BRAIN_GENERATION, BRAIN_MODEL, BRAIN_RUNTIME, BRAIN_THINKING, LANGUAGES, LIVE_AUDIO, SHARED_MODELS, TTS_RATE, resolve_voice
from installer import require_model, runtime_parakeet_library, runtime_server
from local_api import chatterbox_synthesize, gemma_chat_stream
from main import effective_family, finish, prepared_reference, render_system_prompt, spoken_reply, start_run, stream_synthesize, tts_endpoint, write_meta
from resident import ensure_gemma, stop as resident_stop
from ui_streaming import SpeechSegmenter, highlighted_progress, pcm16_lookahead
from vad import SileroEndpoint


class Conversation:
    def __init__(self, models_dir: Path, data_dir: Path, settings: dict) -> None:
        self.models_dir = models_dir
        self.data_dir = data_dir
        self.settings = dict(settings)
        self.paths = None
        self.asr = None
        self.vad = None
        self.asr_queue = queue.SimpleQueue()
        self.llm_queue = queue.SimpleQueue()
        self.tts_queue = queue.SimpleQueue()
        self.output_queue = queue.SimpleQueue()
        self.asr_thread = None
        self.llm_thread = None
        self.tts_thread = None
        self.active = False
        self.failure = None
        self.ptt_open = False
        self.turn = 0
        self.transcript = ""
        self.pending = ""
        self.answer = ""
        self.status = "Stopped"
        self.progress = []
        self.history = []
        self.references: dict[str, Path] = {}

    def _family(self, settings: dict, streaming: bool):
        args = argparse.Namespace(streaming=streaming, stream_join=settings["tts_join"])
        return effective_family(settings["tts_family"], args)

    def _reference(self, voice: str) -> Path:
        source = resolve_voice(self.data_dir, voice).resolve()
        key = str(source)
        if key not in self.references:
            self.references[key] = prepared_reference(source, self.data_dir)
        return self.references[key]

    def _emit(self, kind: str, payload=None) -> None:
        self.output_queue.put((kind, payload))

    def _state(self, status: str | None = None) -> None:
        if status is not None:
            self.status = status
        self._emit("state")

    def _worker(self, target, name: str) -> threading.Thread:
        def run():
            try:
                target()
            except BaseException as exc:
                self.failure = exc
                self.status = f"{name} failed · {exc}"
                self._emit("error", exc)

        return threading.Thread(target=run, name=name, daemon=True)

    def start(self) -> None:
        if self.active:
            raise RuntimeError("conversation is already active")
        self.paths = start_run("conversation", self.models_dir, self.data_dir)
        resident_stop("parakeet")
        ensure_gemma(runtime_server("gemma"), require_model(SHARED_MODELS[BRAIN_MODEL], self.models_dir), BRAIN_RUNTIME)
        settings = dict(self.settings)
        family = self._family(settings, settings["tts_mode"] == "real")
        language = settings["tts_language"]
        if language not in family["TTS_LANGUAGES"]:
            raise RuntimeError(f"language {language!r} is not wired in {family['name']}")
        tts_endpoint(self._reference(settings["tts_voice"]), language, family, self.paths)
        self.asr = LiveASR(runtime_parakeet_library(), require_model(SHARED_MODELS["parakeet-eou"], self.models_dir))
        self.asr.start()
        self.vad = SileroEndpoint(settings["vad_threshold"], settings["vad_silence_ms"])
        self.active = True
        self.ptt_open = settings["ingestion_mode"] == "continuous"
        self.asr_thread = self._worker(self._asr_loop, "trident-asr")
        self.llm_thread = self._worker(self._llm_loop, "trident-llm")
        self.tts_thread = self._worker(self._tts_loop, "trident-tts")
        self.asr_thread.start()
        self.llm_thread.start()
        self.tts_thread.start()
        self._state("Listening · STT/LLM/TTS resident")

    def configure(self, settings: dict) -> None:
        vad_changed = settings["vad_threshold"] != self.settings["vad_threshold"] or settings["vad_silence_ms"] != self.settings["vad_silence_ms"]
        self.settings = dict(settings)
        self.ptt_open = self.settings["ingestion_mode"] == "continuous"
        if vad_changed:
            self.asr_queue.put(("vad-config", (self.settings["vad_threshold"], self.settings["vad_silence_ms"])))
        self._state("Configuration applied")

    def ptt_start(self) -> None:
        if not self.active:
            raise RuntimeError("conversation is not active")
        self.ptt_open = True
        self._state("Push-to-talk · open")

    def ptt_stop(self) -> None:
        if not self.active:
            raise RuntimeError("conversation is not active")
        self.ptt_open = False
        self.asr_queue.put(("cut", "ptt"))
        self._state("Push-to-talk · finalizing")

    def submit(self, text: str) -> None:
        if not self.active:
            raise RuntimeError("conversation is not active")
        self.asr_queue.put(("manual", text.strip()))

    def feed_audio(self, pcm_f32: bytes) -> None:
        if self.failure:
            raise RuntimeError(str(self.failure))
        if not self.active:
            raise RuntimeError("conversation is not active")
        if self.settings["ingestion_mode"] == "ptt" and not self.ptt_open:
            return
        if pcm_f32:
            self.asr_queue.put(("feed", pcm_f32))

    def _asr_loop(self) -> None:
        while True:
            op, payload = self.asr_queue.get()
            if op == "feed":
                event = self.asr.feed(payload)
                if event is not None:
                    self._apply_asr(event)
                if self.settings["vad_trigger"] and self.vad.feed(payload):
                    self._apply_asr(self.asr.cut("vad"))
            elif op == "cut":
                self._apply_asr(self.asr.cut(payload))
            elif op == "manual":
                if payload:
                    self._dispatch("MANUAL", payload)
                else:
                    self._apply_asr(self.asr.cut("manual"))
            elif op == "vad-config":
                threshold, silence_ms = payload
                self.vad.configure(threshold, silence_ms)
            elif op == "finish":
                for event in self.asr.finish():
                    self._apply_asr(event)
                return
            else:
                raise RuntimeError(f"unknown ASR queue operation: {op}")

    def _apply_asr(self, event: dict) -> None:
        fragment = event["fragment"]
        if fragment:
            self.transcript = (self.transcript.rstrip() + " " + fragment).strip()
            self.pending = (self.pending.rstrip() + " " + fragment).strip()
            self._state("ASR · partial")
        if event["eob"]:
            self._state("ASR · EOB")
        if event["eou"] and self.settings["eou_trigger"]:
            self.vad.reset()
            self._dispatch("EOU")
            return
        if event["source"] == "cut" and event["tag"] in {"vad", "ptt", "manual"}:
            self._dispatch(event["tag"].upper())
            return
        if len(self.pending) >= int(self.settings["char_trigger"]):
            self._dispatch("CHAR")

    def _dispatch(self, reason: str, text: str | None = None) -> None:
        chunk = (text if text is not None else self.pending).strip()
        if not chunk:
            return
        if text is None:
            self.pending = ""
        self.turn += 1
        self.llm_queue.put((self.turn, chunk, dict(self.settings)))
        self._state(f"Dispatch {self.turn} · {reason} · {len(chunk)} chars")

    def _llm_payload(self, text: str, settings: dict) -> dict:
        language = settings["tts_language"]
        system = render_system_prompt(settings["system_prompt"], language, LANGUAGES[language])
        limit = int(LIVE_AUDIO["llm_history_turns"]) * 2
        messages = [{"role": "system", "content": system}, *self.history[-limit:], {"role": "user", "content": text}]
        g = BRAIN_GENERATION
        return {
            "model": "gemma",
            "messages": messages,
            "stream": True,
            "cache_prompt": True,
            "temperature": g["temperature"],
            "top_p": g["top_p"],
            "top_k": g["top_k"],
            "min_p": g["min_p"],
            "repeat_penalty": g["repeat_penalty"],
            "seed": g["seed"],
            "max_tokens": g["max_tokens"],
            "chat_template_kwargs": {"enable_thinking": bool(BRAIN_THINKING)},
        }

    def _llm_loop(self) -> None:
        base = ensure_gemma(runtime_server("gemma"), require_model(SHARED_MODELS[BRAIN_MODEL], self.models_dir), BRAIN_RUNTIME)
        while True:
            item = self.llm_queue.get()
            if item is None:
                return
            turn, prompt, settings = item
            self.answer = ""
            self._state(f"LLM {turn} · generating")
            raw = ""
            segmenter = SpeechSegmenter(
                int(LIVE_AUDIO["tts_speech_min_chars"]),
                int(LIVE_AUDIO["tts_speech_hard_chars"]),
            )
            speech_started = False

            def enqueue(units: list[str]) -> None:
                nonlocal speech_started
                for unit in units:
                    if speech_started:
                        self.tts_queue.put(("unit", turn, unit))
                    else:
                        self.tts_queue.put(("start", turn, unit, settings))
                        speech_started = True

            for delta in gemma_chat_stream(base, self._llm_payload(prompt, settings)):
                raw += delta
                self.answer = raw
                self._state(f"LLM {turn} · streaming")
                enqueue(segmenter.update(spoken_reply(raw, streaming=True)))
            answer = spoken_reply(raw)
            if not answer:
                raise RuntimeError("Gemma returned an empty answer")
            enqueue(segmenter.update(answer, flush=True))
            self.answer = answer
            self.history.extend(({"role": "user", "content": prompt}, {"role": "assistant", "content": answer}))
            self.tts_queue.put(("end", turn, answer))
            self._state(f"LLM {turn} · complete · TTS finishing")

    def _tts_loop(self) -> None:
        while True:
            item = self.tts_queue.get()
            if item is None:
                return
            op, turn, text, settings = item
            if op != "start":
                raise RuntimeError(f"unexpected TTS queue operation: {op}")
            family = self._family(settings, settings["tts_mode"] == "real")
            language = settings["tts_language"]
            reference = self._reference(settings["tts_voice"])
            if settings["tts_mode"] == "real":
                self._tts_real(turn, text, family, language, reference)
            else:
                self._tts_buffered(turn, text, family, language, reference)

    def _next_tts(self, turn: int) -> tuple[str | None, str | None]:
        item = self.tts_queue.get()
        if item is None:
            raise RuntimeError(f"TTS turn {turn} ended without its marker")
        op, item_turn, text = item
        if item_turn != turn:
            raise RuntimeError(f"TTS turn {item_turn} interleaved with turn {turn}")
        if op == "unit":
            return text, None
        if op == "end":
            return None, text
        raise RuntimeError(f"unexpected TTS queue operation: {op}")

    def _tts_real(self, turn: int, first: str, family: dict, language: str, reference: Path) -> None:
        final_answer = ""
        spoken = ""

        def native_chunks():
            nonlocal final_answer, spoken
            unit = first
            index = 0
            while unit is not None:
                index += 1
                spoken = (spoken.rstrip() + " " + unit).strip()
                self.progress = highlighted_progress(spoken, 0, len(spoken))
                output = self.paths.run_dir / f"tts-turn-{turn:04d}-{index:03d}.wav"
                yield from stream_synthesize(unit, reference, output, language, family, self.paths)
                unit, completed = self._next_tts(turn)
                if completed is not None:
                    final_answer = completed

        self.progress = highlighted_progress(first, 0, len(first))
        self._state(f"TTS {turn} · native stream")
        chunks = pcm16_lookahead(native_chunks(), TTS_RATE, float(LIVE_AUDIO["tts_gradio_min_seconds"]))
        for raw in chunks:
            self.status = f"TTS {turn} · native stream · one ahead"
            self._emit("audio-pcm", raw)
        self.progress = highlighted_progress(final_answer, len(final_answer), len(final_answer))
        self._state(f"TTS {turn} · complete")

    def _tts_buffered(self, turn: int, first: str, family: dict, language: str, reference: Path) -> None:
        base = tts_endpoint(reference, language, family, self.paths)
        pending_path = None
        pending_end = 0
        spoken = ""
        final_answer = ""
        unit = first
        index = 0
        self._state(f"TTS {turn} · buffered")
        while unit is not None:
            index += 1
            spoken = (spoken.rstrip() + " " + unit).strip()
            end = len(spoken)
            path = self.paths.run_dir / f"tts-turn-{turn:04d}-{index:03d}.wav"
            result = chatterbox_synthesize(base, unit, path, False, family["TTS_STREAM"]["join"])
            if not path.is_file():
                raise RuntimeError(f"Chatterbox did not create {path}: {result}")
            if pending_path is not None:
                self.status = f"TTS {turn} · buffered · one ahead"
                self.progress = highlighted_progress(spoken, pending_end, end)
                self._emit("audio-file", str(pending_path))
            pending_path, pending_end = path, end
            unit, completed = self._next_tts(turn)
            if completed is not None:
                final_answer = completed
        if pending_path is not None:
            self._emit("audio-file", str(pending_path))
        self.progress = highlighted_progress(final_answer, len(final_answer), len(final_answer))
        self._state(f"TTS {turn} · complete")

    def stop(self) -> None:
        if not self.active:
            return
        self.active = False
        self.asr_queue.put(("finish", None))
        self.asr_thread.join()
        self._dispatch("STOP")
        self.llm_queue.put(None)
        self.llm_thread.join()
        self.tts_queue.put(None)
        self.tts_thread.join()
        write_meta(
            self.paths,
            command="conversation",
            transcript=self.paths.transcript,
            turns=self.turn,
            eou_trigger=int(self.settings["eou_trigger"]),
            vad_trigger=int(self.settings["vad_trigger"]),
            char_trigger=self.settings["char_trigger"],
            tts_mode=self.settings["tts_mode"],
        )
        self.paths.transcript.write_text(self.transcript + ("\n" if self.transcript else ""), encoding="utf-8")
        finish(self.paths)
        self._state("Stopped")
        self._emit("closed")

    def close(self) -> None:
        if self.active:
            self.stop()
        elif self.asr:
            self.asr.close()

    def next_output(self):
        return self.output_queue.get()
