from __future__ import annotations

import io
import queue
import threading
import time
import wave

import gradio as gr
import numpy as np
from silero_vad_notorch import VADIterator, load_silero_vad

from config import ASR_RATE, FEED_S, MIC_LIMIT_S, PLAY_SLICE_S, Paths, TTS_KNOBS, TTS_RATE, V3_LANGUAGES, VAD_FRAME, load_settings, log
from runtime import Chatterbox, boot, gemma_stream, require_alive, stop_all, transcribe

def resample(x: np.ndarray, src: int) -> np.ndarray:
    if src == ASR_RATE or not x.size:
        return x
    n = max(1, round(x.size * ASR_RATE / src))
    return np.interp(np.linspace(0, x.size - 1, n), np.arange(x.size), x).astype(np.float32)

def spoken(text: str) -> str:
    text = text.replace("\r", "").strip()
    marker = "Assistant:\n"
    if marker in text:
        text = text.rsplit(marker, 1)[1].strip()
    return text

def wav_bytes(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        out.writeframes(pcm)
    return buf.getvalue()

class Segmenter:
    def __init__(self) -> None:
        self.sent = 0

    def take(self, text: str, flush: bool = False) -> list[str]:
        out, minimum, hard = [], min(TTS_KNOBS["first_chars"], TTS_KNOBS["chars"]), TTS_KNOBS["chars"]
        while self.sent < len(text):
            pending, cut = text[self.sent:], 0
            for i in range(minimum - 1, min(len(pending), hard)):
                if pending[i] in ".?!" and (i + 1 == len(pending) or pending[i + 1].isspace()):
                    cut = i + 1
                    break
            if not cut and len(pending) >= hard:
                split = max(pending.rfind(" ", minimum, hard), pending.rfind("\n", minimum, hard), pending.rfind("\t", minimum, hard))
                cut = split + 1 if split >= minimum else hard
            if not cut:
                cut = len(pending) if flush else 0
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
    def __init__(self, on_text, on_epoch, asr: str, settings: dict) -> None:
        self.on_text, self.on_epoch, self.asr = on_text, on_epoch, asr
        self.vad = VADIterator(load_silero_vad(onnx=True), threshold=float(settings["vad_threshold"]), sampling_rate=ASR_RATE, min_silence_duration_ms=int(settings["vad_silence_ms"]), speech_pad_ms=0)
        self.q: queue.SimpleQueue = queue.SimpleQueue()
        self.pending = np.empty(0, dtype=np.float32)
        self.audio = bytearray()
        self.thread = threading.Thread(target=self._loop, name="capture", daemon=True)
        self.speech = self.active = False
        self.turn = 0

    def open(self) -> None:
        self.active = True
        self.thread.start()
        log("capture open")

    def feed(self, pcm: bytes) -> None:
        if self.active and pcm:
            self.q.put(pcm)

    def _asr(self) -> None:
        if not self.audio:
            return
        pcm = bytes(self.audio)
        self.audio.clear()
        duration = len(pcm) / (ASR_RATE * 4)
        x = np.frombuffer(pcm, dtype="<f4")
        wav = wav_bytes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes(), ASR_RATE)
        started = time.perf_counter()
        text = transcribe(self.asr, wav)
        elapsed = time.perf_counter() - started
        log(f"asr input_s={duration:.3f} inference_ms={elapsed * 1000:.0f} rtf={elapsed / duration:.3f} text={text!r}")
        if self.active and text:
            self.on_text(text)

    def _loop(self) -> None:
        while pcm := self.q.get():
            self.pending = np.concatenate((self.pending, np.frombuffer(pcm, dtype="<f4")))
            while self.pending.size >= VAD_FRAME:
                frame, self.pending = self.pending[:VAD_FRAME], self.pending[VAD_FRAME:]
                event = self.vad(frame) or {}
                if "start" in event:
                    self.turn += 1
                    self.audio.clear()
                    self.speech = True
                    self.on_epoch()
                    log(f"vad start turn={self.turn}")
                if self.speech:
                    self.audio.extend(frame.astype("<f4", copy=False).tobytes())
                if "end" in event:
                    log(f"vad end turn={self.turn} input_s={len(self.audio) / (ASR_RATE * 4):.3f}")
                    self.speech = False
                    self._asr()
        self.audio.clear()

    def close(self) -> None:
        if self.active:
            self.active = False
            self.q.put(None)
            self.thread.join()
            log("capture close")

