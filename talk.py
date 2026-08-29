from __future__ import annotations

import io
import queue
import threading
import time
import wave
from collections import deque

import numpy as np
import sounddevice as sd
from silero_vad_notorch import VADIterator, load_silero_vad

from config import ASR_RATE, Paths, TTS_KNOBS, TTS_RATE, VAD_FRAME, emit, load_settings
from runtime import Chatterbox, boot, gemma_stream, require_alive, stop_all, transcribe


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


class Speaker:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pcm: deque[bytes] = deque()
        self.offset = self.epoch = 0
        self.error: RuntimeError | None = None
        self.stream: sd.RawOutputStream | None = None

    def _callback(self, outdata, _frames, _time, status) -> None:
        if status:
            self.error = RuntimeError(f"speaker: {status}")
            raise sd.CallbackAbort
        target = memoryview(outdata)
        target[:] = bytes(len(target))
        written = 0
        with self.lock:
            while written < len(target) and self.pcm:
                source = memoryview(self.pcm[0])
                count = min(len(target) - written, len(source) - self.offset)
                target[written:written + count] = source[self.offset:self.offset + count]
                written += count
                self.offset += count
                if self.offset == len(source):
                    self.pcm.popleft()
                    self.offset = 0

    def open(self) -> None:
        self.stream = sd.RawOutputStream(samplerate=TTS_RATE, blocksize=512, channels=1, dtype="int16", latency="low", callback=self._callback)
        self.stream.start()

    def put(self, epoch: int, pcm: bytes) -> bool:
        with self.lock:
            if epoch != self.epoch:
                return False
            self.pcm.append(pcm)
            return True

    def cancel(self, epoch: int) -> int:
        with self.lock:
            dropped = sum(map(len, self.pcm)) - (self.offset if self.pcm else 0)
            self.pcm.clear()
            self.offset = 0
            self.epoch = epoch
            return dropped

    def check(self) -> None:
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None


class Capture:
    def __init__(self, on_text, on_epoch, asr: str, settings: dict) -> None:
        self.on_text, self.on_epoch, self.asr = on_text, on_epoch, asr
        self.vad = VADIterator(load_silero_vad(onnx=True), threshold=float(settings["vad_threshold"]), sampling_rate=ASR_RATE, min_silence_duration_ms=int(settings["vad_silence_ms"]), speech_pad_ms=0)
        self.q: queue.SimpleQueue = queue.SimpleQueue()
        self.audio = bytearray()
        self.thread = threading.Thread(target=self._loop, name="capture", daemon=True)
        self.stream: sd.RawInputStream | None = None
        self.error: RuntimeError | None = None
        self.speech = self.active = False
        self.turn = 0

    def _callback(self, indata, frames, _time, status) -> None:
        if status or frames != VAD_FRAME:
            self.error = RuntimeError(f"microphone: status={status} frames={frames}")
            raise sd.CallbackAbort
        if self.active:
            self.q.put(bytes(indata))

    def open(self) -> None:
        self.active = True
        self.thread.start()
        self.stream = sd.RawInputStream(samplerate=ASR_RATE, blocksize=VAD_FRAME, channels=1, dtype="float32", latency="low", callback=self._callback)
        self.stream.start()
        emit("capture.open")

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
        emit("asr", turn=self.turn, input_s=round(duration, 3), inference_ms=round(elapsed * 1000), rtf=round(elapsed / duration, 3), text=text)
        if self.active and text:
            self.on_text(text)

    def _loop(self) -> None:
        while pcm := self.q.get():
            frame = np.frombuffer(pcm, dtype="<f4")
            event = self.vad(frame) or {}
            if "start" in event:
                self.turn += 1
                self.audio.clear()
                self.speech = True
                emit("vad.start", turn=self.turn, epoch=self.on_epoch())
            if self.speech:
                self.audio.extend(pcm)
            if "end" in event:
                emit("vad.end", turn=self.turn, input_s=round(len(self.audio) / (ASR_RATE * 4), 3))
                self.speech = False
                self._asr()
        self.audio.clear()

    def check(self) -> None:
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        if self.active:
            self.active = False
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
                self.stream = None
            self.q.put(None)
            self.thread.join()
            emit("capture.close")


