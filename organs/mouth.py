"""Chatterbox turbo turns English text into samples for the Telegram call.

The model is loaded once, on the CPU from config, and the voice is reference.wav.
say() returns float32 mono at the model rate. pcm48() is signed 16-bit mono at 48 kHz.
"""

import re
import unicodedata

import numpy as np

from organs import CONFIG, log, path_of

LOG = log("mouth")
CFG = CONFIG["mouth"]
TAGS = re.compile(r"\[(laugh|chuckle|sigh|gasp|cough|clear throat|sniff|groan)\]")


def speakable(text: str) -> str:
    """Letters, digits, punctuation, and nano's bracketed tags, kept as ASCII. Other characters are removed."""
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

    def say(self, text: str) -> np.ndarray:
        """Float32 mono of this sentence, at self.sr."""
        self.load()
        wav = self.model.generate(speakable(text))
        return wav.squeeze().cpu().numpy().astype(np.float32)


def pcm48(samples: np.ndarray, rate: int) -> bytes:
    """Signed 16-bit mono at 48 kHz, resampled from float samples at the given rate, for the call."""
    if rate != 48000:
        count = int(len(samples) * 48000 / rate)
        samples = np.interp(np.linspace(0, len(samples) - 1, count), np.arange(len(samples)), samples)
    return (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()
