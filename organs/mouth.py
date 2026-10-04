"""Chatterbox turbo turns English text into samples for the Telegram call.

The model is loaded once, on the GPU, and the voice is reference.wav.
pieces() yields float32 mono at the model rate. The first piece is yielded once about ten seconds exist.
pcm48() is signed 16-bit mono at 48 kHz.
"""

import re
import time
import unicodedata

import numpy as np

from organs import CONFIG, log, path_of

LOG = log("mouth")
CFG = CONFIG["mouth"]
TAGS = re.compile(r"\[(laugh|chuckle|sigh|gasp|cough|clear throat|sniff|groan)\]")


def speakable(text: str) -> str:
    """Letters, digits, punctuation, and bracketed tags, kept as ASCII."""
    kept = TAGS.sub(lambda m: f" <{m.group(1)}> ", text)
    kept = unicodedata.normalize("NFKD", kept).encode("ascii", "ignore").decode("ascii")
    kept = re.sub(r"<([a-z ]+)>", r"[\1]", kept)
    return " ".join(kept.split())


class Mouth:
    def __init__(self):
        self.model = None
        self.sr = 24000

    def load(self):
        if self.model is not None:
            return
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        LOG.info("loading chatterbox turbo on %s", CFG["device"])
        self.model = ChatterboxTurboTTS.from_pretrained(CFG["device"])
        self.model.prepare_conditionals(str(path_of("mouth", "reference")))
        self.sr = int(self.model.sr)
        LOG.info("mouth ready at %d Hz", self.sr)

    def _wav(self, text: str) -> np.ndarray:
        self.load()
        return self.model.generate(text).squeeze().cpu().numpy().astype(np.float32)

    def pieces(self, text: str):
        words = speakable(text).split()
        parts, index, step = [], 0, 37
        while index < len(words) and sum(map(len, parts)) < 10 * self.sr:
            step = min(step, len(words) - index)
            parts.append(self._wav(" ".join(words[index:index + step])))
            index += step
            have = sum(map(len, parts))
            step = max(1, round((10 * self.sr - have) * index / have))
        chunk = np.concatenate(parts) if parts else self._wav(speakable(text))
        LOG.info("chunk %.2fs %.3f", len(chunk) / self.sr, time.time())
        yield chunk
        if index < len(words):
            rest = self._wav(" ".join(words[index:]))
            LOG.info("synth %.2fs %.3f", (len(chunk) + len(rest)) / self.sr, time.time())
            yield rest


def pcm48(samples: np.ndarray, rate: int) -> bytes:
    """Signed 16-bit mono at 48 kHz, resampled from float samples at the given rate, for the call."""
    if rate != 48000:
        count = int(len(samples) * 48000 / rate)
        samples = np.interp(np.linspace(0, len(samples) - 1, count), np.arange(len(samples)), samples)
    return (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()
