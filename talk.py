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

import config
from config import (
    ASR_RATE, ECHO_MS, FEED_S, MIC_LIMIT_S, Paths, TTS_KNOBS, TTS_RATE, VAD_FRAME,
    load_settings, log, voice_wav,
)
from runtime import Chatterbox, gemma_stream, pcm24, require_alive, start_chatterbox, transcribe


def resample(samples: np.ndarray, src: int, dst: int) -> np.ndarray:
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
            if event:
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
        self.vad = None
        self.thread = None
        self.active = False
        self.wav = self.path = None
        self.index = 0
        self.ring = np.zeros(0, dtype=np.int16)
        self.lock = threading.Lock()

    def open(self) -> None:
        self.vad = VAD(self.settings["vad_threshold"], self.settings["vad_silence_ms"])
        self.thread = threading.Thread(target=self._loop, name="capture", daemon=True)
        self.thread.start()
        self.active = True
        log("capture open")

    def feed(self, pcm: bytes, rate: int = ASR_RATE) -> None:
        if self.active and pcm:
            self.q.put(("feed", (pcm, rate)))

    def play(self, pcm16: bytes, rate: int = TTS_RATE) -> None:
        if not pcm16:
            return
        samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32)
        samples = resample(samples, rate, ASR_RATE)
        pcm = np.clip(samples, -32768, 32767).astype("<i2")
        with self.lock:
            n = max(1, int(ASR_RATE * ECHO_MS / 1000))
            self.ring = np.concatenate((self.ring, pcm))[-n:] if self.ring.size else pcm

    def _echo(self, pcm: bytes) -> bool:
        with self.lock:
            ring = self.ring
        mic = np.frombuffer(pcm, dtype="<f4")
        window = min(mic.size, int(ASR_RATE * 0.2))
        if window < 160 or ring.size < window:
            return False
        mic_w = mic[-window:]
        mic_c = mic_w - mic_w.mean()
        if float(np.sqrt((mic_c * mic_c).mean())) < 1e-4:
            return False
        ref = ring.astype(np.float32)
        sums = np.concatenate(([0.0], np.cumsum(ref, dtype=np.float64)))
        squares = np.concatenate(([0.0], np.cumsum(ref * ref, dtype=np.float64)))
        energy = squares[window:] - squares[:-window] - (sums[window:] - sums[:-window]) ** 2 / window
        dots = np.correlate(ref, mic_c, mode="valid")
        denom = np.sqrt(np.maximum(energy, 0.0)) * np.sqrt((mic_c * mic_c).sum())
        corr = np.divide(dots, denom, out=np.zeros_like(dots), where=denom > 0)
        score = float(corr[int(np.argmax(corr))])
        if score >= 0.7:
            log(f"echo suppress corr={score:.3f}")
            return True
        return False

    def _to16k(self, pcm: bytes, rate: int) -> bytes:
        return resample(np.frombuffer(pcm, dtype="<f4"), rate, ASR_RATE).astype("<f4").tobytes()

    def _open_turn(self) -> None:
        if self.wav is not None:
            return
        self.index += 1
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

    def _close(self) -> Path | None:
        if self.wav is None:
            return None
        self.wav.close()
        path, self.wav, self.path = self.path, None, None
        return path

    def _asr(self, reason: str) -> None:
        path = self._close()
        if not path:
            return
        try:
            started = time.perf_counter()
            with wave.open(str(path), "rb") as audio:
                duration = audio.getnframes() / audio.getframerate()
            text = transcribe(self.asr, path)
            elapsed = time.perf_counter() - started
            rtf = elapsed / duration if duration else 0
            log(f"asr {reason} {duration:.2f}s x{1 / rtf if rtf else 0:.2f} {text!r}")
        finally:
            path.unlink(missing_ok=True)
        if text:
            self.on_text(text)

    def _loop(self) -> None:
        try:
            while True:
                op, payload = self.q.get()
                if op == "finish":
                    if self.vad and self.vad.speech:
                        self._asr("stop")
                    else:
                        path = self._close()
                        if path:
                            path.unlink(missing_ok=True)
                    return
                pcm, rate = payload
                pcm = self._to16k(pcm, rate)
                started, ended = self.vad.feed(pcm)
                if started:
                    if self._echo(pcm):
                        self.vad.reset()
                    else:
                        self.on_epoch()
                        self._open_turn()
                if self.vad.speech:
                    self._write(pcm)
                if ended:
                    self._asr("vad")
        except Exception as exc:
            log(f"capture failed {type(exc).__name__}: {exc}")
            raise

    def close(self) -> None:
        if not self.active:
            return
        self.active = False
        self.q.put(("finish", None))
        if self.thread:
            self.thread.join()
        log("capture close")


