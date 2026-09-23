import sys
from pathlib import Path
import torch
from install import MODELS, reexec
from settings import EAR, INBOX, bus, next_path, put, take

class Ear:
    def __init__(self, model=True):
        self.ready = False
        if not model:
            bus()
            return
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
        self.ready = True

    def transcribe(self, audio):
        inputs = self.processor(audio, sampling_rate=EAR["sample_rate"], language=EAR["language"])
        with torch.inference_mode():
            output = self.model.generate(**inputs, return_dict_in_generate=True)
        text = self.processor.batch_decode(output.sequences, skip_special_tokens=True)
        return (text[0] if text else "").strip()

    def line(self, text):
        path = put(next_path("transcription"), text + "\n")
        print(path.name, text, flush=True)

    def drain_inbox(self):
        bus()
        for path in sorted(INBOX.glob("*.txt")):
            text = take(path, "inbox").strip()
            if text:
                self.line(text)

    def hear(self, path):
        from transformers.audio_utils import load_audio
        file = Path(path)
        if not file.is_file():
            raise RuntimeError("missing " + str(file))
        text = self.transcribe(load_audio(str(file), sampling_rate=EAR["sample_rate"], backend="librosa"))
        if not text:
            raise RuntimeError("ear heard nothing")
        self.line(text)

    def listen(self, mic=True):
        import time
        bus()
        print("ready", flush=True)
        if not mic:
            while True:
                self.drain_inbox()
                time.sleep(0.05)
            return
        import numpy as np
        import sounddevice as sd
        rate, hop = EAR["sample_rate"], int(EAR["sample_rate"] * 0.05)
        pause, level = max(1, int(EAR["pause"] / 0.05)), EAR["level"]
        speech, quiet, hot = [], 0, False
        with sd.InputStream(samplerate=rate, channels=1, dtype="float32", blocksize=hop) as stream:
            while True:
                self.drain_inbox()
                frame, _ = stream.read(hop)
                if float(np.abs(frame).mean()) >= level:
                    hot, quiet = True, 0
                    speech.append(frame.copy())
                    continue
                if not hot:
                    continue
                speech.append(frame.copy())
                quiet += 1
                if quiet < pause:
                    continue
                audio = np.concatenate(speech).reshape(-1)
                speech, quiet, hot = [], 0, False
                if len(audio) < rate // 2:
                    continue
                text = self.transcribe(audio)
                if text:
                    self.line(text)

if __name__ == "__main__":
    reexec()
    if len(sys.argv) == 1:
        Ear().listen()
    elif len(sys.argv) == 2 and sys.argv[1] == "--inbox":
        Ear(model=False).listen(mic=False)
    elif len(sys.argv) == 2 and sys.argv[1]:
        Ear().hear(sys.argv[1])
    else:
        raise SystemExit("usage: python asr.py [wav | --inbox]")