class Conversation:
    def __init__(self, paths: Paths, settings: dict) -> None:
        self.settings = settings
        self.parakeet, self.gemma = require_alive("parakeet"), require_alive("gemma")
        require_alive("chatterbox")
        self.epoch = self.turn = 0
        self.transcript = self.answer = ""
        self.status = "Stopped"
        self.history: list[dict[str, str]] = []
        self.out: queue.SimpleQueue = queue.SimpleQueue()
        self.llm_q: queue.SimpleQueue = queue.SimpleQueue()
        self.lock = threading.Lock()
        self.tts = Chatterbox()
        self.capture = Capture(self._utterance, lambda: self._bump("vad-start"), self.parakeet, settings)
        self.llm_t = threading.Thread(target=self._llm, name="llm", daemon=True)
        self.pcm_t = threading.Thread(target=self._pcm, name="pcm", daemon=True)
        self.active = False

    def _bump(self, reason: str) -> int:
        with self.lock:
            self.epoch += 1
            epoch = self.epoch
            self.tts.send(epoch, [])
        log(f"barge-in epoch={epoch} reason={reason} turn={self.turn}")
        self.out.put(("audio-reset", None, epoch))
        return epoch

    def _state(self, status: str) -> None:
        self.status = status
        self.out.put(("state", None, self.epoch))

    def start(self) -> None:
        self.tts.open()
        self.pcm_t.start()
        self.capture.open()
        self.llm_t.start()
        self.active = True
        self._state("Listening")

    def feed(self, pcm: bytes) -> None:
        if self.active:
            self.capture.feed(pcm)

    def _utterance(self, text: str) -> None:
        if not self.active:
            return
        text = text.strip()
        if not text:
            return
        self.transcript = (self.transcript + " " + text).strip()
        with self.lock:
            epoch = self.epoch
        log(f"user epoch={epoch} text={text!r}")
        self.llm_q.put((epoch, text))
        self._state(f"Heard {len(text)} chars")

    def _send(self, epoch: int, turn: int, unit: str, first: bool) -> bool:
        with self.lock:
            if epoch != self.epoch:
                return first
            self.tts.send(epoch, [unit])
        if first:
            log(f"tts first_send turn={turn} epoch={epoch} text={unit!r}")
        return False

    def _llm(self) -> None:
        while item := self.llm_q.get():
            epoch, prompt = item
            with self.lock:
                stale = epoch != self.epoch
            if stale:
                continue
            self.turn += 1
            turn, t0, first, first_send = self.turn, time.perf_counter(), True, True
            log(f"llm begin turn={turn} epoch={epoch} prompt={prompt!r}")
            self._state("Thinking")
            seg, raw = Segmenter(), ""
            messages = [{"role": "system", "content": self.settings["system_prompt"].strip()}, *self.history, {"role": "user", "content": prompt}]
            gen = gemma_stream(self.gemma, messages)
            try:
                for delta in gen:
                    with self.lock:
                        stale = epoch != self.epoch
                    if stale:
                        break
                    if first:
                        log(f"llm first turn={turn} epoch={epoch} ms={(time.perf_counter() - t0) * 1000:.0f}")
                        first = False
                    raw += delta
                    self.answer = raw
                    for unit in seg.take(spoken(raw)):
                        first_send = self._send(epoch, turn, unit, first_send)
            finally:
                gen.close()
            with self.lock:
                stale = epoch != self.epoch
            if stale:
                log(f"llm cancelled turn={turn} epoch={epoch} ms={(time.perf_counter() - t0) * 1000:.0f}")
                continue
            answer = spoken(raw)
            for unit in seg.take(answer, True):
                first_send = self._send(epoch, turn, unit, first_send)
            if answer:
                self.answer = answer
                self.history.extend(({"role": "user", "content": prompt}, {"role": "assistant", "content": answer}))
            log(f"llm done turn={turn} epoch={epoch} empty={int(not answer)} ms={(time.perf_counter() - t0) * 1000:.0f} answer={answer!r}")
            self._state("Listening")

    def _pcm(self) -> None:
        first_epoch = -1
        while frame := self.tts.recv_frame():
            kind, epoch, payload = frame
            if kind == 2 and payload and epoch == self.epoch:
                if first_epoch != epoch:
                    first_epoch = epoch
                    log(f"tts first_pcm epoch={epoch} bytes={len(payload)}")
                self.out.put(("audio-pcm", payload, epoch))
            if kind == 0:
                log(f"tts done epoch={epoch}")
            if kind == 1:
                raise RuntimeError("TTS: " + payload.decode("utf-8", "replace"))
            if kind > 2:
                raise RuntimeError(f"TTS frame {kind}")

    def stop(self) -> None:
        if not self.active:
            return
        log(f"stop begin turn={self.turn} epoch={self.epoch}")
        self.active = False
        self._bump("stop")
        self.capture.close()
        self.llm_q.put(None)
        self.llm_t.join()
        self.tts.close()
        self.pcm_t.join()
        self._state("Stopped")
        self.out.put(("closed", None, self.epoch))
        log("stop end")

