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

from config import ASR_RATE, SMART_TURN_FILE, Paths, TTS_KNOBS, TTS_RATE, VAD_FRAME, emit, load_settings, raise_worker_failure, run_file, transcript, wait_workers
from runtime import Chatterbox, boot, check_residents, gemma_stream, require_alive, stop_all, transcribe

STOP_PHRASES = {
    "stop", "stop speaking", "please stop", "that's enough", "thats enough",
    "quiet", "silence", "przestan", "przestań", "przestan mowic", "przestań mówić",
    "dosyc", "dość", "cicho", "milcz",
}
BACKCHANNELS = {
    "yeah", "yes", "yep", "yup", "ok", "okay", "mhm", "mm", "uh", "um", "aha",
    "uh huh", "huh", "right", "sure", "tak", "no", "nie", "okej",
}

def spoken(text: str) -> str:
    text = text.replace("\r", "").strip()
    marker = "Assistant:\n"
    return text.rsplit(marker, 1)[-1].strip() if marker in text else text

def folded_utterance(text: str) -> str:
    out = []
    for ch in text.casefold().replace("\r", " ").replace("\n", " "):
        out.append(ch if ch.isalnum() or ch.isspace() else " ")
    return " ".join("".join(out).split())

def classify_utterance(text: str) -> tuple[str, str, bool]:
    folded = folded_utterance(text)
    if folded in STOP_PHRASES:
        return "stop", folded, False
    if folded in BACKCHANNELS:
        return "backchannel", folded, False
    return "request", folded, bool(folded) and len(folded) <= 3 and folded.isalpha()

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
        out, hard = [], TTS_KNOBS["chars"]
        while self.sent < len(text):
            pending, cut = text[self.sent:], 0
            for i in range(min(len(pending), hard)):
                if pending[i] in ".?!" and (i + 1 == len(pending) or pending[i + 1].isspace()):
                    cut = i + 1
                    break
            if not cut and len(pending) >= hard:
                split = max(pending.rfind(" ", 0, hard), pending.rfind("\n", 0, hard), pending.rfind("\t", 0, hard))
                cut = split + 1 if split >= 0 else hard
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
        self.drains: queue.SimpleQueue = queue.SimpleQueue()
        self.offset = self.epoch = 0
        self.last_drained_ns = 0
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
            if written and not self.pcm:
                observed = time.perf_counter_ns()
                self.last_drained_ns = observed
                self.drains.put((self.epoch, observed))

    def open(self) -> None:
        self.stream = sd.RawOutputStream(samplerate=TTS_RATE, blocksize=VAD_FRAME, channels=1, dtype="int16", latency="low", callback=self._callback)
        self.stream.start()

    def put(self, epoch: int, pcm: bytes) -> tuple[bool, int]:
        with self.lock:
            if epoch != self.epoch:
                return False, 0
            resume_from = self.last_drained_ns if not self.pcm else 0
            self.pcm.append(pcm)
            return True, resume_from

    def busy(self) -> bool:
        with self.lock:
            return bool(self.pcm)

    def cancel(self, epoch: int) -> int:
        with self.lock:
            dropped = sum(map(len, self.pcm)) - (self.offset if self.pcm else 0)
            self.pcm.clear()
            self.offset = 0
            self.epoch = epoch
            self.last_drained_ns = 0
            self.silence = (epoch, time.perf_counter_ns())
            return dropped

    def check(self) -> None:
        if self.error is not None:
            raise self.error
        while not self.events.empty():
            epoch, requested, observed = self.events.get()
            emit("audio.silent", epoch=epoch, latency_ms=round((observed - requested) / 1e6, 3), callback_ns=observed)
        while not self.drains.empty():
            epoch, observed = self.drains.get()
            emit("audio.drained", epoch=epoch, callback_ns=observed, dispatch_delay_ms=round((time.perf_counter_ns() - observed) / 1e6, 3))

    def close(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None

class Capture:
    def __init__(self, model, settings: dict, on_start, on_utterance) -> None:
        self.on_start, self.on_utterance = on_start, on_utterance
        self.candidate_silence_ms = int(settings["candidate_silence_ms"])
        self.vad = VADIterator(load_silero_vad(onnx=True), threshold=.5, sampling_rate=ASR_RATE, min_silence_duration_ms=self.candidate_silence_ms, speech_pad_ms=0)
        self.smart = SmartTurn(model, settings["completion_threshold"], settings["acoustic_context_seconds"])
        self.context_seconds = float(settings["acoustic_context_seconds"])
        self.q: queue.SimpleQueue = queue.SimpleQueue()
        self.decisions: queue.SimpleQueue = queue.SimpleQueue()
        self.audio = bytearray()
        self.state_lock = threading.Lock()
        self.thread = threading.Thread(target=self._loop, name="vad")
        self.decision_thread = threading.Thread(target=self._decide, name="smart-turn")
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
        self.decision_thread.start()
        self.thread.start()
        self.stream = sd.RawInputStream(samplerate=ASR_RATE, blocksize=VAD_FRAME, channels=1, dtype="float32", latency="low", callback=self._callback)
        self.stream.start()
        emit("capture.open")

    def _decide(self) -> None:
        emit("worker.start", worker="smart-turn")
        while (item := self.decisions.get()) is not None:
            epoch, utterance_id, audio = item
            started = time.perf_counter(); complete, probability = self.smart.decide(audio)
            elapsed = (time.perf_counter() - started) * 1000
            emit("turn.decision", epoch=epoch, utterance_id=utterance_id, complete=complete, probability=round(probability, 6), decision_ms=round(elapsed, 3), context_s=self.context_seconds, input_s=round(len(audio) / (ASR_RATE * 4), 3))
            with self.state_lock:
                if complete and self.utterance and epoch == self.epoch and utterance_id == self.utterance_id:
                    audio = bytes(self.audio)
                    self.audio.clear()
                    self.utterance = False
                    self.vad.reset_states()
                else:
                    audio = b""
            if audio:
                emit("utterance.complete", epoch=epoch, utterance_id=utterance_id, input_s=round(len(audio) / (ASR_RATE * 4), 3))
                self.on_utterance(epoch, utterance_id, audio)
        emit("worker.stop", worker="smart-turn")

    def _loop(self) -> None:
        emit("worker.start", worker="vad")
        while (pcm := self.q.get()) is not None:
            with self.state_lock:
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
                    emit("vad.end_candidate", epoch=self.epoch, utterance_id=self.utterance_id, vad_sample=int(event["end"]), candidate_silence_ms=self.candidate_silence_ms, input_s=round(len(self.audio) / (ASR_RATE * 4), 3))
                    self.decisions.put((self.epoch, self.utterance_id, bytes(self.audio)))
        self.decisions.put(None)
        self.decision_thread.join()
        if self.audio:
            emit("utterance.drop", epoch=self.epoch, utterance_id=self.utterance_id, reason="shutdown", bytes=len(self.audio))
        self.audio.clear()
        emit("worker.stop", worker="vad")

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

class Synthesis:
    def __init__(self) -> None:
        require_alive("chatterbox")
        self.epoch = self.response_id = 0
        self.lock = threading.Lock()
        self.tts = Chatterbox()
        self.speaker = Speaker()
        self.pcm_thread = threading.Thread(target=self._pcm, name="pcm")
        self.llm_active = False
        self.tts_pending = 0
        self.first_pcm: set[int] = set()
        self._queue_ns = 0
        self._queue_response = 0
        self.active = False

    def _start_output(self) -> None:
        self.speaker.open()
        self.tts.open()
        self.pcm_thread.start()
        self.active = True

    def _advance(self, reason: str, utterance_id: int = 0) -> int:
        with self.lock:
            llm_active, tts_pending, speaker_busy = self.llm_active, self.tts_pending, self.speaker.busy()
            interrupted = llm_active or tts_pending > 0 or speaker_busy
            self.epoch += 1
            epoch = self.epoch
            self.llm_active = False
            self.tts_pending = 0
            dropped = self.speaker.cancel(epoch)
            self.tts.send(epoch, 0, 0)
        emit("epoch.advance", epoch=epoch, reason=reason, utterance_id=utterance_id, interrupted=interrupted, llm_active=llm_active, tts_pending=tts_pending, speaker_busy=speaker_busy)
        emit("audio.cancel", epoch=epoch, dropped_bytes=dropped, audio_ms=round(1000 * dropped / (TTS_RATE * 2)))
        if reason == "speech" and interrupted:
            emit("barge_in", epoch=epoch, utterance_id=utterance_id, llm_active=llm_active, tts_pending=tts_pending, speaker_busy=speaker_busy)
        return epoch

    def _speech_start(self, utterance_id: int) -> int:
        return self._advance("speech", utterance_id)

    def _send(self, epoch: int, response_id: int, piece_id: int, text: str) -> None:
        with self.lock:
            if epoch != self.epoch:
                return
            self.tts.send(epoch, response_id, piece_id, text)
            self.tts_pending += 1
            pending = self.tts_pending
            now = time.perf_counter_ns()
            gap = round((now - self._queue_ns) / 1e6, 3) if self._queue_ns and self._queue_response == response_id else None
            self._queue_ns, self._queue_response = now, response_id
        emit("tts.piece.queued", epoch=epoch, response_id=response_id, piece_id=piece_id, chars=len(text), gap_ms=gap, tts_pending=pending, text=text)

    def _pcm(self) -> None:
        emit("worker.start", worker="pcm")
        while (frame := self.tts.recv_frame()) is not None:
            kind, epoch, response_id, piece_id, chunk_id, payload = frame
            if kind == 2:
                received = time.perf_counter_ns()
                with self.lock:
                    live_epoch, llm_active = self.epoch, self.llm_active
                    accepted, resume_from = self.speaker.put(epoch, payload) if epoch == live_epoch else (False, 0)
                    first = accepted and response_id not in self.first_pcm
                    if first:
                        self.first_pcm.add(response_id)
                    pending = self.tts_pending
                audio_ms = round(1000 * len(payload) / (TTS_RATE * 2))
                if not accepted:
                    emit("pcm.drop", epoch=epoch, live_epoch=live_epoch, response_id=response_id, piece_id=piece_id, chunk_id=chunk_id, bytes=len(payload), audio_ms=audio_ms)
                elif first:
                    emit("pcm.first", epoch=epoch, response_id=response_id, piece_id=piece_id, chunk_id=chunk_id, bytes=len(payload), audio_ms=audio_ms, received_ns=received, llm_active=llm_active, tts_pending=pending)
                elif resume_from:
                    emit("pcm.resume", epoch=epoch, response_id=response_id, piece_id=piece_id, chunk_id=chunk_id, bytes=len(payload), audio_ms=audio_ms, starvation_ms=round((received - resume_from) / 1e6, 3), drained_ns=resume_from, received_ns=received, llm_active=llm_active, tts_pending=pending)
            elif kind == 0:
                with self.lock:
                    live_epoch = self.epoch
                    accepted = epoch == live_epoch
                    if accepted:
                        self.tts_pending -= 1
                    pending = self.tts_pending
                emit("tts.piece.ack", epoch=epoch, live_epoch=live_epoch, response_id=response_id, piece_id=piece_id, batch_chunks=chunk_id + 1, accepted=accepted, tts_pending=pending)
            elif kind == 1:
                raise RuntimeError(payload.decode("utf-8"))
            else:
                raise RuntimeError(f"TTS frame {kind}")
        emit("worker.stop", worker="pcm")

    def check(self) -> None:
        self.speaker.check()
        raise_worker_failure()

    def _stop_output(self) -> None:
        self.tts.close()
        self.pcm_thread.join()
        self.speaker.close()

class Conversation(Synthesis):
    def __init__(self, paths: Paths, settings: dict) -> None:
        super().__init__()
        self.settings = settings
        self.parakeet, self.gemma = require_alive("parakeet"), require_alive("gemma")
        self.history: list[dict[str, str]] = []
        self.q: queue.SimpleQueue = queue.SimpleQueue()
        self.capture = Capture(paths.models_dir / SMART_TURN_FILE, settings, self._speech_start, self._utterance)
        self.conversation_thread = threading.Thread(target=self._conversation, name="conversation")

    def start(self) -> None:
        transcript("user", ""); transcript("assistant", "")
        emit("transcript.open", user=run_file("transcript-user", "txt").name, assistant=run_file("transcript-assistant", "txt").name)
        self._start_output()
        self.conversation_thread.start()
        self.capture.open()
        emit("audio.open", input=str(sd.query_devices(kind="input")["name"]), output=str(sd.query_devices(kind="output")["name"]), input_rate=ASR_RATE, output_rate=TTS_RATE, output_block=VAD_FRAME)

    def _utterance(self, epoch: int, utterance_id: int, pcm: bytes) -> None:
        if self.active:
            queued = time.perf_counter_ns()
            self.q.put((epoch, utterance_id, pcm, queued))
            emit("utterance.queued", epoch=epoch, utterance_id=utterance_id, bytes=len(pcm))

    def _conversation(self) -> None:
        emit("worker.start", worker="conversation")
        while (item := self.q.get()) is not None:
            epoch, utterance_id, pcm, queued = item
            dequeued = time.perf_counter_ns()
            queue_ms = round((dequeued - queued) / 1e6, 3)
            emit("utterance.dequeued", epoch=epoch, utterance_id=utterance_id, queue_ms=queue_ms)
            with self.lock:
                stale = epoch != self.epoch
            if stale:
                emit("utterance.drop", epoch=epoch, utterance_id=utterance_id, reason="stale", bytes=len(pcm))
                continue
            duration = len(pcm) / (ASR_RATE * 4)
            asr_started = time.perf_counter()
            emit("asr.begin", epoch=epoch, utterance_id=utterance_id, input_s=round(duration, 3), queue_ms=queue_ms)
            prepared = time.perf_counter()
            audio = np.frombuffer(pcm, dtype="<f4")
            wav = wav_bytes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
            prepare_ms = round((time.perf_counter() - prepared) * 1000, 3)
            emit("asr.request", epoch=epoch, utterance_id=utterance_id, prepare_ms=prepare_ms, wav_bytes=len(wav))
            request_started = time.perf_counter()
            prompt = transcribe(self.parakeet, wav)
            roundtrip = time.perf_counter() - request_started
            total = time.perf_counter() - asr_started
            with self.lock:
                stale = epoch != self.epoch
            emit("asr.done", epoch=epoch, utterance_id=utterance_id, accepted=not stale and bool(prompt), roundtrip_ms=round(roundtrip * 1000, 3), total_ms=round(total * 1000, 3), roundtrip_rtf=round(roundtrip / duration, 3), total_rtf=round(total / duration, 3), chars=len(prompt), text=prompt)
            if prompt:
                transcript("user", prompt); print(f"\nuser: {prompt}", flush=True)
            if stale or not prompt:
                continue
            intent, folded, short_alpha = classify_utterance(prompt)
            emit("asr.intent", epoch=epoch, utterance_id=utterance_id, intent=intent, folded=folded, short_alpha=short_alpha, routed="gemma")
            with self.lock:
                self.response_id += 1
                response_id = self.response_id
                self.llm_active = True
            started, first, raw, piece_id = time.perf_counter(), True, "", 0
            emit("llm.begin", epoch=epoch, utterance_id=utterance_id, response_id=response_id, chars=len(prompt))
            print("assistant: ", end="", flush=True)
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
                    transcript("assistant", delta); print(delta, end="", flush=True)
                    raw += delta
                    for unit in segmenter.take(spoken(raw)):
                        piece_id += 1
                        self._send(epoch, response_id, piece_id, unit)
            finally:
                stream.close()
            print(flush=True)
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
                heard = response_id in self.first_pcm
            emit("llm.done", epoch=epoch, utterance_id=utterance_id, response_id=response_id, empty=not answer, elapsed_ms=round((time.perf_counter() - started) * 1000), chars=len(answer), pieces=piece_id, pcm_first=heard, text=answer)
        emit("worker.stop", worker="conversation")

    def check(self) -> None:
        self.capture.check()
        super().check()

    def stop(self) -> None:
        if not self.active:
            return
        emit("shutdown.begin", epoch=self.epoch)
        self.active = False
        self._advance("shutdown")
        self.capture.close()
        self.q.put(None)
        self.conversation_thread.join()
        self._stop_output()
        emit("audio.close")
        emit("shutdown.done", epoch=self.epoch)

class TTSMode(Synthesis):
    def __init__(self, primary: str, replacement: str | None, interrupt_after: float | None) -> None:
        super().__init__()
        self.primary, self.replacement = primary, replacement
        self.interrupt_after = interrupt_after
        self.ready_ns = 0
        self.started_ns = 0

    def _input(self, text: str, source: str, injected_ns: int) -> None:
        units = Segmenter().take(text, True)
        with self.lock:
            self.response_id += 1
            epoch, response_id = self.epoch, self.response_id
        emit("tts.input", source=source, epoch=epoch, response_id=response_id, injected_ns=injected_ns, after_ready_ms=round((injected_ns - self.ready_ns) / 1e6, 3), chars=len(text), pieces=len(units), text=text)
        for piece_id, unit in enumerate(units, 1):
            self._send(epoch, response_id, piece_id, unit)

    def start(self) -> None:
        self._start_output()
        self.ready_ns = self.started_ns = time.perf_counter_ns()
        output = str(sd.query_devices(kind="output")["name"])
        emit("audio.open", output=output, output_rate=TTS_RATE, output_block=VAD_FRAME)
        emit("tts.mode.ready", ready_ns=self.ready_ns, output=output)
        print("trident.ready", flush=True)
        self._input(self.primary, "primary", self.ready_ns)

    def run(self) -> None:
        requested_ns = self.ready_ns + round(self.interrupt_after * 1e9) if self.replacement is not None else 0
        interrupted = False
        while True:
            self.check()
            check_residents()
            now = time.perf_counter_ns()
            if self.replacement is not None and not interrupted and now >= requested_ns:
                observed_ns = now
                self._advance("tts.interrupt")
                emit("tts.interrupt", requested_after_s=self.interrupt_after, observed_after_s=round((observed_ns - self.ready_ns) / 1e9, 6), drift_ms=round((observed_ns - requested_ns) / 1e6, 3), ready_ns=self.ready_ns, requested_ns=requested_ns, observed_ns=observed_ns, epoch=self.epoch)
                self._input(self.replacement, "replacement", observed_ns)
                interrupted = True
            with self.lock:
                pending = self.tts_pending
            if pending == 0 and not self.speaker.busy() and (self.replacement is None or interrupted):
                self.speaker.check()
                emit("tts.complete", epoch=self.epoch, response_id=self.response_id, elapsed_ms=round((time.perf_counter_ns() - self.started_ns) / 1e6, 3))
                return
            wait_workers(.01)

    def stop(self) -> None:
        if not self.active:
            return
        emit("shutdown.begin", epoch=self.epoch)
        self.active = False
        self._advance("shutdown")
        self._stop_output()
        emit("audio.close")
        emit("shutdown.done", epoch=self.epoch)

def launch(paths: Paths, family: str = "nano", language: str = "en") -> None:
    conversation = None
    try:
        boot(paths, family, language)
        conversation = Conversation(paths, load_settings(paths.data_dir))
        conversation.start()
        emit("console.ready", family=family, language=language, stop="Ctrl+C")
        print("trident.ready", flush=True)
        while True:
            conversation.check()
            check_residents()
            wait_workers(.05)
    finally:
        try:
            if conversation is not None:
                conversation.stop()
        finally:
            stop_all()

def launch_tts(paths: Paths, family: str, language: str, primary: str, replacement: str | None, interrupt_after: float | None) -> None:
    mode = None
    try:
        boot(paths, family, language)
        mode = TTSMode(primary, replacement, interrupt_after)
        mode.start()
        mode.run()
    finally:
        try:
            if mode is not None:
                mode.stop()
        finally:
            stop_all()