class Conversation:
    def __init__(self, models_dir: Path, data_dir: Path, settings: dict, paths: Paths) -> None:
        self.models_dir, self.data_dir, self.settings, self.paths = models_dir, data_dir, dict(settings), paths
        self.parakeet = self.gemma = self.tts = None
        self.epoch = 0
        self.transcript = self.answer = ""
        self.status = "Stopped"
        self.history: list[dict] = []
        self.turn = 0
        self.out: queue.SimpleQueue = queue.SimpleQueue()
        self.llm_q: queue.SimpleQueue = queue.SimpleQueue()
        self.tts_q: queue.SimpleQueue = queue.SimpleQueue()
        self.capture = None
        self.llm_t = self.tts_t = None
        self.active = False
        self.lock = threading.Lock()
        self.failure = None

    def _bump(self, reason: str) -> int:
        with self.lock:
            self.epoch += 1
            epoch = self.epoch
        log(f"barge-in epoch={epoch} reason={reason} turn={self.turn}")
        self.out.put(("audio-reset", None, epoch))
        return epoch

    def _state(self, status: str | None = None) -> None:
        if status is not None:
            self.status = status
        self.out.put(("state", None, self.epoch))

    def start(self) -> None:
        self.parakeet = require_alive("parakeet")
        self.gemma = require_alive("gemma")
        ref = pcm24(voice_wav(self.data_dir, self.settings["tts_voice"]), self.data_dir / "prepared")
        self.tts = start_chatterbox(self.models_dir, ref)
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
        try:
            while True:
                item = self.llm_q.get()
                if item is None:
                    self.tts_q.put(None)
                    return
                epoch, prompt = item
                with self.lock:
                    live = self.epoch
                if epoch != live:
                    log(f"llm skip stale epoch={epoch} live={live}")
                    continue
                self.answer = ""
                self.turn += 1
                turn = self.turn
                log(f"llm begin turn={turn} epoch={epoch} {prompt!r}")
                self._state("Thinking")
                t0 = time.perf_counter()
                ttfa = None
                seg = Segmenter(min(TTS_KNOBS["first_chars"], TTS_KNOBS["chars"]), TTS_KNOBS["chars"])
                raw = ""
                messages = [{"role": "system", "content": self.settings["system_prompt"].strip()}, *self.history, {"role": "user", "content": prompt}]
                for delta in gemma_stream(self.gemma, messages):
                    with self.lock:
                        if epoch != self.epoch:
                            break
                    if ttfa is None:
                        ttfa = time.perf_counter() - t0
                    raw += delta
                    self.answer = raw
                    self.out.put(("state", None, epoch))
                    for unit in seg.update(spoken(raw)):
                        self.tts_q.put((epoch, unit))
                with self.lock:
                    if epoch != self.epoch:
                        log(f"llm cancelled turn={turn} epoch={epoch} live={self.epoch}")
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
        except Exception as exc:
            self.failure = exc
            log(f"llm failed {type(exc).__name__}: {exc}")
            self.out.put(("error", exc, self.epoch))

    def _tts(self) -> None:
        try:
            while True:
                item = self.tts_q.get()
                if item is None:
                    return
                epoch, text = item
                with self.lock:
                    live = self.epoch
                if not text or epoch != live:
                    if text:
                        log(f"tts skip stale epoch={epoch} live={live} {text!r}")
                    continue
                client = Chatterbox(self.tts, cancel=lambda e=epoch: self.epoch != e)
                try:
                    client.open()
                    client.send(text)
                    log(f"tts begin epoch={epoch} {text!r}")
                    for pcm in client:
                        with self.lock:
                            if self.epoch != epoch:
                                client.close()
                                log(f"tts cancel epoch={epoch} live={self.epoch}")
                                break
                        if pcm:
                            self.out.put(("audio-pcm", pcm, epoch))
                            if self.capture:
                                self.capture.play(pcm, TTS_RATE)
                    else:
                        log(f"tts done epoch={epoch}")
                except InterruptedError:
                    log(f"tts interrupted epoch={epoch} live={self.epoch}")
                except Exception as exc:
                    log(f"tts failed epoch={epoch} {type(exc).__name__}: {exc}")
                finally:
                    client.close()
        except Exception as exc:
            self.failure = exc
            log(f"tts worker failed {type(exc).__name__}: {exc}")
            self.out.put(("error", exc, self.epoch))

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
        if self.paths.transcript:
            self.paths.transcript.write_text(self.transcript + ("\n" if self.transcript else ""), encoding="utf-8")
        self._state("Stopped")
        self.out.put(("closed", None, self.epoch))
        log("stop end")

    def close(self) -> None:
        if self.active:
            self.stop()