def load_mic(audio) -> bytes:
    if audio is None:
        return b""
    rate, values = audio
    x = np.asarray(values)
    if not x.size:
        return b""
    integer = np.issubdtype(x.dtype, np.integer)
    if integer:
        info = np.iinfo(x.dtype)
        x = x.astype(np.float32) / max(abs(info.min), info.max)
    if not integer:
        x = x.astype(np.float32, copy=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return np.clip(resample(np.clip(x, -1, 1), int(rate)), -1, 1).astype("<f4").tobytes()

def build(paths: Paths):
    settings = load_settings(paths.data_dir)
    engine: Conversation | None = None
    idle = (gr.Audio(value=None, interactive=False, recording=False), gr.update(interactive=True), gr.update(interactive=False))

    def drop() -> None:
        nonlocal engine
        if engine is not None:
            engine.stop()
            engine = None
        stop_all()

    def start(family, language):
        nonlocal engine
        drop()
        boot(paths, str(family), str(language))
        engine = Conversation(paths, settings)
        engine.start()
        return engine.transcript, engine.answer, engine.status, gr.Audio(value=None, interactive=True, recording=True), gr.update(interactive=False), gr.update(interactive=True)

    def set_family(family):
        return gr.update(choices=list(V3_LANGUAGES) if family == "v3" else ["en"], value="en")

    def pump():
        current = engine
        if current is None:
            return
        while True:
            kind, payload, epoch = current.out.get()
            audio = gr.skip()
            if kind == "audio-pcm" and epoch == current.epoch:
                step = max(2, int(TTS_RATE * PLAY_SLICE_S) * 2)
                for offset in range(0, len(payload), step):
                    if epoch != current.epoch:
                        break
                    chunk = payload[offset:offset + step]
                    yield current.transcript, current.answer, wav_bytes(chunk, TTS_RATE), current.status, gr.skip(), gr.skip(), gr.skip()
                    if offset + step < len(payload):
                        time.sleep(len(chunk) / (TTS_RATE * 2))
                continue
            if kind == "audio-reset" and epoch == current.epoch:
                audio = gr.Audio(value=None, streaming=True, autoplay=True)
            if kind == "closed":
                yield current.transcript, current.answer, audio, current.status, *idle
                return
            yield current.transcript, current.answer, audio, current.status, gr.skip(), gr.skip(), gr.skip()

    def feed(audio):
        if engine is not None:
            engine.feed(load_mic(audio))

    def stop():
        drop()
        return "", "", gr.Audio(value=None, streaming=True, autoplay=True), "Stopped", *idle

    with gr.Blocks(title="Trident") as demo:
        with gr.Row():
            family = gr.Dropdown(choices=["nano", "turbo", "v3"], value="nano", label="Synthesizer")
            language = gr.Dropdown(choices=["en"], value="en", label="TTS language")
        mic = gr.Audio(sources=["microphone"], type="numpy", streaming=True, interactive=False, label="Microphone")
        with gr.Row():
            start_btn = gr.Button("Start", variant="primary")
            stop_btn = gr.Button("Stop", interactive=False)
        status = gr.Textbox(value="Stopped", label="Status", interactive=False)
        you = gr.Textbox(label="You", lines=3, interactive=False)
        bot = gr.Textbox(label="Trident", lines=4, interactive=False)
        speaker = gr.Audio(label="Speech", streaming=True, autoplay=True)
        family.change(set_family, family, language, queue=False)
        start_btn.click(start, inputs=[family, language], outputs=[you, bot, status, mic, start_btn, stop_btn], concurrency_limit=None).then(pump, outputs=[you, bot, speaker, status, mic, start_btn, stop_btn], concurrency_limit=None)
        mic.stream(feed, mic, outputs=None, time_limit=MIC_LIMIT_S, stream_every=FEED_S, concurrency_limit=1)
        stop_btn.click(stop, outputs=[you, bot, speaker, status, mic, start_btn, stop_btn], queue=False)
        demo.unload(drop)
    return demo.queue(default_concurrency_limit=None)

def launch(paths: Paths) -> None:
    try:
        build(paths).launch(server_name="127.0.0.1", server_port=7860, show_error=True)
    finally:
        stop_all()
