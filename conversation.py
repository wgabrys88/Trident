from __future__ import annotations

import queue
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from config import ASR_RATE, ECHO_RING_MS, FAMILIES, TTS_RATE, resolve_voice
from local_api import ChatterboxClient, gemma_chat_stream
from log import clear_run_log, note, set_run_log
from main import finish, gemma_kwargs, prepared_reference, render_system_prompt, resolved_tts, spoken_reply, start_run, transcribe_wav, tts_endpoint, write_meta
from resident import require_alive
from ui_streaming import SpeechSegmenter
from vad import SileroEndpoint


@dataclass
class Event:
    kind: str
    payload: object = None
    epoch: int = 0


@dataclass
class _Piece:
    epoch: int
    text: str


class Capture:
    def __init__(
        self,
        on_utterance: Callable[[str, str], None],
        on_epoch: Callable[[str], None],
        paths,
        asr_base: str,
        settings: dict,
    ) -> None:
        self._on_utterance = on_utterance
        self._on_epoch = on_epoch
        self._paths = paths
        self._asr_base = asr_base
        self._settings = dict(settings)
        self._asr_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._vad: SileroEndpoint | None = None
        self._thread: threading.Thread | None = None
        self._active = False
        self._turn_wave: wave.Wave_write | None = None
        self._turn_path: Path | None = None
        self._turn_index = 0
        self._echo_ring = np.zeros(0, dtype=np.int16)
        self._echo_lock = threading.Lock()

    def open(self) -> None:
        if self._active:
            raise RuntimeError("capture is already active")
        self._vad = SileroEndpoint(self._settings["vad_threshold"], self._settings["vad_silence_ms"])
        self._thread = threading.Thread(target=self._asr_loop, name="trident-capture", daemon=True)
        self._thread.start()
        self._active = True
        note("component=capture event=open")

    def feed(self, pcm_f32: bytes, src_rate: int = ASR_RATE) -> None:
        if not self._active or not pcm_f32:
            return
        self._asr_queue.put(("feed", (pcm_f32, src_rate)))

    def play_pcm(self, pcm16: bytes, src_rate: int = TTS_RATE) -> None:
        if not pcm16:
            return
        samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32)
        if src_rate != ASR_RATE and samples.size:
            count = max(1, round(samples.size * ASR_RATE / src_rate))
            samples = np.interp(np.linspace(0, samples.size - 1, count), np.arange(samples.size), samples)
        pcm = np.clip(samples, -32768.0, 32767.0).astype("<i2")
        with self._echo_lock:
            target = max(1, int(ASR_RATE * ECHO_RING_MS / 1000))
            new = np.concatenate((self._echo_ring, pcm)) if self._echo_ring.size else pcm
            if new.size > target:
                new = new[-target:]
            self._echo_ring = new

    def _echo_score(self, pcm_f32: bytes) -> tuple[float, float]:
        if not pcm_f32:
            return 0.0, 0.0
        with self._echo_lock:
            ring = self._echo_ring
        mic = np.frombuffer(pcm_f32, dtype="<f4")
        if mic.size == 0:
            return 0.0, 0.0
        window = min(mic.size, int(ASR_RATE * 0.2))
        if window < 160 or ring.size < window:
            return 0.0, 0.0
        mic_window = mic[-window:]
        mic_centered = mic_window - mic_window.mean()
        mic_std = float(np.sqrt((mic_centered * mic_centered).mean()))
        if mic_std < 1e-4:
            return 0.0, 0.0
        reference = ring.astype(np.float32)
        sums = np.concatenate(([0.0], np.cumsum(reference, dtype=np.float64)))
        squares = np.concatenate(([0.0], np.cumsum(reference * reference, dtype=np.float64)))
        window_sums = sums[window:] - sums[:-window]
        energy = squares[window:] - squares[:-window] - window_sums * window_sums / window
        dots = np.correlate(reference, mic_centered, mode="valid")
        denominator = np.sqrt(np.maximum(energy, 0.0)) * np.sqrt((mic_centered * mic_centered).sum())
        correlations = np.divide(dots, denominator, out=np.zeros_like(dots), where=denominator > 0)
        index = int(np.argmax(correlations))
        corr = float(correlations[index])
        ring_std = float(np.sqrt(max(energy[index], 0.0) / window))
        return corr, ring_std

    def _vad_should_suppress(self, pcm_f32: bytes) -> bool:
        corr, ring_std = self._echo_score(pcm_f32)
        if corr >= 0.7:
            note(f"component=capture event=echo_suppress corr={corr:.3f} ring_std={ring_std:.1f}")
            return True
        return False

    def _resample(self, pcm_f32: bytes, src_rate: int) -> bytes:
        samples = np.frombuffer(pcm_f32, dtype="<f4")
        if src_rate != ASR_RATE and samples.size:
            count = max(1, round(samples.size * ASR_RATE / src_rate))
            samples = np.interp(np.linspace(0, samples.size - 1, count), np.arange(samples.size), samples).astype(np.float32)
        return samples.astype("<f4", copy=False).tobytes()

    def _open_turn(self) -> None:
        if self._turn_wave is not None:
            return
        self._turn_index += 1
        path = self._paths.run_dir / f".turn-{self._turn_index:04d}.wav"
        wave_file = wave.open(str(path), "wb")
        wave_file.setnchannels(1)
        wave_file.setsampwidth(2)
        wave_file.setframerate(ASR_RATE)
        self._turn_wave = wave_file
        self._turn_path = path

    def _write_turn(self, pcm_f32: bytes) -> None:
        if self._turn_wave is None or not pcm_f32:
            return
        audio = np.frombuffer(pcm_f32, dtype="<f4")
        pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        self._turn_wave.writeframesraw(pcm16)

    def _close_turn(self) -> Path | None:
        if self._turn_wave is None:
            return None
        self._turn_wave.close()
        path = self._turn_path
        self._turn_wave = None
        self._turn_path = None
        return path

    def _discard_turn(self) -> None:
        path = self._close_turn()
        if path:
            path.unlink(missing_ok=True)

    def _transcribe_turn(self, reason: str) -> None:
        path = self._close_turn()
        if not path:
            return
        try:
            text = transcribe_wav(path, self._asr_base, self._paths.run_dir / ".asr-chunks")
        finally:
            path.unlink(missing_ok=True)
        note(f"component=capture event=vad_end reason={reason} chars={len(text or '')}")
        if not text:
            return
        self._on_utterance(reason, text)

    def _asr_loop(self) -> None:
        set_run_log(self._paths.log)
        try:
            while True:
                op, payload = self._asr_queue.get()
                if op == "feed":
                    native, src_rate = payload
                    pcm = self._resample(native, src_rate)
                    started, ended = self._vad.feed(pcm)
                    if started:
                        if self._vad_should_suppress(pcm):
                            self._vad.reset()
                        else:
                            self._on_epoch("vad-start")
                            self._open_turn()
                    if self._vad.speech:
                        self._write_turn(pcm)
                    if ended:
                        self._transcribe_turn("VAD")
                elif op == "finish":
                    if self._vad is not None and self._vad.speech:
                        self._transcribe_turn("STOP")
                    else:
                        self._discard_turn()
                    return
                else:
                    raise RuntimeError(f"unknown capture queue operation: {op}")
        except Exception as exc:
            note(f"component=capture event=loop_exception type={type(exc).__name__} message={exc}")
            raise
        finally:
            clear_run_log(self._paths.log)

    def close(self) -> None:
        if not self._active:
            return
        note("component=capture event=close active=1")
        self._active = False
        self._asr_queue.put(("finish", None))
        if self._thread is not None:
            self._thread.join()