def load_mic(audio) -> bytes:
    if audio is None:
        return b""
    if isinstance(audio, (tuple, list)) and len(audio) == 2:
        rate, values = audio
        x = np.asarray(values)
        if x.ndim > 1:
            x = x.mean(axis=1)
        if np.issubdtype(x.dtype, np.integer):
            x = x.astype(np.float32) / max(abs(np.iinfo(x.dtype).min), np.iinfo(x.dtype).max)
        else:
            x = x.astype(np.float32, copy=False)
        return np.clip(resample(np.clip(x, -1, 1), int(rate), ASR_RATE), -1, 1).astype("<f4").tobytes()
    path = Path(str(audio))
    if not path.is_file() or path.stat().st_size < 44:
        return b""
    try:
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            nch = w.getnchannels()
            raw = w.readframes(w.getnframes())
            sw = w.getsampwidth()
    except (wave.Error, EOFError, OSError):
        return b""
    if sw == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        return b""
    if nch > 1:
        x = x.reshape(-1, nch).mean(axis=1)
    return np.clip(resample(x, rate, ASR_RATE), -1, 1).astype("<f4").tobytes()


def wav_bytes(pcm16: bytes) -> bytes:
    if not pcm16:
        return b""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(TTS_RATE)
        out.writeframes(pcm16)
    return buf.getvalue()


_sessions: dict[str, Conversation] = {}
_lock = threading.Lock()


def build(models_dir: Path | None = None, data_dir: Path | None = None):
    root = Paths(models_dir, data_dir)
    if config.LOG:
        root.log = config.LOG
        root.run_dir = config.LOG.parent
        root.transcript = config.LOG.parent / (config.LOG.stem.replace("trident", "transcript") + ".txt")
    settings = load_settings(root.data_dir)

    def sid(request: gr.Request) -> str:
        if not request.session_hash:
            raise RuntimeError("Gradio session missing")
        return request.session_hash

    def drop(session: str) -> None:
        with _lock:
            engine = _sessions.pop(session, None)
        if engine:
            engine.close()

    def start(request: gr.Request):
        session = sid(request)
        drop(session)
        engine = Conversation(root.models_dir, root.data_dir, settings, root)
        engine.start()
        with _lock:
            _sessions[session] = engine
        return engine.transcript, engine.answer, engine.status, gr.Audio(value=None, interactive=True, recording=True), gr.Button(interactive=False), gr.Button(interactive=True)

    def pump(request: gr.Request):
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
                audio = wav_bytes(payload)
            elif kind == "audio-reset" and epoch == engine.epoch:
                audio = gr.Audio(value=None, streaming=True, autoplay=True)
            if kind == "error":
                drop(sid(request))
                yield engine.transcript, engine.answer, audio, engine.status, *stopped
                raise RuntimeError(str(payload))
            if kind == "closed":
                yield engine.transcript, engine.answer, audio, engine.status, *stopped
                return
            yield engine.transcript, engine.answer, audio, engine.status, *hold

    def feed(audio, request: gr.Request):
        pcm = load_mic(audio)
        with _lock:
            engine = _sessions.get(sid(request))
            if engine:
                engine.feed(pcm)

    def stop(request: gr.Request):
        with _lock:
            engine = _sessions.pop(sid(request), None)
        if engine:
            engine.stop()
        return "Stopped", gr.Button(interactive=True)

    with gr.Blocks(fill_width=True, title="Trident") as demo:
        mic = gr.Audio(sources=["microphone"], type="filepath", streaming=True, interactive=False, label="Microphone")
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
        demo.unload(lambda request: drop(sid(request)))
    return demo.queue(default_concurrency_limit=None)


def launch(models_dir: Path | None = None, data_dir: Path | None = None) -> None:
    build(models_dir, data_dir).launch(server_name="127.0.0.1", server_port=7860, show_error=True)
