import ctypes, threading, time
import numpy as np
import torch
from install import MODELS, reexec
from settings import EAR
LINES = MODELS / "ear" / "lines.txt"
def device():
    import sounddevice as sd
    return next(i for i, item in enumerate(sd.query_devices())
                if str(item["name"]).startswith("CABLE Output") and item["max_input_channels"] and item["default_samplerate"] == 48000)
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
        self.segments = []
        self.lock = threading.Lock()
    def transcribe(self, segment):
        if len(segment) < int(EAR["min_speech"] * EAR["sample_rate"]):
            return ""
        inputs = self.processor(segment, sampling_rate=EAR["sample_rate"], language=EAR["language"])
        with torch.inference_mode():
            output = self.model.generate(**inputs, return_dict_in_generate=True)
        text = self.processor.batch_decode(output.sequences, skip_special_tokens=True)
        return (text[0] if text else "").strip()
    def warm(self):
        self.transcribe(np.zeros(EAR["sample_rate"], dtype=np.float32))
    def capture(self, started):
        import sounddevice as sd
        ctypes.windll.ole32.CoInitializeEx(None, 0)
        rate = EAR["sample_rate"]
        preroll_n = int(EAR["preroll"] * rate)
        min_speech_n = int(EAR["min_speech"] * rate)
        min_silence_n = int(EAR["min_silence"] * rate)
        max_n = int(EAR["max_speech"] * rate)
        pending = np.zeros(0, dtype=np.float32)
        speech, heard, quiet = None, 0, 0
        with sd.InputStream(device=device(), channels=1, dtype="float32", samplerate=48000, blocksize=4800) as stream:
            started.set()
            while True:
                samples, _ = stream.read(4800)
                flat = np.asarray(samples, dtype=np.float32).reshape(-1)
                flat = flat[:len(flat) - (len(flat) % 3)]
                if not len(flat):
                    continue
                audio = flat.reshape(-1, 3).mean(axis=1)
                loud = float(np.sqrt(np.mean(audio * audio))) >= EAR["level"]
                if speech is None:
                    pending = np.concatenate((pending, audio))
                    if len(pending) > preroll_n:
                        pending = pending[-preroll_n:]
                    if loud:
                        speech, heard, quiet = pending, len(audio), 0
                        pending = np.zeros(0, dtype=np.float32)
                    continue
                speech = np.concatenate((speech, audio))
                if loud:
                    heard += len(audio)
                    quiet = 0
                else:
                    quiet += len(audio)
                if (quiet >= min_silence_n and heard >= min_speech_n) or len(speech) >= max_n:
                    with self.lock:
                        self.segments.append(np.array(speech, dtype=np.float32))
                    speech, heard, quiet = None, 0, 0
    def write(self, text):
        LINES.parent.mkdir(parents=True, exist_ok=True)
        with LINES.open("a", encoding="utf-8") as out:
            out.write(text + "\n")
            out.flush()
        print(text, flush=True)
    def run(self):
        self.warm()
        device()
        started = threading.Event()
        threading.Thread(target=self.capture, args=(started,), daemon=True).start()
        started.wait()
        print("ready", flush=True)
        while True:
            with self.lock:
                segment = self.segments.pop(0) if self.segments else None
            if segment is None:
                time.sleep(0.05)
                continue
            text = self.transcribe(segment)
            if text:
                self.write(text)
if __name__ == "__main__":
    reexec()
    Ear().run()
