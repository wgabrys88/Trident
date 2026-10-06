import io
import wave
from collections import deque

import numpy as np

from store import CONFIG


def wav(pcm, rate):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(pcm)
    return stream.getvalue()


def call_pcm(payload):
    with wave.open(io.BytesIO(payload), "rb") as source:
        if source.getsampwidth() != 2:
            raise ValueError("Voice output must be PCM16 WAV")
        rate = source.getframerate()
        channels = source.getnchannels()
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32)
    samples = samples.reshape(-1, channels).mean(axis=1)
    positions = np.arange(round(len(samples) * 48000 / rate)) * rate / 48000
    return np.rint(np.interp(positions, np.arange(len(samples)), samples)).astype("<i2").tobytes()


class Utterances:
    def __init__(self):
        self.cfg = CONFIG["ears"]
        self.reset()

    def reset(self):
        self.pending = b""
        self.lead = deque(maxlen=self.cfg["padding_ms"] // 20)
        self.parts = []
        self.quiet = 0

    def feed(self, pcm):
        self.pending += pcm
        while len(self.pending) >= 640:
            frame, self.pending = self.pending[:640], self.pending[640:]
            samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768
            voiced = np.sqrt(np.mean(samples * samples)) >= self.cfg["rms_threshold"]
            if not self.parts:
                self.lead.append(frame)
                if voiced:
                    self.parts = list(self.lead)
                    self.lead.clear()
            else:
                self.parts.append(frame)
                self.quiet = 0 if voiced else self.quiet + 20
                if self.quiet >= self.cfg["silence_ms"] or len(self.parts) * 0.02 >= self.cfg["utterance_seconds"]:
                    yield b"".join(self.parts)
                    self.parts = []
                    self.quiet = 0

    def finish(self):
        result = b"".join(self.parts) + self.pending if self.parts else b""
        self.reset()
        return result
