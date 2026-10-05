import re
import sys
import unicodedata

import numpy as np

from organs import CONFIG, path_of

CFG = CONFIG["mouth"]
TAGS = re.compile(r"\[(laugh|chuckle|sigh|gasp|cough|clear throat|sniff|groan)\]")


def speakable(text: str) -> str:
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

        self.model = ChatterboxTurboTTS.from_pretrained(CFG["device"])
        self.model.prepare_conditionals(str(path_of("mouth", "reference")))
        self.sr = int(self.model.sr)

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
        yield chunk
        if index < len(words):
            yield self._wav(" ".join(words[index:]))


def pcm48(samples: np.ndarray, rate: int) -> bytes:
    if rate != 48000:
        count = int(len(samples) * 48000 / rate)
        samples = np.interp(np.linspace(0, len(samples) - 1, count), np.arange(len(samples)), samples)
    return (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()


if __name__ == "__main__":
    mouth = Mouth()
    mouth.load()
    audio = list(mouth.pieces(sys.stdin.read()))
    sys.stdout.buffer.write(pcm48(np.concatenate(audio), mouth.sr))
