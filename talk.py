from __future__ import annotations

import io
import queue
import threading
import time
import wave
from collections import deque

import numpy as np
import onnxruntime as ort
import sounddevice as sd
from silero_vad_notorch import VADIterator, load_silero_vad

from config import ASR_RATE, SMART_TURN_FILE, Paths, TTS_KNOBS, TTS_RATE, VAD_FRAME, emit, load_settings, raise_worker_failure, wait_workers
from runtime import Chatterbox, boot, check_residents, gemma_stream, require_alive, stop_all, transcribe

def spoken(text: str) -> str:
    text = text.replace("\r", "").strip()
    marker = "Assistant:\n"
    return text.rsplit(marker, 1)[-1].strip() if marker in text else text

def wav_bytes(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setparams((1, 2, ASR_RATE, 0, "NONE", "not compressed"))
        out.writeframes(pcm)
    return buf.getvalue()

def _mel_filters() -> np.ndarray:
    def hz_to_mel(hz):
        hz = np.asarray(hz, dtype=np.float64)
        mel = hz / (200.0 / 3.0)
        mask = hz >= 1000.0
        mel[mask] = 15.0 + np.log(hz[mask] / 1000.0) / (np.log(6.4) / 27.0)
        return mel
    def mel_to_hz(mel):
        mel = np.asarray(mel, dtype=np.float64)
        hz = (200.0 / 3.0) * mel
        mask = mel >= 15.0
        hz[mask] = 1000.0 * np.exp((np.log(6.4) / 27.0) * (mel[mask] - 15.0))
        return hz
    centers = mel_to_hz(np.linspace(hz_to_mel([0.0])[0], hz_to_mel([8000.0])[0], 82))
    bins = np.linspace(0.0, ASR_RATE / 2.0, 201)
    down = (bins[:, None] - centers[:-2]) / (centers[1:-1] - centers[:-2])
    up = (centers[2:] - bins[:, None]) / (centers[2:] - centers[1:-1])
    filters = np.maximum(0.0, np.minimum(down, up))
    return filters * 2.0 / (centers[2:] - centers[:-2])

_FILTERS = _mel_filters()
_WINDOW = np.hanning(401)[:-1]

class SmartTurn:
    def __init__(self, model, threshold: float, context_seconds: float) -> None:
        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
        self.threshold = float(threshold)
        self.samples = int(float(context_seconds) * ASR_RATE)

    def decide(self, pcm: bytes) -> tuple[bool, float]:
        audio = np.frombuffer(pcm, dtype="<f4")
        audio = audio[-self.samples:] if audio.size > self.samples else np.pad(audio, (self.samples - audio.size, 0))
        audio = (audio - audio.mean()) / np.sqrt(audio.var() + 1e-7)
        frames = np.lib.stride_tricks.sliding_window_view(np.pad(audio, (200, 200), mode="reflect"), 400)[::160]
        power = np.abs(np.fft.rfft(frames * _WINDOW, n=400, axis=1)) ** 2
        mel = np.maximum(power @ _FILTERS, 1e-10)
        spec = np.log10(mel).T[:, :-1]
        features = ((np.maximum(spec, spec.max() - 8.0) + 4.0) / 4.0).astype(np.float32)[None]
        probability = float(self.session.run(None, {"input_features": features})[0][0].item())
        return probability > self.threshold, probability

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
        self.events: queue.SimpleQueue = queue.SimpleQueue()
        self.offset = self.epoch = 0
        self.silence: tuple[int, int] | None = None
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
            if self.silence is not None and not self.pcm:
                epoch, requested = self.silence
                self.silence = None
                self.events.put((epoch, requested, time.perf_counter_ns()))
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
        self.stream = sd.RawOutputStream(samplerate=TTS_RATE, blocksize=VAD_FRAME, channels=1, dtype="int16", latency="low", callback=self._callback)
        self.stream.start()

    def put(self, epoch: int, pcm: bytes) -> bool:
        with self.lock:
            if epoch != self.epoch:
                return False
            self.pcm.append(pcm)
            return True

    def busy(self) -> bool:
        with self.lock:
            return bool(self.pcm)

    def cancel(self, epoch: int) -> int:
        with self.lock:
            dropped = sum(map(len, self.pcm)) - (self.offset if self.pcm else 0)
            self.pcm.clear()
            self.offset = 0
            self.epoch = epoch
            self.silence = (epoch, time.perf_counter_ns())
            return dropped

    def check(self) -> None:
        if self.error is not None:
            raise self.error
        while not self.events.empty():
            epoch, requested, observed = self.events.get()
            emit("audio.silent", epoch=epoch, latency_ms=round((observed - requested) / 1e6, 3))

    def close(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None

class Capture:
    def __init__(self, model, settings: dict, on_start, on_utterance) -> None:
        self.on_start, self.on_utterance = on_start, on_utterance
        self.vad = VADIterator(load_silero_vad(onnx=True), threshold=.5, sampling_rate=ASR_RATE, min_silence_duration_ms=int(settings["candidate_silence_ms"]), speech_pad_ms=0)
        self.smart = SmartTurn(model, settings["completion_threshold"], settings["acoustic_context_seconds"])
        self.context_seconds = float(settings["acoustic_context_seconds"])
        self.q: queue.SimpleQueue = queue.SimpleQueue()
        self.audio = bytearray()
        self.thread = threading.Thread(target=self._loop, name="vad")
        self.stream: sd.RawInputStream | None = None
        self.error: RuntimeError | None = None
        self.active = self.utterance = False
        self.utterance_id = self.epoch = 0

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

    def _loop(self) -> None:
        while (pcm := self.q.get()) is not None:
            event = self.vad(np.frombuffer(pcm, dtype="<f4")) or {}
            if "start" in event:
                if not self.utterance:
                    self.audio.clear()
                    self.utterance = True
                    self.utterance_id += 1
                    self.epoch = self.on_start(self.utterance_id)
                    emit("vad.start", epoch=self.epoch, utterance_id=self.utterance_id)
                else:
                    emit("vad.resume", epoch=self.epoch, utterance_id=self.utterance_id)
            if self.utterance:
                self.audio.extend(pcm)
            if "end" in event:
                started = time.perf_counter()
                complete, probability = self.smart.decide(bytes(self.audio))
                elapsed = (time.perf_counter() - started) * 1000
                emit("turn.decision", epoch=self.epoch, utterance_id=self.utterance_id, complete=complete, probability=round(probability, 6), inference_ms=round(elapsed, 3), context_s=self.context_seconds, input_s=round(len(self.audio) / (ASR_RATE * 4), 3))
                if complete:
                    audio = bytes(self.audio)
                    self.audio.clear()
                    self.utterance = False
                    self.vad.reset_states()
                    emit("utterance.complete", epoch=self.epoch, utterance_id=self.utterance_id, input_s=round(len(audio) / (ASR_RATE * 4), 3))
                    self.on_utterance(self.epoch, self.utterance_id, audio)
        if self.audio:
            emit("utterance.drop", epoch=self.epoch, utterance_id=self.utterance_id, reason="shutdown", bytes=len(self.audio))
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
    def __init__(self, paths: Paths, settings: dict) -> None:
        self.settings = settings
        self.parakeet, self.gemma = require_alive("parakeet"), require_alive("gemma")
        require_alive("chatterbox")
        self.epoch = self.response_id = 0
        self.history: list[dict[str, str]] = []
        self.q: queue.SimpleQueue = queue.SimpleQueue()
        self.lock = threading.Lock()
        self.tts = Chatterbox()
        self.speaker = Speaker()
        self.capture = Capture(paths.models_dir / SMART_TURN_FILE, settings, self._speech_start, self._utterance)
        self.conversation_thread = threading.Thread(target=self._conversation, name="conversation")
        self.pcm_thread = threading.Thread(target=self._pcm, name="pcm")
        self.llm_active = False
        self.tts_pending = 0
        self.first_pcm: set[int] = set()
        self.active = False

    def _advance(self, reason: str, utterance_id: int = 0) -> int:
        with self.lock:
            interrupted = self.llm_active or self.tts_pending > 0 or self.speaker.busy()
            self.epoch += 1
            epoch = self.epoch
            self.llm_active = False
            self.tts_pending = 0
            dropped = self.speaker.cancel(epoch)
            self.tts.send(epoch, 0, 0)
        emit("epoch.advance", epoch=epoch, reason=reason, utterance_id=utterance_id)
        emit("audio.cancel", epoch=epoch, dropped_bytes=dropped, audio_ms=round(1000 * dropped / (TTS_RATE * 2)))
        if reason == "speech" and interrupted:
            emit("barge_in", epoch=epoch, utterance_id=utterance_id)
        return epoch

    def _speech_start(self, utterance_id: int) -> int:
        return self._advance("speech", utterance_id)

    def start(self) -> None:
        self.speaker.open()
        self.tts.open()
        self.pcm_thread.start()
        self.conversation_thread.start()
        self.active = True
        self.capture.open()
        emit("audio.open", input=str(sd.query_devices(kind="input")["name"]), output=str(sd.query_devices(kind="output")["name"]), input_rate=ASR_RATE, output_rate=TTS_RATE, output_block=VAD_FRAME)

    def _utterance(self, epoch: int, utterance_id: int, pcm: bytes) -> None:
        if self.active:
            self.q.put((epoch, utterance_id, pcm))

    def _send(self, epoch: int, response_id: int, piece_id: int, text: str) -> None:
        with self.lock:
            if epoch != self.epoch:
                return
            self.tts.send(epoch, response_id, piece_id, text)
            self.tts_pending += 1
        emit("tts.piece.queued", epoch=epoch, response_id=response_id, piece_id=piece_id, chars=len(text))

    def _conversation(self) -> None:
        while (item := self.q.get()) is not None:
            epoch, utterance_id, pcm = item
            with self.lock:
                stale = epoch != self.epoch
            if stale:
                emit("utterance.drop", epoch=epoch, utterance_id=utterance_id, reason="stale", bytes=len(pcm))
                continue
            duration = len(pcm) / (ASR_RATE * 4)
            audio = np.frombuffer(pcm, dtype="<f4")
            wav = wav_bytes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
            started = time.perf_counter()
            emit("asr.begin", epoch=epoch, utterance_id=utterance_id, input_s=round(duration, 3))
            prompt = transcribe(self.parakeet, wav)
            elapsed = time.perf_counter() - started
            with self.lock:
                stale = epoch != self.epoch
            emit("asr.done", epoch=epoch, utterance_id=utterance_id, accepted=not stale and bool(prompt), inference_ms=round(elapsed * 1000), rtf=round(elapsed / duration, 3), chars=len(prompt), text=prompt)
            if stale or not prompt:
                continue
            with self.lock:
                self.response_id += 1
                response_id = self.response_id
                self.llm_active = True
            started, first, raw, piece_id = time.perf_counter(), True, "", 0
            emit("llm.begin", epoch=epoch, utterance_id=utterance_id, response_id=response_id, chars=len(prompt))
            segmenter = Segmenter()
            messages = [{"role": "system", "content": self.settings["system_prompt"].strip()}, *self.history, {"role": "user", "content": prompt}]
            stream = gemma_stream(self.gemma, messages)
            try:
                for delta in stream:
                    with self.lock:
                        stale = epoch != self.epoch
                    if stale:
                        break
                    if first:
                        emit("llm.first", epoch=epoch, utterance_id=utterance_id, response_id=response_id, latency_ms=round((time.perf_counter() - started) * 1000))
                        first = False
                    raw += delta
                    for unit in segmenter.take(spoken(raw)):
                        piece_id += 1
                        self._send(epoch, response_id, piece_id, unit)
            finally:
                stream.close()
            with self.lock:
                stale = epoch != self.epoch
            if stale:
                emit("llm.cancel", epoch=epoch, utterance_id=utterance_id, response_id=response_id, elapsed_ms=round((time.perf_counter() - started) * 1000))
                continue
            answer = spoken(raw)
            for unit in segmenter.take(answer, True):
                piece_id += 1
                self._send(epoch, response_id, piece_id, unit)
            if answer:
                self.history.extend(({"role": "user", "content": prompt}, {"role": "assistant", "content": answer}))
            with self.lock:
                self.llm_active = False
            emit("llm.done", epoch=epoch, utterance_id=utterance_id, response_id=response_id, empty=not answer, elapsed_ms=round((time.perf_counter() - started) * 1000), chars=len(answer), pieces=piece_id, text=answer)

    def _pcm(self) -> None:
        while (frame := self.tts.recv_frame()) is not None:
            kind, epoch, response_id, piece_id, chunk_id, payload = frame
            if kind == 2:
                with self.lock:
                    accepted = epoch == self.epoch and self.speaker.put(epoch, payload)
                event = "pcm.accept" if accepted else "pcm.drop"
                emit(event, epoch=epoch, response_id=response_id, piece_id=piece_id, chunk_id=chunk_id, bytes=len(payload), audio_ms=round(1000 * len(payload) / (TTS_RATE * 2)))
                if accepted and response_id not in self.first_pcm:
                    self.first_pcm.add(response_id)
                    emit("pcm.first", epoch=epoch, response_id=response_id, piece_id=piece_id, chunk_id=chunk_id)
            elif kind == 0:
                with self.lock:
                    if epoch == self.epoch:
                        self.tts_pending -= 1
                emit("tts.piece.done", epoch=epoch, response_id=response_id, piece_id=piece_id, chunks=chunk_id + 1)
            elif kind == 1:
                raise RuntimeError(payload.decode("utf-8"))
            else:
                raise RuntimeError(f"TTS frame {kind}")

    def check(self) -> None:
        self.capture.check()
        self.speaker.check()
        raise_worker_failure()

    def stop(self) -> None:
        if not self.active:
            return
        emit("shutdown.begin", epoch=self.epoch)
        self.active = False
        self._advance("shutdown")
        self.capture.close()
        self.q.put(None)
        self.conversation_thread.join()
        self.tts.close()
        self.pcm_thread.join()
        self.speaker.close()
        emit("audio.close")
        emit("shutdown.done", epoch=self.epoch)

def launch(paths: Paths, family: str = "nano", language: str = "en") -> None:
    conversation = None
    try:
        boot(paths, family, language)
        conversation = Conversation(paths, load_settings(paths.data_dir))
        conversation.start()
        emit("console.ready", family=family, language=language, stop="Ctrl+C")
        while True:
            conversation.check()
            check_residents()
            wait_workers(.05)
    except KeyboardInterrupt:
        emit("console.interrupt")
    finally:
        try:
            if conversation is not None:
                conversation.stop()
        finally:
            stop_all()
