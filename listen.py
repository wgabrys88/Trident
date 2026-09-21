import queue, sys, tarfile, threading, wave
from pathlib import Path
import numpy as np
from host import MODELS, PipeServer, download, write_wav
from settings import BRAIN, EAR, PROMPT
class Voice:
    def __init__(self, t3):
        import gguf
        reader = gguf.GGUFReader(str(t3))
        field = reader.fields["general.architecture"]
        self.multilingual = bytes(field.parts[field.data[0]]).decode() != "chatterbox-gpt2"
class Ear:
    def __init__(self, speaking, feed=None):
        self.speaking, self.feed, self.texts, self.wavs = speaking, feed, queue.Queue(), []
        if feed not in (None, "-"):
            path = Path(feed)
            if path.is_dir():
                self.wavs = sorted(item for item in path.iterdir() if item.suffix.lower() == ".wav")
                if not self.wavs:
                    raise RuntimeError("ear: no wav in " + feed)
            elif path.suffix.lower() == ".wav":
                if not path.is_file():
                    raise FileNotFoundError(feed)
                self.wavs = [path]
        self.wav = bool(self.wavs)
        if feed is not None and not self.wav:
            return
        import sherpa_onnx
        home = MODELS / "ear"
        home.mkdir(parents=True, exist_ok=True)
        model = home / EAR["dir"]
        if not all((model / name).is_file() for name in EAR["files"]):
            archive = home / "parakeet.tar.bz2"
            download(EAR["archive"], archive)
            with tarfile.open(archive, "r:bz2") as tar:
                tar.extractall(home, filter="data")
            archive.unlink()
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(model / EAR["files"][0]), decoder=str(model / EAR["files"][1]),
            joiner=str(model / EAR["files"][2]), tokens=str(model / EAR["files"][3]),
            num_threads=EAR["threads"], sample_rate=EAR["sample_rate"], feature_dim=80,
            decoding_method="greedy_search", model_type="nemo_transducer", provider=EAR["provider"])
        self.segments = queue.Queue()
        if self.wav:
            return
        vad = home / "silero_vad.onnx"
        if not vad.is_file():
            download(EAR["vad"], vad)
        config = sherpa_onnx.VadModelConfig()
        s = config.silero_vad
        s.model, s.threshold, s.min_silence_duration, s.min_speech_duration, s.max_speech_duration = (
            str(vad), EAR["vad_threshold"], EAR["min_silence"], EAR["min_speech"], EAR["max_speech"])
        config.sample_rate, config.num_threads, config.provider = EAR["sample_rate"], 1, EAR["provider"]
        self.vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=100)
    def capture(self):
        import sounddevice as sd
        window, buffer = self.vad.config.silero_vad.window_size, np.array([], dtype=np.float32)
        with sd.InputStream(channels=1, dtype="float32", samplerate=EAR["sample_rate"]) as stream:
            while True:
                samples, _ = stream.read(EAR["sample_rate"] // 10)
                buffer = np.concatenate([buffer, samples.reshape(-1)])
                while len(buffer) >= window:
                    self.vad.accept_waveform(buffer[:window])
                    buffer = buffer[window:]
                while not self.vad.empty():
                    if not self.speaking.is_set():
                        self.segments.put(np.array(self.vad.front.samples, dtype=np.float32))
                    self.vad.pop()
    def decode(self):
        while True:
            segment = self.segments.get()
            if segment is None:
                self.texts.put(None)
                return
            stream = self.recognizer.create_stream()
            stream.accept_waveform(EAR["sample_rate"], segment)
            self.recognizer.decode_stream(stream)
            text = stream.result.text.strip()
            if text:
                self.texts.put((stream.result.lang, text))
    def inject(self):
        lines = sys.stdin if self.feed == "-" else (
            Path(self.feed).read_text(encoding="utf-8").splitlines() if Path(self.feed).is_file() else self.feed.splitlines())
        for line in lines:
            if line.strip():
                self.texts.put(("", line.strip()))
        self.texts.put(None)
    def inject_wav(self):
        for path in self.wavs:
            with wave.open(str(path), "rb") as wav:
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                    raise RuntimeError("ear: wav must be mono pcm16")
                rate, pcm = wav.getframerate(), np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
            if rate != EAR["sample_rate"]:
                n = int(round(len(pcm) * EAR["sample_rate"] / rate))
                pcm = np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm).astype(np.float32)
            self.segments.put(pcm)
        self.segments.put(None)
    def start(self):
        targets = (self.inject_wav, self.decode) if self.wav else ((self.inject,) if self.feed is not None else (self.capture, self.decode))
        for target in targets:
            threading.Thread(target=target, daemon=True).start()
        return self
class Brain:
    def __init__(self):
        from llama_cpp import Llama
        path = MODELS / BRAIN["file"]
        if not path.is_file():
            download(BRAIN["url"], path)
        self.llm = Llama(model_path=str(path), n_ctx=BRAIN["n_ctx"], n_threads=BRAIN["n_threads"],
                         n_gpu_layers=BRAIN["n_gpu_layers"], verbose=False)
    def complete(self, user):
        out = self.llm(f"<|turn>system\n{PROMPT}<turn|>\n<|turn>user\n{user}<turn|>\n<|turn>model\n",
                       stop=["<turn|>"], **BRAIN["decode"])
        return out["choices"][0]["text"].strip()
class Mouth:
    def __init__(self, pipe, speaking, multilingual):
        self.pipe, self.speaking, self.multilingual, self.n = pipe, speaking, multilingual, 0
    def say(self, language, text):
        import sounddevice as sd
        self.speaking.set()
        pcm = np.frombuffer(PipeServer().synthesize(self.pipe, language if self.multilingual else "", text), dtype=np.int16)
        self.n += 1
        print(write_wav(pcm.tobytes(), str(self.n)), flush=True)
        print(text, flush=True)
        sd.play(pcm, 24000)
        sd.wait()
        self.speaking.clear()
class Session:
    def __init__(self, pipe, t3, args):
        speaking = threading.Event()
        self.mouth = Mouth(pipe, speaking, Voice(t3).multilingual)
        self.brain = Brain()
        self.ear = Ear(speaking, args.text).start()
    def step(self, heard):
        lang, text = heard
        print(f"hear {text}", flush=True)
        if lang:
            print(f"lang {lang}", flush=True)
        out = self.brain.complete(f"{lang}\n{text}" if lang else text)
        if not out:
            return
        self.mouth.say(lang, out)
    def run(self):
        print("listening", flush=True)
        while True:
            heard = self.ear.texts.get()
            if heard is None:
                raise SystemExit
            self.step(heard)
