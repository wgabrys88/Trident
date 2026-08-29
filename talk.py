from __future__ import annotations

import io
import queue
import threading
import time
import wave
from pathlib import Path

import gradio as gr
import numpy as np
from silero_vad_notorch import VADIterator, load_silero_vad

from config import (
    ASR_RATE, FEED_S, MIC_LIMIT_S, Paths, TTS_KNOBS, TTS_RATE, VAD_FRAME,
    load_settings, log, voice_wav,
)
from runtime import Chatterbox, gemma_stream, require_alive, transcribe

MIN_TURN_S = 1.0


def linear_resample(samples: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst or samples.size == 0:
        return samples
    n = max(1, round(samples.size * dst / src))
    return np.interp(np.linspace(0, samples.size - 1, n), np.arange(samples.size), samples).astype(np.float32)


def spoken(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if "\nAssistant:\n" in text:
        text = text.rsplit("\nAssistant:\n", 1)[1].strip()
    elif text.startswith("Assistant:\n"):
        text = text[11:].strip()
    return text


def pcm_to_wav(pcm: bytes, rate: int) -> bytes:
    if not pcm:
        return b""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buf.getvalue()


class VAD:
    def __init__(self, threshold: float, silence_ms: int) -> None:
        self.iterator = VADIterator(load_silero_vad(onnx=True), threshold=float(threshold), sampling_rate=ASR_RATE, min_silence_duration_ms=int(silence_ms), speech_pad_ms=0)
        self.buffer = np.empty(0, dtype=np.float32)
        self.speech = False

    def feed(self, pcm: bytes) -> tuple[bool, bool]:
        if not pcm:
            return False, False
        self.buffer = np.concatenate((self.buffer, np.frombuffer(pcm, dtype="<f4")))
        started = ended = False
        while self.buffer.size >= VAD_FRAME:
            event = self.iterator(self.buffer[:VAD_FRAME])
            self.buffer = self.buffer[VAD_FRAME:]
            if "start" in event:
                self.speech, started = True, True
            if "end" in event:
                self.speech, ended = False, True
        return started, ended

    def reset(self) -> None:
        self.iterator.reset_states()
        self.buffer = np.empty(0, dtype=np.float32)
        self.speech = False


class Segmenter:
    def __init__(self, minimum: int, hard: int) -> None:
        self.minimum, self.hard, self.sent = minimum, hard, 0

    def update(self, text: str, flush: bool = False) -> list[str]:
        out = []
        while self.sent < len(text):
            pending = text[self.sent:]
            cut = 0
            for i in range(self.minimum - 1, min(len(pending), self.hard)):
                if pending[i] in ".?!" and (i + 1 == len(pending) or pending[i + 1].isspace()):
                    cut = i + 1
                    break
            if not cut and len(pending) >= self.hard:
                split = max(pending.rfind(" ", self.minimum, self.hard), pending.rfind("\n", self.minimum, self.hard), pending.rfind("\t", self.minimum, self.hard))
                cut = split + 1 if split >= self.minimum else self.hard
            if not cut and flush:
                cut = len(pending)
            if not cut:
                break
            unit = pending[:cut].strip()
            self.sent += cut
            while self.sent < len(text) and text[self.sent].isspace():
                self.sent += 1
            if unit:
                out.append(unit)
        return out


class Capture:
    def __init__(self, on_text, on_epoch, run_dir: Path, asr: str, settings: dict) -> None:
        self.on_text, self.on_epoch = on_text, on_epoch
        self.run_dir, self.asr, self.settings = run_dir, asr, settings
        self.q: queue.SimpleQueue = queue.SimpleQueue()
        self.vad: VAD | None = None
        self.thread: threading.Thread | None = None
        self.active = False
        self.wav: wave.Wave_write | None = None
        self.path: Path | None = None
        self.index = 0
        self.duration_s = 0.0

    def open(self) -> None:
        self.vad = VAD(self.settings["vad_threshold"], self.settings["vad_silence_ms"])
        self.thread = threading.Thread(target=self._loop, name="capture", daemon=True)
        self.thread.start()
        self.active = True
        log("capture open")

    def feed(self, pcm: bytes) -> None:
        if self.active and pcm:
            self.q.put(pcm)

    def _open_turn(self) -> None:
        self.index += 1
        self.duration_s = 0.0
        self.path = self.run_dir / f".turn-{self.index:04d}.wav"
        self.wav = wave.open(str(self.path), "wb")
        self.wav.setnchannels(1)
        self.wav.setsampwidth(2)
        self.wav.setframerate(ASR_RATE)

    def _write(self, pcm: bytes) -> None:
        if self.wav is None or not pcm:
            return
        audio = np.frombuffer(pcm, dtype="<f4")
        self.wav.writeframesraw((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
        self.duration_s += len(pcm) / (ASR_RATE * 4)

    def _close(self) -> Path | None:
        if self.wav is None:
            return None
        self.wav.close()
        path, self.wav, self.path = self.path, None, None
        return path

    def _asr(self, reason: str) -> None:
        path = self._close()
        if path is None:
            return
        if self.duration_s < MIN_TURN_S:
            path.unlink()
            return
        started = time.perf_counter()
        try:
            text = transcribe(self.asr, path)
        finally:
            path.unlink()
        elapsed = time.perf_counter() - started
        rtf = elapsed / self.duration_s
        log(f"asr {reason} {self.duration_s:.2f}s x{1 / rtf if rtf else 0:.2f} {text!r}")
        if text:
            self.on_text(text)

    def _loop(self) -> None:
        while True:
            pcm = self.q.get()
            if pcm is None:
                if self.vad and self.vad.speech:
                    self._asr("stop")
                else:
                    path = self._close()
                    if path:
                        path.unlink(missing_ok=True)
                return
            assert self.vad is not None
            started, ended = self.vad.feed(pcm)
            if started:
                self.on_epoch()
                self._open_turn()
            if self.vad.speech:
                self._write(pcm)
            if ended:
                self._asr("vad")

    def close(self) -> None:
        if not self.active:
            return
        self.active = False
        self.q.put(None)
        if self.thread:
            self.thread.join()
        log("capture close")


class Conversation:
    def __init__(self, paths: Paths, settings: dict) -> None:
        self.paths, self.settings = paths, dict(settings)
        self.parakeet = require_alive("parakeet")
        self.gemma = require_alive("gemma")
        self.tts_url = require_alive("chatterbox")
        self.epoch = 0
        self.turn = 0
        self.transcript = ""
        self.answer = ""
        self.status = "Stopped"
        self.history: list[dict[str, str]] = []
        self.out: queue.SimpleQueue = queue.SimpleQueue()
        self.llm_q: queue.SimpleQueue = queue.SimpleQueue()
        self.tts_q: queue.SimpleQueue = queue.SimpleQueue()
        self.capture: Capture | None = None
        self.llm_t = self.tts_t = self.pcm_t = None
        self.active = False
        self.lock = threading.Lock()
        self.tts_client: Chatterbox | None = None

    def _bump(self, reason: str) -> int:
        with self.lock:
            self.epoch += 1
            epoch = self.epoch
        log(f"barge-in epoch={epoch} reason={reason} turn={self.turn}")
        if self.tts_client is not None:
            self.tts_client.send(epoch, [])
        self.out.put(("audio-reset", None, epoch))
        return epoch

    def _state(self, status: str | None = None) -> None:
        if status is not None:
            self.status = status
        self.out.put(("state", None, self.epoch))

    def start(self) -> None:
        self.tts_client = Chatterbox(self.tts_url)
        self.tts_client.open()
        self.pcm_t = threading.Thread(target=self._pcm, name="pcm", daemon=True)
        self.pcm_t.start()
        self.capture = Capture(self._utterance, lambda: self._bump("vad-start"), self.paths.run_dir, self.parakeet, self.settings)
        self.capture.open()
        self.llm_t = threading.Thread(target=self._llm, name="llm", daemon=True)
        self.tts_t = threading.Thread(target=self._tts, name="tts", daemon=True)
        self.llm_t.start()
        self.tts_t.start()
        self.active = True
        self._state("Listening")

    def feed(self, pcm: bytes) -> None:
        if self.active and self.capture:
            self.capture.feed(pcm)

    def _utterance(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self.transcript = (self.transcript + " " + text).strip()
        with self.lock:
            epoch = self.epoch
        log(f"user epoch={epoch} {text!r}")
        self.llm_q.put((epoch, text))
        self._state(f"Heard {len(text)} chars")

    def _llm(self) -> None:
        while True:
            item = self.llm_q.get()
            if item is None:
                self.tts_q.put(None)
                return
            epoch, prompt = item
            with self.lock:
                live = self.epoch
            if epoch != live:
                continue
            self.turn += 1
            turn = self.turn
            log(f"llm begin turn={turn} epoch={epoch} {prompt!r}")
            self._state("Thinking")
            t0 = time.perf_counter()
            ttfa: float | None = None
            seg = Segmenter(min(TTS_KNOBS["first_chars"], TTS_KNOBS["chars"]), TTS_KNOBS["chars"])
            raw = ""
            messages = [{"role": "system", "content": self.settings["system_prompt"].strip()}, *self.history, {"role": "user", "content": prompt}]
            gen = gemma_stream(self.gemma, messages)
            try:
                for delta in gen:
                    with self.lock:
                        stale = epoch != self.epoch
                    if stale:
                        break
                    if ttfa is None:
                        ttfa = time.perf_counter() - t0
                    raw += delta
                    self.answer = raw
                    for unit in seg.update(spoken(raw)):
                        self.tts_q.put((epoch, unit))
            finally:
                gen.close()
            with self.lock:
                if epoch != self.epoch:
                    continue
            answer = spoken(raw)
            for unit in seg.update(answer, flush=True):
                self.tts_q.put((epoch, unit))
            if not answer:
                self._state("Listening")
                continue
            self.answer = answer
            self.history.extend(({"role": "user", "content": prompt}, {"role": "assistant", "content": answer}))
            log(f"llm done turn={turn} ttfa_ms={(ttfa or 0) * 1000:.0f} total_ms={(time.perf_counter() - t0) * 1000:.0f} {answer!r}")
            self._state("Listening")

    def _tts(self) -> None:
        assert self.tts_client is not None
        while True:
            item = self.tts_q.get()
            if item is None:
                return
            epoch, text = item
            with self.lock:
                live = self.epoch
            if not text or epoch != live:
                continue
            pieces = [text]
            while True:
                try:
                    more = self.tts_q.get_nowait()
                except queue.Empty:
                    break
                if more is None:
                    self.tts_q.put(None)
                    break
                more_epoch, more_text = more
                if more_epoch == epoch and more_text:
                    pieces.append(more_text)
            with self.lock:
                if epoch != self.epoch:
                    continue
            self.tts_client.send(epoch, pieces)
            log(f"tts send epoch={epoch} n={len(pieces)} {pieces[0]!r}")

    def _pcm(self) -> None:
        assert self.tts_client is not None
        while True:
            frame = self.tts_client.recv_frame()
            if frame is None:
                return
            kind, epoch, payload = frame
            if kind == 2:
                if payload and epoch == self.epoch:
                    self.out.put(("audio-pcm", payload, epoch))
            elif kind == 0:
                log(f"tts batch done epoch={epoch}")
            elif kind == 1:
                raise RuntimeError("TTS: " + payload.decode("utf-8", "replace"))
            else:
                raise RuntimeError(f"TTS frame {kind}")

    def stop(self) -> None:
        if not self.active:
            return
        log(f"stop begin turn={self.turn} epoch={self.epoch}")
        self._bump("stop")
        self.active = False
        if self.capture:
            self.capture.close()
        self.llm_q.put(None)
        if self.llm_t:
            self.llm_t.join()
        if self.tts_t:
            self.tts_t.join()
        if self.tts_client:
            self.tts_client.close()
        if self.pcm_t:
            self.pcm_t.join()
        self._state("Stopped")
        self.out.put(("closed", None, self.epoch))
        log("stop end")

    def close(self) -> None:
        if self.active:
            self.stop()


def load_mic(audio) -> bytes:
    if audio is None:
        return b""
    rate, values = audio
    x = np.asarray(values)
    if x.size == 0:
        return b""
    if x.ndim > 1:
        x = x.mean(axis=1)
    if np.issubdtype(x.dtype, np.integer):
        x = x.astype(np.float32) / max(abs(np.iinfo(x.dtype).min), np.iinfo(x.dtype).max)
    else:
        x = x.astype(np.float32, copy=False)
    return np.clip(linear_resample(np.clip(x, -1, 1), int(rate), ASR_RATE), -1, 1).astype("<f4").tobytes()


_sessions: dict[str, Conversation] = {}
_lock = threading.Lock()


def build(paths: Paths):
    settings = load_settings(paths.data_dir)

    def sid(request: gr.Request | None) -> str:
        if request is None or not request.session_hash:
            return ""
        return request.session_hash

    def drop(session: str) -> None:
        with _lock:
            engine = _sessions.pop(session, None)
        if engine:
            engine.close()

    def start(request: gr.Request | None):
        session = sid(request)
        drop(session)
        engine = Conversation(paths, settings)
        engine.start()
        with _lock:
            _sessions[session] = engine
        return engine.transcript, engine.answer, engine.status, gr.Audio(value=None, interactive=True, recording=True), gr.Button(interactive=False), gr.Button(interactive=True)

    def pump(request: gr.Request | None):
        with _lock:
            engine = _sessions.get(sid(request))
        if engine is None:
            return
        hold = (gr.skip(), gr.skip(), gr.skip())
        stopped = (gr.Audio(value=None, interactive=False, recording=False), gr.Button(interactive=True), gr.Button(interactive=False))
        while True:
            kind, payload, epoch = engine.out.get()
            audio = gr.skip()
            if kind == "audio-pcm" and epoch == engine.epoch:
                audio = pcm_to_wav(payload, TTS_RATE)
            elif kind == "audio-reset" and epoch == engine.epoch:
                audio = gr.Audio(value=None, streaming=True, autoplay=True)
            if kind == "closed":
                yield engine.transcript, engine.answer, audio, engine.status, *stopped
                return
            yield engine.transcript, engine.answer, audio, engine.status, *hold

    def feed(audio, request: gr.Request | None):
        pcm = load_mic(audio)
        with _lock:
            engine = _sessions.get(sid(request))
            if engine:
                engine.feed(pcm)

    def stop(request: gr.Request | None):
        with _lock:
            engine = _sessions.pop(sid(request), None)
        if engine:
            engine.stop()
        return "Stopped", gr.Button(interactive=True)

    with gr.Blocks(fill_width=True, title="Trident") as demo:
        mic = gr.Audio(sources=["microphone"], type="numpy", streaming=True, interactive=False, label="Microphone")
        with gr.Row():
            start_btn = gr.Button("Start", variant="primary")
            stop_btn = gr.Button("Stop", interactive=False)
        status = gr.Textbox(value="Stopped", label="Status", interactive=False)
        you = gr.Textbox(label="You", lines=5, interactive=False)
        bot = gr.Textbox(label="Trident", lines=6, interactive=False)
        speaker = gr.Audio(label="Speech", streaming=True, autoplay=True)
        start_btn.click(start, outputs=[you, bot, status, mic, start_btn, stop_btn], concurrency_limit=None, show_progress="minimal").then(
            pump, outputs=[you, bot, speaker, status, mic, start_btn, stop_btn], concurrency_limit=None, show_progress="hidden",
        )
        mic.stream(feed, mic, outputs=None, time_limit=MIC_LIMIT_S, stream_every=FEED_S, concurrency_limit=1, show_progress="hidden")
        stop_btn.click(lambda: (gr.Audio(value=None, interactive=False, recording=False), gr.Button(interactive=False)), outputs=[mic, stop_btn], queue=False).then(
            stop, outputs=[status, start_btn], concurrency_limit=None, show_progress="minimal",
        )
        demo.unload(lambda request=None: drop(sid(request)))
    return demo.queue(default_concurrency_limit=None)


def launch(paths: Paths) -> None:
    build(paths).launch(server_name="127.0.0.1", server_port=7860, show_error=True)
