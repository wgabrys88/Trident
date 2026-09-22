import sys
from pathlib import Path
import torch
from install import MODELS, reexec
from settings import EAR
LINES = MODELS / "ear" / "lines.txt"
class Ear:
    def __init__(self):
        from transformers import AutoModelForRNNT, AutoProcessor
        home = MODELS / "ear" / EAR["dir"]
        missing = [home / name for name in EAR["files"] if not (home / name).is_file()]
        if missing:
            raise RuntimeError("missing " + str(missing[0]))
        torch.set_num_threads(EAR["threads"])
        self.processor = AutoProcessor.from_pretrained(home)
        self.processor.set_num_lookahead_tokens(EAR["lookahead"])
        self.model = AutoModelForRNNT.from_pretrained(home)
        self.model.eval()
    def transcribe(self, audio):
        inputs = self.processor(audio, sampling_rate=EAR["sample_rate"], language=EAR["language"])
        with torch.inference_mode():
            output = self.model.generate(**inputs, return_dict_in_generate=True)
        text = self.processor.batch_decode(output.sequences, skip_special_tokens=True)
        return (text[0] if text else "").strip()
    def hear(self, path):
        from transformers.audio_utils import load_audio
        file = Path(path)
        if not file.is_file():
            raise RuntimeError("missing " + str(file))
        text = self.transcribe(load_audio(str(file), sampling_rate=EAR["sample_rate"], backend="librosa"))
        if not text:
            raise RuntimeError("ear heard nothing")
        LINES.parent.mkdir(parents=True, exist_ok=True)
        with LINES.open("a", encoding="utf-8") as out:
            out.write(text + "\n")
            out.flush()
        print(text, flush=True)
if __name__ == "__main__":
    reexec()
    argv = sys.argv
    if len(argv) != 2 or not argv[1]:
        raise SystemExit("usage: python asr.py <wav>")
    Ear().hear(argv[1])
