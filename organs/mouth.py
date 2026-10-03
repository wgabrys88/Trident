"""Mouth: Chatterbox nano. English text in, 24 kHz float audio out.

The model loads once (110M parameters, CPU) and stays. Gemma keeps the GPU.
Speech is produced with Wojciech's reference voice (reference.wav).

Run alone:  python -m organs.mouth "Hello, I am up."   -> plays it on the speakers
"""

import re
import sys
import unicodedata

import numpy as np
import sounddevice as sd

from organs import CONFIG, log, path_of

LOG = log("mouth")
CFG = CONFIG["mouth"]
TAGS = re.compile(r"\[(laugh|chuckle|sigh|gasp|cough|clear throat|sniff|groan)\]")


def speakable(text: str) -> str:
    """Nano is English-only: keep ASCII letters, digits, punctuation and its paralinguistic tags."""
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

        LOG.info("loading chatterbox nano on %s", CFG["device"])
        self.model = ChatterboxTurboTTS.from_pretrained(device=CFG["device"], nano=True)
        self.model.prepare_conditionals(str(path_of("mouth", "reference")))
        self.sr = int(self.model.sr)
        LOG.info("mouth ready at %d Hz", self.sr)

    def say(self, text: str) -> np.ndarray:
        """-> float32 mono samples at self.sr."""
        self.load()
        wav = self.model.generate(speakable(text))
        return wav.squeeze().cpu().numpy().astype(np.float32)

    def play(self, samples: np.ndarray):
        device = None if CFG["speaker"] == "default" else next(i for i, d in enumerate(sd.query_devices()) if d["max_output_channels"] > 0 and CFG["speaker"].lower() in d["name"].lower())
        sd.play(samples, self.sr, device=device, blocking=True)


def pcm48(samples: np.ndarray, rate: int) -> bytes:
    """float mono at any rate -> signed 16-bit mono 48 kHz, what the phone line sends."""
    if rate != 48000:
        count = int(len(samples) * 48000 / rate)
        samples = np.interp(np.linspace(0, len(samples) - 1, count), np.arange(len(samples)), samples)
    return (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()


if __name__ == "__main__":
    mouth = Mouth()
    mouth.play(mouth.say(" ".join(sys.argv[1:]) or "Hello Wojciech, I am up. Do you want anything?"))
