from config import ensure_venv
ensure_venv()
if __name__ == "__main__":
    from main import main
    raise SystemExit(main("asr"))

import io
import queue
import threading
import time
import wave
from pathlib import Path

import numpy as np
import onnxruntime as ort
import sounddevice as sd
from silero_vad_notorch import VADIterator, load_silero_vad

from config import ASR_RATE, SMART_TURN_FILE, VAD_FRAME, Paths, Wasapi, load_settings
from journal import finish_cleanup, join_or_fail
from runtime import CancelableHTTP, Residents

_EOF = object()


def pcm_i16(pcm: bytes) -> bytes:
    return (np.clip(np.frombuffer(pcm, dtype="<f4"), -1, 1) * 32767).astype("<i2").tobytes()


def load_pcm(path: Path, journal=None) -> bytes:
    def read(src: Path) -> tuple[int, int, int, bytes]:
        with wave.open(str(src), "rb") as handle:
            return handle.getframerate(), handle.getnchannels(), handle.getsampwidth(), handle.readframes(handle.getnframes())
    rate, channels, width, frames = read(path)
    if channels != 1 or width != 2 or rate != ASR_RATE:
        if journal is None:
            raise RuntimeError(f"wav must be 16 kHz mono s16: {path}")
        dest = journal.run_dir / f"{Path(path).stem}-16k.wav"
        rate, channels, width, frames = read(journal.resample(path, dest, ASR_RATE))
        if channels != 1 or width != 2 or rate != ASR_RATE:
            raise RuntimeError(f"ffmpeg did not produce 16 kHz mono s16: {dest}")
    return (np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32767.0).astype("<f4").tobytes()


