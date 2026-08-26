from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort
from silero_vad_notorch import VADIterator, load_silero_vad

from config import ASR_RATE, LIVE_AUDIO, SMART_TURN_SECONDS


class SileroEndpoint:
    def __init__(self, threshold: float, silence_ms: int) -> None:
        self.model = load_silero_vad(onnx=True)
        self.configure(threshold, silence_ms)

    def configure(self, threshold: float, silence_ms: int) -> None:
        self.iterator = VADIterator(
            self.model,
            threshold=float(threshold),
            sampling_rate=ASR_RATE,
            min_silence_duration_ms=int(silence_ms),
            speech_pad_ms=0,
        )
        self.buffer = np.empty(0, dtype=np.float32)
        self.speech = False

    def feed(self, pcm_f32: bytes) -> tuple[bool, bool]:
        if not pcm_f32:
            return False, False
        self.buffer = np.concatenate((self.buffer, np.frombuffer(pcm_f32, dtype="<f4")))
        started = ended = False
        frame = int(LIVE_AUDIO["vad_frame_samples"])
        while self.buffer.size >= frame:
            event = self.iterator(self.buffer[:frame])
            self.buffer = self.buffer[frame:]
            if event:
                if "start" in event:
                    self.speech = True
                    started = True
                if "end" in event:
                    ended = True
        return started, ended

    def reset(self) -> None:
        self.iterator.reset_states()
        self.buffer = np.empty(0, dtype=np.float32)
        self.speech = False


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
    filters *= 2.0 / (centers[2:] - centers[:-2])
    return filters


_SMART_TURN_FILTERS = _mel_filters()
_SMART_TURN_WINDOW = np.hanning(401)[:-1]
_SMART_TURN_SAMPLES = SMART_TURN_SECONDS * ASR_RATE


def _smart_turn_features(audio: np.ndarray) -> np.ndarray:
    if audio.size > _SMART_TURN_SAMPLES:
        audio = audio[-_SMART_TURN_SAMPLES:]
    elif audio.size < _SMART_TURN_SAMPLES:
        audio = np.pad(audio, (_SMART_TURN_SAMPLES - audio.size, 0))
    audio = audio.astype(np.float32, copy=False)
    audio = (audio - audio.mean()) / np.sqrt(audio.var() + 1e-7)
    padded = np.pad(audio, (200, 200), mode="reflect")
    frames = np.lib.stride_tricks.sliding_window_view(padded, 400)[::160]
    power = np.abs(np.fft.rfft(frames * _SMART_TURN_WINDOW, n=400, axis=1)) ** 2
    mel = np.maximum(power @ _SMART_TURN_FILTERS, 1e-10)
    log_spec = np.log10(mel).T[:, :-1]
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    return ((log_spec + 4.0) / 4.0).astype(np.float32)[None, :, :]


class SmartTurnEndpoint:
    def __init__(self, model: Path) -> None:
        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            str(model.resolve()),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )

    def complete(self, pcm_f32: bytes) -> tuple[bool, float]:
        if not pcm_f32:
            return False, 0.0
        audio = np.frombuffer(pcm_f32, dtype="<f4")
        probability = float(self.session.run(None, {"input_features": _smart_turn_features(audio)})[0][0].item())
        return probability > 0.5, probability
