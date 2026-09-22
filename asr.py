import sys, time
from pathlib import Path
import torch
from install import MODELS, ROOT, reexec
from settings import EAR, WAV
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
    def watch(self):
        home = ROOT / WAV
        seen = {item.name for item in home.glob("*.wav")} if home.is_dir() else set()
        sizes = {}
        print("ready", flush=True)
        while True:
            if home.is_dir():
                for path in sorted(home.glob("*.wav")):
                    if path.name in seen:
                        continue
                    size = path.stat().st_size
                    if size == 0 or sizes.get(path.name) != size:
                        sizes[path.name] = size
                        continue
                    seen.add(path.name)
                    del sizes[path.name]
                    self.hear(path)
            time.sleep(0.05)
if __name__ == "__main__":
    reexec()
    if len(sys.argv) == 1:
        Ear().watch()
    elif len(sys.argv) == 2 and sys.argv[1]:
        Ear().hear(sys.argv[1])
    else:
        raise SystemExit("usage: python asr.py [wav]")