def wav_bytes(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setparams((1, 2, ASR_RATE, 0, "NONE", "not compressed")); out.writeframes(pcm)
    return buf.getvalue()


def transcribe(base: str, pcm: bytes, channel: CancelableHTTP) -> str:
    import json, secrets
    wav = wav_bytes(pcm_i16(pcm))
    boundary = "----trident" + secrets.token_hex(8)
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"utterance.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode()
            + wav + f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nparakeet\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson\r\n--{boundary}--\r\n".encode())
    response = channel.open(base + "/v1/audio/transcriptions", body, {"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"})
    try: return str(json.loads(response.read()).get("text") or "").strip()
    finally: channel.clear(response)


def _mel_filters() -> np.ndarray:
    def hz_to_mel(hz):
        hz = np.asarray(hz, dtype=np.float64)
        mel, high = hz / (200.0 / 3.0), hz >= 1000.0
        mel[high] = 15.0 + np.log(hz[high] / 1000.0) / (np.log(6.4) / 27.0)
        return mel
    def mel_to_hz(mel):
        mel = np.asarray(mel, dtype=np.float64)
        return np.where(mel >= 15.0, 1000.0 * np.exp((np.log(6.4) / 27.0) * (mel - 15.0)), (200.0 / 3.0) * mel)
    centers = mel_to_hz(np.linspace(hz_to_mel([0.0])[0], hz_to_mel([8000.0])[0], 82))
    bins = np.linspace(0.0, ASR_RATE / 2.0, 201)
    return np.maximum(0.0, np.minimum((bins[:, None] - centers[:-2]) / (centers[1:-1] - centers[:-2]), (centers[2:] - bins[:, None]) / (centers[2:] - centers[1:-1]))) * 2.0 / (centers[2:] - centers[:-2])


_FILTERS = _mel_filters()
_WINDOW = np.hanning(401)[:-1]


class SmartTurn:
    def __init__(self, model: Path, threshold: float, context_seconds: float) -> None:
        options = ort.SessionOptions(); options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.inter_op_num_threads = options.intra_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
        self.threshold, self.samples = float(threshold), int(float(context_seconds) * ASR_RATE)

    def decide(self, pcm: bytes) -> tuple[bool, float]:
        audio = np.frombuffer(pcm, dtype="<f4")[-self.samples:]
        if audio.size < self.samples: audio = np.pad(audio, (self.samples - audio.size, 0))
        if audio.size: audio = (audio - audio.mean()) / np.sqrt(audio.var() + 1e-7)
        frames = np.lib.stride_tricks.sliding_window_view(np.pad(audio, (200, 200), mode="reflect"), 400)[::160]
        spec = np.log10(np.maximum(np.abs(np.fft.rfft(frames * _WINDOW, n=400, axis=1)) ** 2 @ _FILTERS, 1e-10)).T[:, :-1]
        probability = float(self.session.run(None, {"input_features": ((np.maximum(spec, spec.max() - 8.0) + 4.0) / 4.0).astype(np.float32)[None]})[0][0].item())
        return probability > self.threshold, probability


class Source(Wasapi):
    kind, component, ready_event, stop_event, rate_key, peer_rate = "input", "capture", "source.ready", "source.stopped", "capture_rate", ASR_RATE

    def __init__(self, frame_cb, paths: Paths) -> None:
        super().__init__(paths)
        self.frame_cb, self.pending = frame_cb, np.empty(0, dtype=np.float32)

    def _callback(self, indata, frames, _timing, status) -> None:
        if status:
            self.error = RuntimeError(f"WASAPI capture: {status}"); raise sd.CallbackAbort
        samples = np.frombuffer(indata, dtype="<f4", count=frames)
        self.pending = np.concatenate((self.pending, samples))
        while self.pending.size >= VAD_FRAME:
            self.frame_cb(self.pending[:VAD_FRAME]); self.pending = self.pending[VAD_FRAME:]


class Capture:
    def __init__(self, paths: Paths, settings: dict, on_start, on_utterance) -> None:
        self.paths, self.journal = paths, paths.journal; self.on_start, self.on_utterance = on_start, on_utterance
        self.vad = VADIterator(load_silero_vad(onnx=True), threshold=.5, sampling_rate=ASR_RATE, min_silence_duration_ms=int(settings["candidate_silence_ms"]), speech_pad_ms=0)
        self.smart = SmartTurn(paths.models_dir / SMART_TURN_FILE, settings["completion_threshold"], settings["acoustic_context_seconds"])
        self.frames: queue.SimpleQueue = queue.SimpleQueue(); self.decisions: queue.SimpleQueue = queue.SimpleQueue()
        self.audio = bytearray(); self.state_lock = threading.Lock(); self.active = self.utterance = False
        self.utterance_id = self.generation = self.accepted_turns = 0
        self.source = Source(self.frame, paths)
        self.vad_thread = self.decision_thread = None

    def frame(self, samples: np.ndarray) -> None:
        if self.active: self.frames.put(np.asarray(samples, dtype="<f4").tobytes())

    def open(self) -> None:
        self.active = True
        self.decision_thread = self.paths.supervisor.start("smart-turn", self._decide)
        self.vad_thread = self.paths.supervisor.start("vad", self._loop)
        self.source.open()

    def _decide(self) -> None:
        while (item := self.decisions.get()) is not _EOF:
            utterance_id, generation, audio = item
            started = time.perf_counter(); complete, probability = self.smart.decide(audio)
            self.journal.emit("smart-turn", "completed", utterance_id=utterance_id, candidate_generation=generation, complete=complete, probability=round(probability, 6), decision_ms=round((time.perf_counter() - started) * 1000, 3), input_s=round(len(audio) / (ASR_RATE * 4), 3))
            accepted = b""
            with self.state_lock:
                same = self.utterance and utterance_id == self.utterance_id and generation == self.generation
                if complete and same:
                    accepted = bytes(self.audio); self.audio.clear(); self.utterance = False; self.accepted_turns += 1; self.vad.reset_states()
                elif complete and not same:
                    self.journal.emit("smart-turn", "cancelled", utterance_id=utterance_id, candidate_generation=generation, reason="candidate-resumed-or-changed")
            if accepted:
                rel = f"utterances/{utterance_id}.wav"
                self.journal.wav(rel, pcm_i16(accepted), ASR_RATE)
                self.journal.emit("capture", "utterance.completed", utterance_id=utterance_id, input_s=round(len(accepted) / (ASR_RATE * 4), 3), wav=rel)
                self.on_utterance(utterance_id, accepted)

    def _loop(self) -> None:
        while (pcm := self.frames.get()) is not _EOF:
            with self.state_lock:
                event = self.vad(np.frombuffer(pcm, dtype="<f4")) or {}
                if "start" in event:
                    if not self.utterance:
                        self.audio.clear(); self.utterance = True; self.utterance_id += 1; self.generation += 1
                        fields = self.on_start(self.utterance_id) or {}
                        self.journal.emit("vad", "speech.started", utterance_id=self.utterance_id, candidate_generation=self.generation, **fields)
                    else:
                        self.generation += 1; self.journal.emit("vad", "speech.resumed", utterance_id=self.utterance_id, candidate_generation=self.generation)
                if self.utterance: self.audio.extend(pcm)
                if "end" in event and self.utterance:
                    generation, audio = self.generation, bytes(self.audio)
                    self.decisions.put((self.utterance_id, generation, audio))
                    self.journal.emit("vad", "candidate.queued", utterance_id=self.utterance_id, candidate_generation=generation, vad_sample=int(event["end"]), input_s=round(len(audio) / (ASR_RATE * 4), 3))
        self.decisions.put(_EOF)

    def check(self) -> None:
        self.source.check(); self.paths.supervisor.check()

    def close(self) -> None:
        if not self.active: return
        self.active = False; self.source.close(); self.frames.put(_EOF)
        join_or_fail(self.vad_thread, "vad"); join_or_fail(self.decision_thread, "smart-turn")


def launch(paths: Paths, family: str = "nano", language: str = "en", primary=None, replacement=None, interrupt_after=None) -> None:
    residents, capture, failure, http = Residents(paths), None, None, CancelableHTTP()
    try:
        residents.boot(family, language)
        base = residents.require_alive("parakeet")
        paths.journal.emit("main", "ready", family=family, language=language); print("trident.ready", flush=True)
        if paths.wavs:
            for index, src in enumerate(paths.wavs, 1):
                dest = paths.run_dir / src.name
                if dest.resolve() != src.resolve(): dest.write_bytes(src.read_bytes())
                pcm = load_pcm(dest if dest.is_file() else src, paths.journal)
                duration, started = len(pcm) / (ASR_RATE * 4), time.perf_counter()
                text = transcribe(base, pcm, http)
                total = time.perf_counter() - started
                paths.journal.emit("asr", "completed", utterance_id=index, accepted=bool(text), input_s=round(duration, 3), total_ms=round(total * 1000, 3), rtf=round(total / max(duration, 1e-9), 3), chars=len(text), text=text, wav=dest.name)
                if text:
                    paths.journal.transcript("user", text); print(f"user: {text}", flush=True)
        else:
            q: queue.SimpleQueue = queue.SimpleQueue()
            capture = Capture(paths, load_settings(paths.data_dir), lambda uid: None, lambda uid, pcm: q.put((uid, pcm, time.perf_counter())))
            capture.open()
            while True:
                capture.check(); residents.check()
                try: utterance_id, pcm, started = q.get(timeout=.05)
                except queue.Empty: continue
                duration = len(pcm) / (ASR_RATE * 4)
                text = transcribe(base, pcm, http)
                total = time.perf_counter() - started
                paths.journal.emit("asr", "completed", utterance_id=utterance_id, accepted=bool(text), input_s=round(duration, 3), total_ms=round(total * 1000, 3), rtf=round(total / max(duration, 1e-9), 3), chars=len(text), text=text, wav=f"utterances/{utterance_id}.wav")
                if text:
                    paths.journal.transcript("user", text); print(f"user: {text}", flush=True)
    except BaseException as error:
        failure = (error, error.__traceback__)
    http.close()
    finish_cleanup(paths, failure, ([("capture", capture.close)] if capture is not None else []) + [("residents", residents.stop), ("supervisor", lambda: paths.supervisor.join(1))])
