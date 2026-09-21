import queue
import re
import subprocess
import sys
import tarfile
import threading
import wave
from pathlib import Path

import numpy as np

from host import MODELS, ROOT, PipeServer, download, kill, write_wav
from settings import BRAIN, EAR, PROMPTS


class Voice:
    def __init__(self, t3):
        import gguf
        reader = gguf.GGUFReader(str(t3))
        field = reader.fields["general.architecture"]
        self.architecture = "gpt2" if bytes(field.parts[field.data[0]]).decode() == "chatterbox-gpt2" else "llama"
        if self.architecture == "gpt2":
            tokens, types = reader.fields["tokenizer.ggml.tokens"], reader.fields["tokenizer.ggml.token_type"]
            self.tags = [bytes(tokens.parts[i]).decode() for i, j in zip(tokens.data, types.data) if int(types.parts[j][0]) == 4]
            self.languages = []
        else:
            field = reader.fields["chatterbox.tokenizer.language_tokens"]
            self.languages = [code.strip("[]") for code in bytes(field.parts[field.data[0]]).decode().split(",")]
            self.tags = []

    def prompt(self, limit):
        return PROMPTS["open"][self.architecture].format(limit=limit, tags=" ".join(self.tags), languages=" ".join(self.languages))


class Ear:
    def __init__(self, speaking, feed=None):
        self.speaking = speaking
        self.texts = queue.Queue()
        self.feed = feed
        self.wavs = []
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
        config.silero_vad.model = str(vad)
        config.silero_vad.threshold = EAR["vad_threshold"]
        config.silero_vad.min_silence_duration = EAR["min_silence"]
        config.silero_vad.min_speech_duration = EAR["min_speech"]
        config.silero_vad.max_speech_duration = EAR["max_speech"]
        config.sample_rate = EAR["sample_rate"]
        config.num_threads = 1
        config.provider = EAR["provider"]
        self.vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=100)

    def capture(self):
        import sounddevice as sd
        window = self.vad.config.silero_vad.window_size
        buffer = np.array([], dtype=np.float32)
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
            samples = self.segments.get()
            stream = self.recognizer.create_stream()
            stream.accept_waveform(EAR["sample_rate"], samples)
            self.recognizer.decode_stream(stream)
            text = stream.result.text.strip()
            if text:
                self.texts.put(text)

    def inject(self):
        if self.feed == "-":
            lines = sys.stdin
        else:
            path = Path(self.feed)
            lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else self.feed.splitlines()
        for line in lines:
            line = line.strip()
            if line:
                self.texts.put(line)

    def inject_wav(self):
        for path in self.wavs:
            with wave.open(str(path), "rb") as wav:
                if wav.getnchannels() != 1:
                    raise RuntimeError("ear: wav must be mono")
                if wav.getsampwidth() != 2:
                    raise RuntimeError("ear: wav must be pcm16")
                rate = wav.getframerate()
                pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
            if rate != EAR["sample_rate"]:
                n = int(round(len(pcm) * EAR["sample_rate"] / rate))
                pcm = np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm).astype(np.float32)
            self.segments.put(pcm)

    def start(self):
        if self.wav:
            for target in (self.inject_wav, self.decode):
                threading.Thread(target=target, daemon=True).start()
        elif self.feed is not None:
            threading.Thread(target=self.inject, daemon=True).start()
        else:
            for target in (self.capture, self.decode):
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

    def complete(self, system, user, mode):
        out = self.llm(
            f"<|turn>system\n{system}<turn|>\n<|turn>user\n{user}<turn|>\n<|turn>model\n",
            stop=["<turn|>"], **BRAIN["decode"][mode])
        return out["choices"][0]["text"].strip()


class Mouth:
    def __init__(self, pipe, speaking):
        self.pipe = pipe
        self.speaking = speaking
        self.n = 0

    def say(self, lines):
        import sounddevice as sd
        self.speaking.set()
        for line in lines:
            pcm = np.frombuffer(PipeServer().synthesize(self.pipe, *line), dtype=np.int16)
            self.n += 1
            print(write_wav(pcm.tobytes(), str(self.n)), flush=True)
            print(f"say {line[0]}|{line[1]}" if line[0] else f"say {line[1]}", flush=True)
            sd.play(pcm, 24000)
            sd.wait()
        self.speaking.clear()


class Session:
    def __init__(self, pipe, t3, args, py):
        self.voice = Voice(t3)
        self.limit = int(args.session["chunk-chars"])
        self.timeout = int(args.session["tool-timeout"])
        self.py = py
        speaking = threading.Event()
        self.mouth = Mouth(pipe, speaking)
        self.brain = Brain()
        self.ear = Ear(speaking, args.text).start()
        self.open_prompt = self.voice.prompt(self.limit)
        self.pending = None

    def line(self, text):
        return ("en", text) if self.voice.architecture == "llama" else ("", text)

    def parse(self, out):
        lines = []
        for raw in out.splitlines():
            raw = raw.strip()
            if not raw:
                continue
            if self.voice.architecture == "gpt2":
                rest = raw
                while rest.startswith("["):
                    tag, rest = rest.split("]", 1)
                    if tag + "]" not in self.voice.tags:
                        raise RuntimeError("brain: unknown tag " + tag + "] in line: " + raw)
                    rest = rest.lstrip()
                if len(rest) > self.limit:
                    raise RuntimeError("brain: line too long: " + raw)
                lines.append(("", raw))
            else:
                match = re.fullmatch(r"([a-z]{2,3})\|(.+)", raw)
                if not match or match[1] not in self.voice.languages:
                    raise RuntimeError("brain: bad language line: " + raw)
                if len(match[2]) > self.limit:
                    raise RuntimeError("brain: line too long: " + raw)
                lines.append((match[1], match[2].strip()))
        if not lines:
            raise RuntimeError("brain: empty answer")
        return lines

    def halt(self):
        kill(MODELS / "server.pid")
        raise SystemExit

    def approve(self):
        run = subprocess.run([str(self.py), str(MODELS / "tool.py")], cwd=str(ROOT), capture_output=True, text=True, timeout=self.timeout)
        report = self.brain.complete(self.open_prompt, PROMPTS["report"] + "\n" + run.stdout + run.stderr, "report")
        self.mouth.say(self.parse(report))
        self.pending = None

    def step(self, text):
        print(f"hear {text}", flush=True)
        if self.pending:
            parts = self.brain.complete(PROMPTS["consent"].format(intent=self.pending), text, "consent").split()
            if not parts:
                raise RuntimeError("brain: empty consent")
            word = parts[0].lower()
            if word == "off":
                self.halt()
            if word == "yes":
                self.approve()
            elif word == "no":
                self.pending = None
            else:
                raise RuntimeError("brain: consent is not yes, no, or off: " + word)
            return
        out = self.brain.complete(self.open_prompt, text, "speak")
        if not out:
            return
        if out.split()[0].lower() == "off":
            self.halt()
        if out.lstrip().startswith("# I will "):
            intent = out.strip().splitlines()[0]
            (MODELS / "tool.py").write_text(out.strip() + "\n", encoding="utf-8")
            self.mouth.say([self.line(intent[2:])])
            self.pending = intent[2:]
            return
        self.mouth.say(self.parse(out))

    def run(self):
        print("listening", flush=True)
        while True:
            text = self.ear.texts.get()
            try:
                self.step(text)
            except (RuntimeError, OSError) as error:
                print(f"error {error}", flush=True)
                self.pending = None
                self.mouth.say([self.line(str(error))])
