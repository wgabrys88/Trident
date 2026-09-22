import ctypes, threading, time
import numpy as np
from install import MODELS, reexec
from settings import EAR
LINES = MODELS / "ear" / "lines.txt"
def device():
    import sounddevice as sd
    return next(i for i, item in enumerate(sd.query_devices())
                if str(item["name"]).startswith("CABLE Output") and item["max_input_channels"] and item["default_samplerate"] == 48000)
class Ear:
    def __init__(self):
        import sherpa_onnx
        model, vad = MODELS / "ear" / EAR["dir"], MODELS / "ear" / "silero_vad.onnx"
        paths = [model / name for name in EAR["files"]] + [vad]
        missing = [path for path in paths if not path.is_file()]
        if missing:
            raise RuntimeError("missing " + str(missing[0]))
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(paths[0]), decoder=str(paths[1]), joiner=str(paths[2]), tokens=str(paths[3]),
            num_threads=EAR["threads"], sample_rate=EAR["sample_rate"], feature_dim=80,
            decoding_method="greedy_search", model_type="nemo_transducer", provider=EAR["provider"])
        config = sherpa_onnx.VadModelConfig()
        silero = config.silero_vad
        silero.model, silero.threshold, silero.min_silence_duration, silero.min_speech_duration, silero.max_speech_duration = (
            str(vad), EAR["vad_threshold"], EAR["min_silence"], EAR["min_speech"], EAR["max_speech"])
        config.sample_rate, config.num_threads, config.provider = EAR["sample_rate"], 1, EAR["provider"]
        self.vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=100)
        self.segments = []
        self.lock = threading.Lock()
    def transcribe(self, segment):
        stream = self.recognizer.create_stream()
        stream.accept_waveform(EAR["sample_rate"], segment)
        self.recognizer.decode_stream(stream)
        return stream.result.text.strip()
    def warm(self):
        window = self.vad.config.silero_vad.window_size
        self.vad.accept_waveform(np.zeros(window, dtype=np.float32))
        while not self.vad.empty():
            self.vad.pop()
        self.transcribe(np.zeros(EAR["sample_rate"], dtype=np.float32))
    def capture(self):
        import sounddevice as sd
        ctypes.windll.ole32.CoInitializeEx(None, 0)
        window = self.vad.config.silero_vad.window_size
        buffer = np.array([], dtype=np.float32)
        with sd.InputStream(device=device(), channels=1, dtype="float32", samplerate=48000, blocksize=4800) as stream:
            while True:
                samples, _ = stream.read(4800)
                flat = np.asarray(samples, dtype=np.float32).reshape(-1)
                flat = flat[:len(flat) - (len(flat) % 3)]
                buffer = np.concatenate([buffer, flat.reshape(-1, 3).mean(axis=1)])
                while len(buffer) >= window:
                    self.vad.accept_waveform(buffer[:window])
                    buffer = buffer[window:]
                while not self.vad.empty():
                    segment = np.array(self.vad.front.samples, dtype=np.float32)
                    self.vad.pop()
                    with self.lock:
                        self.segments.append(segment)
    def write(self, text):
        LINES.parent.mkdir(parents=True, exist_ok=True)
        with LINES.open("a", encoding="utf-8") as out:
            out.write(text + "\n")
            out.flush()
        print(text, flush=True)
    def run(self):
        self.warm()
        device()
        print("ready", flush=True)
        threading.Thread(target=self.capture, daemon=True).start()
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
