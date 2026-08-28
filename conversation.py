from __future__ import annotations

import queue
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from cable import Microphone
from config import ASR_RATE, ECHO_RING_MS, LANGUAGES, TTS_RATE, resolve_voice
from local_api import ChatterboxClient
from log import clear_run_log, note, set_run_log
from main import effective_family, finish, gemma_kwargs, prepared_reference, render_system_prompt, resolved_tts, spoken_reply, start_run, stream_synthesize, transcribe_wav, tts_endpoint, write_meta
from resident import require_alive
from ui_streaming import SpeechSegmenter
from vad import SileroEndpoint


@dataclass
class Event:
    kind: str
    payload: Any = None
    epoch: int = 0


@dataclass
class _Piece:
    turn: int
    seq: int
    text: str
    is_last: bool


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
        self._mic: Microphone | None = None
        self._active = False
        self._turn_wave: wave.Wave_write | None = None
        self._turn_path: Path | None = None
        self._turn_index = 0
        self._echo_ring = np.zeros(0, dtype=np.int16)
        self._echo_lock = threading.Lock()
        self._handsfree = settings.get("ingestion_mode", "continuous") == "continuous"

    def open(self) -> None:
        if self._active:
            raise RuntimeError("capture is already active")
        self._vad = SileroEndpoint(self._settings["vad_threshold"], self._settings["vad_silence_ms"])
        self._thread = threading.Thread(target=self._asr_loop, name="trident-capture", daemon=True)
        self._thread.start()
        if self._handsfree:
            self._mic = Microphone(self._feed_audio_bytes)
            self._mic.start()
        self._active = True
        note(f"component=capture event=open handsfree={int(self._handsfree)}")

    def configure_vad(self, threshold: float, silence_ms: int) -> None:
        if not self._active:
            return
        self._asr_queue.put(("vad-config", (float(threshold), int(silence_ms))))
        self._on_epoch("vad-config")

    def submit_audio(self, pcm_f32: bytes) -> None:
        if not self._active:
            return
        if not pcm_f32:
            return
        self._on_epoch("ptt")
        self._asr_queue.put(("feed", pcm_f32))
        self._asr_queue.put(("cut", "PTT"))

    def feed_audio(self, pcm_f32: bytes) -> None:
        if not self._active:
            return
        if not self._settings.get("ingestion_mode", "continuous") == "continuous":
            return
        if pcm_f32:
            self._asr_queue.put(("feed", pcm_f32))

    def manual_text(self, text: str) -> bool:
        if not self._active:
            return False
        text = text.strip()
        if not text:
            self._asr_queue.put(("cut", "MANUAL"))
            return True
        self._on_epoch("manual")
        self._asr_queue.put(("manual", text))
        return True

    def play_pcm(self, pcm16: bytes, src_rate: int = TTS_RATE) -> None:
        if not pcm16:
            return
        try:
            samples = np.frombuffer(pcm16, dtype="<i2")
        except ValueError:
            return
        if samples.size == 0:
            return
        if src_rate != TTS_RATE and samples.size:
            count = max(1, round(samples.size * TTS_RATE / src_rate))
            samples = np.interp(np.linspace(0, samples.size - 1, count), np.arange(samples.size), samples.astype(np.float32)).astype("<i2")
        with self._echo_lock:
            target = max(1, int(TTS_RATE * ECHO_RING_MS / 1000))
            new = np.concatenate((self._echo_ring, samples)) if self._echo_ring.size else samples.astype("<i2")
            if new.size > target:
                new = new[-target:]
            self._echo_ring = new

    def _echo_score(self, pcm_f32: bytes) -> tuple[float, float]:
        if not pcm_f32:
            return 0.0, 0.0
        with self._echo_lock:
            ring = self._echo_ring
        if ring.size == 0:
            return 0.0, 0.0
        try:
            mic = np.frombuffer(pcm_f32, dtype="<f4")
        except ValueError:
            return 0.0, 0.0
        if mic.size == 0:
            return 0.0, 0.0
        window = min(mic.size, int(ASR_RATE * 0.2))
        if window < 160:
            return 0.0, 0.0
        mic_window = mic[-window:]
        if ring.size < window:
            ring_window = ring
        else:
            ring_window = ring[-window:]
        if ring_window.size != window:
            count = window
            resampled = np.interp(np.linspace(0, ring_window.size - 1, count), np.arange(ring_window.size), ring_window.astype(np.float32))
            ring_window = resampled
        else:
            ring_window = ring_window.astype(np.float32)
        mic_centered = mic_window - mic_window.mean()
        ring_centered = ring_window - ring_window.mean()
        mic_std = float(np.sqrt((mic_centered * mic_centered).mean()))
        ring_std = float(np.sqrt((ring_centered * ring_centered).mean()))
        if mic_std < 1e-4 or ring_std < 1e-4:
            return 0.0, 0.0
        corr = float((mic_centered * ring_centered).mean() / (mic_std * ring_std))
        return corr, ring_std

    def _vad_should_suppress(self, pcm_f32: bytes) -> bool:
        corr, ring_std = self._echo_score(pcm_f32)
        if ring_std < 200.0:
            return False
        if corr >= 0.7:
            note(f"component=capture event=echo_suppress corr={corr:.3f} ring_std={ring_std:.1f}")
            return True
        return False

    def _feed_audio_bytes(self, pcm_f32: bytes) -> None:
        if not pcm_f32:
            return
        if not self._settings.get("ingestion_mode", "continuous") == "continuous":
            return
        self._asr_queue.put(("feed", pcm_f32))

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
        if self._turn_wave is None:
            return
        if not pcm_f32:
            return
        try:
            audio = np.frombuffer(pcm_f32, dtype="<f4")
        except ValueError:
            return
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

    def submit_audio(self, pcm_f32: bytes) -> None:
        if not self._active:
            return
        if not pcm_f32:
            return
        self._on_epoch("ptt")
        self._asr_queue.put(("feed", pcm_f32))
        self._asr_queue.put(("cut", "PTT"))

    def _asr_loop(self) -> None:
        set_run_log(self._paths.log)
        try:
            while True:
                op, payload = self._asr_queue.get()
                if op == "feed":
                    pcm = payload
                    if self._settings.get("ingestion_mode", "continuous") == "continuous" and self._vad is not None:
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
                    else:
                        if self._turn_wave is None:
                            self._open_turn()
                        self._write_turn(pcm)
                elif op == "cut":
                    self._transcribe_turn(payload)
                elif op == "manual":
                    self._on_utterance("MANUAL", payload)
                elif op == "vad-config":
                    threshold, silence_ms = payload
                    if self._vad is not None:
                        self._vad.configure(threshold, silence_ms)
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
        note(f"component=capture event=close active=1")
        self._active = False
        if self._mic is not None:
            self._mic.stop()
        self._asr_queue.put(("finish", None))
        if self._thread is not None:
            self._thread.join()


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
        self.references: dict[str, Path] = {}
        self.brain_seq = 0
        self.audio_epoch = 0
        self.transcript = ""
        self.answer = ""
        self.status = "Stopped"
        self.history: list[dict] = []
        self.failure: BaseException | None = None
        self.turn = 0
        self.tts_started_through = 0
        self.tts_done_through = 0
        self._output_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._llm_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._tts_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._capture: Capture | None = None
        self._llm_thread: threading.Thread | None = None
        self._tts_thread: threading.Thread | None = None
        self._active = False
        self._epoch_callbacks: list[Callable[[int], None]] = []
        self._epoch_lock = threading.Lock()

    def _family(self) -> dict:
        return effective_family(self.settings["tts_family"])

    def _require_language(self) -> None:
        family = self._family()
        language = self.settings["tts_language"]
        if language not in family["TTS_LANGUAGES"]:
            raise RuntimeError(f"language {language!r} is not wired in {family['name']}")

    def _require_engine(self) -> None:
        if self.failure:
            raise RuntimeError(str(self.failure))
        if not self._active:
            raise RuntimeError("conversation is not active")

    def _reference(self, voice: str) -> Path:
        source = resolve_voice(self.data_dir, voice).resolve()
        key = str(source)
        if key not in self.references:
            self.references[key] = prepared_reference(source, self.data_dir)
        return self.references[key]

    def _emit(self, kind: str, payload=None) -> None:
        if self.output_audio or not kind.startswith("audio-"):
            self._output_queue.put(Event(kind, payload, self.audio_epoch))

    def epoch(self) -> int:
        with self._epoch_lock:
            return self.audio_epoch

    def _bump_epoch(self, reason: str) -> None:
        with self._epoch_lock:
            self.audio_epoch += 1
            epoch = self.audio_epoch
        note(
            f"component=conversation event=epoch_bump epoch={epoch} reason={reason}"
            f" turn={self.turn} tts_started_through={self.tts_started_through}"
            f" tts_done_through={self.tts_done_through} transcript_chars={len(self.transcript)}"
        )
        for callback in list(self._epoch_callbacks):
            try:
                callback(epoch)
            except Exception as exc:
                note(f"component=conversation event=epoch_callback_error type={type(exc).__name__} message={exc}")

    def _interrupt(self, reason: str = "interrupt") -> None:
        seq_before = self.brain_seq
        self.brain_seq += 1
        self._bump_epoch(reason)
        note(
            f"component=conversation event=interrupt seq_before={seq_before} seq_after={self.brain_seq}"
            f" turn={self.turn} tts_started_through={self.tts_started_through}"
            f" tts_done_through={self.tts_done_through} transcript_chars={len(self.transcript)}"
        )

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
            self._require_language()
            self._capture = Capture(self._on_utterance, self._on_capture_epoch, self.paths, self.parakeet, self.settings)
            family = self._family()
            tts_endpoint(self._reference(self.settings["tts_voice"]), self.settings["tts_language"], family, self.paths)
            self._capture.open()
            self._llm_thread = self._worker(self._llm_loop, "trident-llm")
            self._tts_thread = self._worker(self._tts_loop, "trident-tts")
            self._llm_thread.start()
            self._tts_thread.start()
            self._active = True
            self._state("Listening · Parakeet ASR · Gemma · TTS resident")
        except Exception:
            if self.owns_run:
                finish(self.paths, "error")
            raise

    def _on_capture_epoch(self, reason: str) -> None:
        self._bump_epoch(reason)

    def configure(self, settings: dict) -> None:
        self._require_language()
        vad_changed = (
            settings["vad_threshold"] != self.settings["vad_threshold"]
            or settings["vad_silence_ms"] != self.settings["vad_silence_ms"]
        )
        self.settings = dict(settings)
        if vad_changed and self._capture is not None:
            self._capture.configure_vad(self.settings["vad_threshold"], self.settings["vad_silence_ms"])
        note(
            f"component=conversation event=configure ingestion={settings['ingestion_mode']}"
            f" tts_family={settings['tts_family']}"
            f" tts_language={settings['tts_language']} vad_threshold={settings['vad_threshold']}"
            f" vad_silence_ms={settings['vad_silence_ms']}"
        )
        self._state("Configuration applied")

    def submit(self, text: str) -> None:
        self._require_engine()
        if self._capture is None:
            raise RuntimeError("conversation is not active")
        if not self._capture.manual_text(text):
            return
        self._state(f"Dispatch {self.brain_seq} · MANUAL · {len(text)} chars")

    def submit_audio(self, pcm_f32: bytes) -> None:
        self._require_engine()
        if self._capture is None:
            raise RuntimeError("conversation is not active")
        if not pcm_f32:
            self._state("Push-to-talk · no audio")
            return
        self._capture.submit_audio(pcm_f32)
        self._state("Push-to-talk · finalizing")

    def feed_audio(self, pcm_f32: bytes) -> None:
        self._require_engine()
        if self._capture is None:
            return
        self._capture.feed_audio(pcm_f32)

    def _on_utterance(self, reason: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self.transcript = (self.transcript.rstrip() + " " + text).strip()
        self.brain_seq += 1
        seq = self.brain_seq
        self._llm_queue.put((seq, text, dict(self.settings)))
        note(
            f"component=conversation event=utterance seq={seq} reason={reason}"
            f" chars={len(text)} transcript_chars={len(self.transcript)}"
        )
        self._state(f"Dispatch {seq} · {reason} · {len(text)} chars")

    def _llm_payload(self, text: str, settings: dict) -> dict:
        language = settings["tts_language"]
        system = render_system_prompt(settings["system_prompt"], language, LANGUAGES[language])
        return gemma_kwargs(
            [{"role": "system", "content": system}, *self.history, {"role": "user", "content": text}],
            stream=True,
        )

    def _tts_loop(self) -> None:
        client: ChatterboxClient | None = None
        turn_active = 0
        epoch_active = 0
        while True:
            item = self._tts_queue.get()
            if item is None:
                if client is not None:
                    self._close_client(client)
                return
            piece: _Piece = item
            if client is None and not piece.is_last and piece.text and piece.turn >= 1:
                family = self._family()
                language = self.settings["tts_language"]
                reference = self._reference(self.settings["tts_voice"])
                base = tts_endpoint(reference, language, family, self.paths)
                wav_path = self.paths.run_dir / f"tts-turn-{piece.turn:04d}.wav"
                epoch_active = self.epoch()
                try:
                    client = ChatterboxClient(base, cancel=lambda: self.epoch() != epoch_active)
                    client.open(wav_path)
                    client.send_piece(piece.text)
                    self._emit("audio-reset")
                except Exception as exc:
                    note(
                        f"component=tts event=open_failed turn={piece.turn} seq={piece.seq}"
                        f" type={type(exc).__name__} message={exc}"
                    )
                    self._close_client(client) if client is not None else None
                    client = None
                    continue
                turn_active = piece.turn
                self._drain_pieces(client, piece.turn, piece.seq, epoch_active)
                continue
            if client is not None and not piece.is_last and piece.text and piece.turn == turn_active:
                try:
                    client.send_piece(piece.text)
                except Exception as exc:
                    note(
                        f"component=tts event=send_failed turn={piece.turn} seq={piece.seq}"
                        f" type={type(exc).__name__} message={exc}"
                    )
                    self._close_client(client)
                    client = None
                    continue
                self._drain_pieces(client, piece.turn, piece.seq, epoch_active)
                continue
            if piece.is_last or (client is not None and piece.turn != turn_active):
                self._close_client(client)
                client = None

    def _drain_pieces(self, client: ChatterboxClient, turn: int, seq: int, epoch_active: int) -> None:
        cancelled = False
        try:
            for pcm in client:
                if self.epoch() != epoch_active:
                    client.cancel_piece()
                    cancelled = True
                    break
                if not pcm:
                    continue
                if self.tts_started_through < turn:
                    self.tts_started_through = turn
                    note(
                        f"component=tts event=started_through turn={turn} seq={seq}"
                        f" bytes={len(pcm)} tts_done_through={self.tts_done_through}"
                    )
                if self._capture is not None:
                    self._capture.play_pcm(pcm, TTS_RATE)
                self._emit("audio-pcm", pcm)
            if not cancelled and self.epoch() == epoch_active:
                self.tts_done_through = max(self.tts_done_through, turn)
                note(
                    f"component=tts event=done_through turn={turn} seq={seq}"
                    f" tts_started_through={self.tts_started_through} tts_done_through={self.tts_done_through}"
                )
        except Exception as exc:
            note(
                f"component=tts event=drain_failed turn={turn} seq={seq}"
                f" type={type(exc).__name__} message={exc}"
            )

    def _close_client(self, client: ChatterboxClient) -> None:
        try:
            client.end()
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass

    def _llm_loop(self) -> None:
        from local_api import gemma_chat_stream
        while True:
            item = self._llm_queue.get()
            if item is None:
                self._tts_queue.put(None)
                return
            seq, prompt, settings = item
            if seq != self.brain_seq:
                continue
            self._require_language()
            self.answer = ""
            self._state(f"LLM {seq} · generating")
            note(f"component=llm event=begin seq={seq} pending_chars={len(prompt)} transcript_chars={len(self.transcript)}")
            started = time.perf_counter()
            ttfa = None
            family = self._family()
            hard_limit = int(family["TTS_CHUNK"]["chars"])
            first_chars = min(int(family["TTS_CHUNK"]["first_chars"]), hard_limit)
            segmenter = SpeechSegmenter(first_chars, hard_limit)
            raw = ""
            turn = self.turn + 1
            epoch_at_dispatch = self.audio_epoch
            self.turn = turn
            note(f"component=llm event=turn_open turn={turn} seq={seq}")
            self._state(f"LLM {seq} · speech ready")
            for delta in gemma_chat_stream(self.gemma_base, self._llm_payload(prompt, settings)):
                if seq != self.brain_seq:
                    break
                if ttfa is None:
                    ttfa = time.perf_counter() - started
                raw += delta
                self.answer = raw
                if epoch_at_dispatch != self.audio_epoch:
                    break
                for unit in segmenter.update(spoken_reply(raw, streaming=True)):
                    self._tts_queue.put(_Piece(turn, seq, unit.text, False))
            if seq != self.brain_seq:
                note(f"component=llm event=cancelled seq={seq} turn={turn}")
                self._tts_queue.put(_Piece(turn, seq, "", True))
                continue
            answer = spoken_reply(raw)
            for unit in segmenter.update(answer, flush=True):
                self._tts_queue.put(_Piece(turn, seq, unit.text, False))
            if not answer:
                self._state(f"LLM {seq} · wait")
                continue
            self.answer = answer
            self.history.extend(({"role": "user", "content": prompt}, {"role": "assistant", "content": answer}))
            self._tts_queue.put(_Piece(turn, seq, "", True))
            note(
                f"component=llm event=complete seq={seq} turn={turn} ttfa_ms={(ttfa or time.perf_counter() - started) * 1000:.3f}"
                f" total_ms={(time.perf_counter() - started) * 1000:.3f} chars={len(answer)}"
            )
            self._state(f"LLM {turn} · complete")

    def stop(self) -> None:
        if not self._active:
            return
        note(
            f"component=conversation event=stop_begin turn={self.turn} brain_seq={self.brain_seq}"
            f" tts_started_through={self.tts_started_through} tts_done_through={self.tts_done_through}"
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
            final_family = self._family()
            write_meta(
                self.paths,
                command="conversation",
                transcript=self.paths.transcript,
                turns=self.turn,
                reply_owner="brain",
                vad_threshold=self.settings["vad_threshold"],
                vad_silence_ms=self.settings["vad_silence_ms"],
                tts_language=self.settings["tts_language"],
                resolved_tts=resolved_tts(final_family),
            )
            set_run_log(self.paths.log)
            finish(self.paths, "error" if self.failure else "ok")
        self._state("Stopped")
        self._emit("closed")
        note("component=conversation event=stop_end")

    def close(self) -> None:
        note(
            f"component=conversation event=close_begin active={int(self._active)} turn={self.turn}"
            f" tts_started_through={self.tts_started_through} tts_done_through={self.tts_done_through}"
            f" brain_seq={self.brain_seq} audio_epoch={self.audio_epoch}"
        )
        try:
            if self._active:
                self.stop()
        except Exception as exc:
            note(f"component=conversation event=close_exception type={type(exc).__name__} message={exc}")
            raise
        finally:
            note(
                f"component=conversation event=close_end active={int(self._active)}"
                f" failure={type(self.failure).__name__ if self.failure else 'none'}"
            )

    def next_output(self):
        return self._output_queue.get()
