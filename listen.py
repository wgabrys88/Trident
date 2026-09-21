import queue
import re
import subprocess
import sys
import tarfile
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from host import MODELS, ROOT, PipeServer, download, write_wav
from settings import BRAIN, EAR, PROMPTS


def clean(text):
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def find(text, phrase):
    return re.search(r"\b" + re.escape(phrase) + r"\b", text)


def cut(text, phrase):
    match = re.search(r"(?i)\b" + re.escape(phrase) + r"\b", text)
    if not match:
        raise RuntimeError("ear: phrase missing: " + phrase)
    return text[:match.start()], text[match.end():]


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
        return PROMPTS["speak"][self.architecture].format(limit=limit, tags=" ".join(self.tags), languages=" ".join(self.languages))


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

    def prompt(self, system, user, closed):
        text = f"<|turn>system\n{system}<turn|>\n<|turn>user\n{user}"
        return text + "<turn|>\n<|turn>model\n" if closed else text

    def prefill(self, system, user):
        tokens = self.llm.tokenize(self.prompt(system, user, False).encode("utf-8"), special=True)
        fed = self.llm.input_ids[: self.llm.n_tokens].tolist()
        common = 0
        for a, b in zip(fed, tokens):
            if a != b:
                break
            common += 1
        self.llm.n_tokens = common
        self.llm.eval(tokens[common:])

    def complete(self, system, user, mode):
        knobs = BRAIN["decode"][mode]
        out = self.llm(self.prompt(system, user, True), stop=["<turn|>"], **knobs)
        return out["choices"][0]["text"].strip()


class Mouth:
    def __init__(self, pipe, speaking):
        self.pipe = pipe
        self.speaking = speaking
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.n = 0

    def synthesize(self, language, text):
        return np.frombuffer(PipeServer().synthesize(self.pipe, language, text), dtype=np.int16)

    def say(self, lines):
        import sounddevice as sd
        self.speaking.set()
        pending = self.pool.submit(self.synthesize, *lines[0])
        for index, line in enumerate(lines):
            pcm = pending.result()
            if index + 1 < len(lines):
                pending = self.pool.submit(self.synthesize, *lines[index + 1])
            self.n += 1
            print(write_wav(pcm.tobytes(), str(self.n)), flush=True)
            print(f"say {line[0]}|{line[1]}" if line[0] else f"say {line[1]}", flush=True)
            sd.play(pcm, 24000)
            sd.wait()
        self.speaking.clear()


class Session:
    def __init__(self, pipe, t3, args, py):
        self.voice = Voice(t3)
        self.phrases = args.session
        self.limit = int(args.session["chunk-chars"])
        self.timeout = int(args.session["tool-timeout"])
        self.py = py
        speaking = threading.Event()
        self.mouth = Mouth(pipe, speaking)
        self.brain = Brain()
        self.ear = Ear(speaking, args.text).start()
        self.speak_prompt = self.voice.prompt(self.limit)
        self.mode = "idle"
        self.transcript = []

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

    def system(self):
        return PROMPTS["code"] if self.mode == "tool" else self.speak_prompt

    def finalize(self):
        request = " ".join(self.transcript)
        if self.mode == "speak":
            self.mouth.say(self.parse(self.brain.complete(self.speak_prompt, request, "speak")))
            self.mode = "idle"
            return
        code = self.brain.complete(PROMPTS["code"], request, "code")
        intent = code.strip().splitlines()[0]
        if not intent.startswith("# I will "):
            raise RuntimeError("brain: script has no intent line: " + intent)
        (MODELS / "tool.py").write_text(code.strip() + "\n", encoding="utf-8")
        self.mouth.say([self.line(intent[2:]), self.line("Say carry out the order or this transmission rejects the proposal.")])
        self.mode = "approval"

    def approve(self):
        run = subprocess.run([str(self.py), str(MODELS / "tool.py")], cwd=str(ROOT), capture_output=True, text=True, timeout=self.timeout)
        report = self.brain.complete(self.speak_prompt + PROMPTS["report"], run.stdout + run.stderr, "report")
        self.mouth.say(self.parse(report))
        self.mode = "idle"

    def step(self, text):
        print(f"hear {text}", flush=True)
        folded = clean(text)
        if self.mode == "idle":
            if not find(folded, self.phrases["wake-phrase"]):
                return
            _, text = cut(text, self.phrases["wake-phrase"])
            folded = clean(text)
            tool = folded.startswith(self.phrases["tool-phrase"])
            self.mode = "tool" if tool else "speak"
            self.transcript = []
            self.brain.llm.reset()
            if tool:
                _, text = cut(text, self.phrases["tool-phrase"])
        if self.mode in ("speak", "tool"):
            stop = find(clean(text), self.phrases["stop-phrase"])
            piece = text
            if stop:
                piece, _ = cut(text, self.phrases["stop-phrase"])
            piece = piece.strip()
            if piece:
                self.transcript.append(piece)
            if stop:
                self.finalize()
            else:
                self.brain.prefill(self.system(), " ".join(self.transcript))
            return
        if find(folded, self.phrases["approve-phrase"]):
            self.approve()
        elif find(folded, self.phrases["reject-phrase"]):
            self.mode = "idle"

    def run(self):
        print("listening", flush=True)
        while True:
            text = self.ear.texts.get()
            try:
                self.step(text)
            except (RuntimeError, OSError) as error:
                print(f"error {error}", flush=True)
                self.mode = "idle"
                self.mouth.say([self.line(str(error))])
