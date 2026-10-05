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
def pcm48(samples: np.ndarray, rate: int) -> bytes:
    if rate != 48000:
        count = int(len(samples) * 48000 / rate)
        samples = np.interp(np.linspace(0, len(samples) - 1, count), np.arange(len(samples)), samples)
    return (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()
if __name__ == "__main__":
    from chatterbox.tts_turbo import ChatterboxTurboTTS
    model = ChatterboxTurboTTS.from_pretrained(CFG["device"])
    model.prepare_conditionals(str(path_of("mouth", "reference")))
    rate = int(model.sr)
    words = speakable(sys.stdin.read()).split()
    clips = []
    index = 0
    while index < len(words):
        clips.append(model.generate(" ".join(words[index:index + 40])).squeeze().cpu().numpy().astype(np.float32))
        index += 40
    if clips:
        sys.stdout.buffer.write(pcm48(np.concatenate(clips), rate))
