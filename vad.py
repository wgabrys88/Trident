from __future__ import annotations

import numpy as np
from silero_vad_notorch import VADIterator, load_silero_vad

from config import ASR_RATE, LIVE_AUDIO


class SileroEndpoint:
    def __init__(self, threshold: float, silence_ms: int) -> None:
        self.model = load_silero_vad(onnx=True)
        self.iterator = VADIterator(
            self.model,
            threshold=float(threshold),
            sampling_rate=ASR_RATE,
            min_silence_duration_ms=int(silence_ms),
            speech_pad_ms=0,
        )
        self.buffer = np.empty(0, dtype=np.float32)

    def feed(self, pcm_f32: bytes) -> bool:
        if not pcm_f32:
            return False
        self.buffer = np.concatenate((self.buffer, np.frombuffer(pcm_f32, dtype="<f4")))
        ended = False
        frame = int(LIVE_AUDIO["vad_frame_samples"])
        while self.buffer.size >= frame:
            event = self.iterator(self.buffer[:frame])
            self.buffer = self.buffer[frame:]
            if event and "end" in event:
                ended = True
        return ended

    def reset(self) -> None:
        self.iterator.reset_states()
        self.buffer = np.empty(0, dtype=np.float32)