class Conversation:
    def __init__(self, settings: dict) -> None:
        self.settings = settings
        self.parakeet, self.gemma = require_alive("parakeet"), require_alive("gemma")
        require_alive("chatterbox")
        self.epoch = self.turn = 0
        self.history: list[dict[str, str]] = []
        self.llm_q: queue.SimpleQueue = queue.SimpleQueue()
        self.lock = threading.Lock()
        self.tts = Chatterbox()
        self.speaker = Speaker()
        self.capture = Capture(self._utterance, lambda: self._bump("vad-start"), self.parakeet, settings)
        self.llm_t = threading.Thread(target=self._llm, name="llm", daemon=True)
        self.pcm_t = threading.Thread(target=self._pcm, name="pcm", daemon=True)
        self.active = False

    def _bump(self, reason: str) -> int:
        with self.lock:
            self.epoch += 1
            epoch = self.epoch
            dropped = self.speaker.cancel(epoch)
            self.tts.send(epoch, [])
        emit("audio.cancel", epoch=epoch, dropped_bytes=dropped, audio_ms=round(1000 * dropped / (TTS_RATE * 2)))
        emit("barge_in", epoch=epoch, reason=reason, turn=self.turn)
        return epoch

    def start(self) -> None:
        self.speaker.open()
        self.tts.open()
        self.pcm_t.start()
        self.llm_t.start()
        self.active = True
        self.capture.open()
        emit("audio.open", input=str(sd.query_devices(kind="input")["name"]), output=str(sd.query_devices(kind="output")["name"]), input_rate=ASR_RATE, output_rate=TTS_RATE, output_block=512)

    def _utterance(self, text: str) -> None:
        if not self.active:
            return
        text = text.strip()
        if not text:
            return
        with self.lock:
            epoch = self.epoch
        emit("user", epoch=epoch, chars=len(text), text=text)
        self.llm_q.put((epoch, text))

    def _send(self, epoch: int, turn: int, unit: str) -> None:
        with self.lock:
            if epoch != self.epoch:
                return
            self.tts.send(epoch, [unit])
        emit("tts.send", turn=turn, epoch=epoch, chars=len(unit), text=unit)

    def _llm(self) -> None:
        while item := self.llm_q.get():
            epoch, prompt = item
            with self.lock:
                stale = epoch != self.epoch
            if stale:
                emit("llm.skip", epoch=epoch)
                continue
            self.turn += 1
            turn, t0, first = self.turn, time.perf_counter(), True
            emit("llm.begin", turn=turn, epoch=epoch, chars=len(prompt), prompt=prompt)
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
                        emit("llm.first", turn=turn, epoch=epoch, ms=round((time.perf_counter() - t0) * 1000))
                        first = False
                    raw += delta
                    for unit in seg.take(spoken(raw)):
                        self._send(epoch, turn, unit)
            finally:
                gen.close()
            with self.lock:
                stale = epoch != self.epoch
            if stale:
                emit("llm.cancel", turn=turn, epoch=epoch, ms=round((time.perf_counter() - t0) * 1000))
                continue
            answer = spoken(raw)
            for unit in seg.take(answer, True):
                self._send(epoch, turn, unit)
            if answer:
                self.history.extend(({"role": "user", "content": prompt}, {"role": "assistant", "content": answer}))
            emit("llm.done", turn=turn, epoch=epoch, empty=not answer, ms=round((time.perf_counter() - t0) * 1000), chars=len(answer), answer=answer)

    def _pcm(self) -> None:
        while frame := self.tts.recv_frame():
            kind, epoch, payload = frame
            if kind == 2 and payload:
                with self.lock:
                    accepted = epoch == self.epoch and self.speaker.put(epoch, payload)
                if accepted:
                    emit("tts.pcm", epoch=epoch, bytes=len(payload), audio_ms=round(1000 * len(payload) / (TTS_RATE * 2)))
            if kind == 1:
                raise RuntimeError("TTS: " + payload.decode("utf-8"))
            if kind > 2:
                raise RuntimeError(f"TTS frame {kind}")

    def check(self) -> None:
        self.capture.check()
        self.speaker.check()

    def stop(self) -> None:
        if not self.active:
            return
        emit("stop.begin", turn=self.turn, epoch=self.epoch)
        self.active = False
        self._bump("stop")
        self.capture.close()
        self.llm_q.put(None)
        self.llm_t.join()
        self.tts.close()
        self.pcm_t.join()
        self.speaker.close()
        emit("audio.close")
        emit("stop.end")


def launch(paths: Paths, family: str = "nano", language: str = "en") -> None:
    conversation = None
    try:
        boot(paths, family, language)
        conversation = Conversation(load_settings(paths.data_dir))
        conversation.start()
        emit("console.ready", family=family, language=language, stop="Ctrl+C")
        while True:
            conversation.check()
            time.sleep(.1)
    except KeyboardInterrupt:
        emit("console.interrupt")
    finally:
        if conversation is not None:
            conversation.stop()
        stop_all()