class Conversation:
    def __init__(self, models_dir: Path, data_dir: Path, settings: dict, paths=None) -> None:
        self.models_dir = models_dir
        self.data_dir = data_dir
        self.settings = dict(settings)
        self.paths = paths
        self.owns_run = paths is None
        self.parakeet = None
        self.gemma_base = None
        self.tts_base = None
        self.references: dict[str, Path] = {}
        self.audio_epoch = 0
        self.transcript = ""
        self.answer = ""
        self.status = "Stopped"
        self.history: list[dict] = []
        self.failure: BaseException | None = None
        self.turn = 0
        self._output_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._llm_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._tts_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._capture: Capture | None = None
        self._llm_thread: threading.Thread | None = None
        self._tts_thread: threading.Thread | None = None
        self._active = False
        self._epoch_lock = threading.Lock()

    def _family(self) -> dict:
        return FAMILIES["nano"]

    def _reference(self, voice: str) -> Path:
        source = resolve_voice(self.data_dir, voice).resolve()
        key = str(source)
        if key not in self.references:
            self.references[key] = prepared_reference(source, self.data_dir)
        return self.references[key]

    def _emit(self, kind: str, payload=None, epoch: int | None = None) -> None:
        self._output_queue.put(Event(kind, payload, self.audio_epoch if epoch is None else epoch))

    def epoch(self) -> int:
        with self._epoch_lock:
            return self.audio_epoch

    def _bump_epoch(self, reason: str) -> None:
        with self._epoch_lock:
            self.audio_epoch += 1
            epoch = self.audio_epoch
        note(
            f"component=conversation event=epoch_bump epoch={epoch} reason={reason}"
            f" turn={self.turn} transcript_chars={len(self.transcript)}"
        )
        self._emit("audio-reset", epoch=epoch)

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
                note(f"component=conversation event=worker_exception name={name} type={type(exc).__name__} message={exc}")
                self._emit("error", exc)
            finally:
                clear_run_log(self.paths.log)
        return threading.Thread(target=run, name=name, daemon=True)

    def start(self) -> None:
        if self._active:
            raise RuntimeError("conversation is already active")
        if self.paths is None:
            self.paths = start_run("conversation", self.models_dir, self.data_dir)
        else:
            set_run_log(self.paths.log)
        try:
            self.parakeet = require_alive("parakeet")
            self.gemma_base = require_alive("gemma")
            family = self._family()
            self.tts_base = tts_endpoint(self._reference(self.settings["tts_voice"]), "en", family, self.paths)
            self._capture = Capture(self._on_utterance, self._on_capture_epoch, self.paths, self.parakeet, self.settings)
            self._capture.open()
            self._llm_thread = self._worker(self._llm_loop, "trident-llm")
            self._tts_thread = self._worker(self._tts_loop, "trident-tts")
            self._llm_thread.start()
            self._tts_thread.start()
            self._active = True
            self._state("Listening")
        except Exception:
            if self._capture is not None:
                self._capture.close()
                self._capture = None
            if self.owns_run:
                finish(self.paths, "error")
            raise

    def _on_capture_epoch(self, reason: str) -> None:
        self._bump_epoch(reason)

    def feed_audio(self, pcm_f32: bytes) -> None:
        if not self._active or self._capture is None:
            return
        self._capture.feed(pcm_f32)

    def _on_utterance(self, reason: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self.transcript = (self.transcript.rstrip() + " " + text).strip()
        epoch = self.epoch()
        self._llm_queue.put((epoch, text, dict(self.settings)))
        note(
            f"component=conversation event=utterance epoch={epoch} reason={reason}"
            f" chars={len(text)} transcript_chars={len(self.transcript)}"
        )
        self._state(f"Dispatch {epoch} · {len(text)} chars")

    def _llm_payload(self, text: str, settings: dict) -> dict:
        system = render_system_prompt(settings["system_prompt"])
        return gemma_kwargs(
            [{"role": "system", "content": system}, *self.history, {"role": "user", "content": text}],
            stream=True,
        )

    def _tts_loop(self) -> None:
        while True:
            item = self._tts_queue.get()
            if item is None:
                return
            piece: _Piece = item
            live = self.epoch()
            if not piece.text or piece.epoch != live:
                if piece.text:
                    note(f"component=tts event=skip_stale epoch={piece.epoch} live={live}")
                continue
            client = ChatterboxClient(self.tts_base, cancel=lambda e=piece.epoch: self.epoch() != e)
            try:
                client.open()
                client.send_piece(piece.text)
                note(f"component=tts event=begin epoch={piece.epoch} chars={len(piece.text)}")
                self._drain_pieces(client, piece.epoch)
            except InterruptedError:
                note(f"component=tts event=interrupted epoch={piece.epoch} live={self.epoch()}")
            except Exception as exc:
                note(
                    f"component=tts event=failed epoch={piece.epoch}"
                    f" type={type(exc).__name__} message={exc}"
                )
            finally:
                client.cancel()

    def _drain_pieces(self, client: ChatterboxClient, epoch: int) -> None:
        for pcm in client:
            if self.epoch() != epoch:
                client.cancel()
                note(f"component=tts event=cancel epoch={epoch} live={self.epoch()}")
                return
            if not pcm:
                continue
            self._emit("audio-pcm", pcm, epoch=epoch)
            if self._capture is not None:
                self._capture.play_pcm(pcm, TTS_RATE)
        if self.epoch() == epoch:
            note(f"component=tts event=done epoch={epoch}")

    def _llm_loop(self) -> None:
        while True:
            item = self._llm_queue.get()
            if item is None:
                self._tts_queue.put(None)
                return
            epoch, prompt, settings = item
            if epoch != self.epoch():
                note(f"component=llm event=skip_stale epoch={epoch} live={self.epoch()}")
                continue
            self.answer = ""
            self._state(f"LLM {epoch} · generating")
            note(f"component=llm event=begin epoch={epoch} pending_chars={len(prompt)} transcript_chars={len(self.transcript)}")
            started = time.perf_counter()
            ttfa = None
            family = self._family()
            hard_limit = int(family["TTS_CHUNK"]["chars"])
            first_chars = min(int(family["TTS_CHUNK"]["first_chars"]), hard_limit)
            segmenter = SpeechSegmenter(first_chars, hard_limit)
            raw = ""
            turn = self.turn + 1
            self.turn = turn
            note(f"component=llm event=turn_open turn={turn} epoch={epoch}")
            self._state(f"LLM {epoch} · speech ready")
            for delta in gemma_chat_stream(self.gemma_base, self._llm_payload(prompt, settings)):
                if epoch != self.epoch():
                    break
                if ttfa is None:
                    ttfa = time.perf_counter() - started
                raw += delta
                self.answer = raw
                self._emit("state")
                for unit in segmenter.update(spoken_reply(raw, streaming=True)):
                    self._tts_queue.put(_Piece(epoch, unit.text))
            if epoch != self.epoch():
                note(f"component=llm event=cancelled epoch={epoch} turn={turn} live={self.epoch()}")
                continue
            answer = spoken_reply(raw)
            for unit in segmenter.update(answer, flush=True):
                self._tts_queue.put(_Piece(epoch, unit.text))
            if not answer:
                self._state(f"LLM {epoch} · wait")
                continue
            self.answer = answer
            self.history.extend(({"role": "user", "content": prompt}, {"role": "assistant", "content": answer}))
            note(
                f"component=llm event=complete epoch={epoch} turn={turn} ttfa_ms={(ttfa or time.perf_counter() - started) * 1000:.3f}"
                f" total_ms={(time.perf_counter() - started) * 1000:.3f} chars={len(answer)}"
            )
            self._state(f"LLM {turn} · complete")

    def stop(self) -> None:
        if not self._active:
            return
        note(
            f"component=conversation event=stop_begin turn={self.turn}"
            f" audio_epoch={self.audio_epoch}"
        )
        self._bump_epoch("stop")
        self._active = False
        if self._capture is not None:
            self._capture.close()
        self._llm_queue.put(None)
        if self._llm_thread is not None:
            self._llm_thread.join()
        if self._tts_thread is not None:
            self._tts_thread.join()
        self.paths.transcript.write_text(self.transcript + ("\n" if self.transcript else ""), encoding="utf-8")
        if self.owns_run:
            write_meta(
                self.paths,
                command="conversation",
                transcript=self.paths.transcript,
                turns=self.turn,
                family="nano",
                vad_threshold=self.settings["vad_threshold"],
                vad_silence_ms=self.settings["vad_silence_ms"],
                resolved_tts=resolved_tts(self._family()),
            )
            set_run_log(self.paths.log)
            finish(self.paths, "error" if self.failure else "ok")
        self._state("Stopped")
        self._emit("closed")
        note("component=conversation event=stop_end")

    def close(self) -> None:
        if self._active:
            self.stop()

    def next_output(self):
        return self._output_queue.get()
