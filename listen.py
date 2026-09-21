import queue
import re
import subprocess
import tarfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from host import MODELS, ROOT, PipeServer, download
from settings import BRAIN, EAR, PROMPTS


def clean(text):
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def find(text, phrase):
    return re.search(r"\b" + re.escape(phrase) + r"\b", text)


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
    def __init__(self, speaking):
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
        vad = home / "silero_vad.onnx"
        if not vad.is_file():
            download(EAR["vad"], vad)
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(model / EAR["files"][0]), decoder=str(model / EAR["files"][1]),
            joiner=str(model / EAR["files"][2]), tokens=str(model / EAR["files"][3]),
            num_threads=EAR["threads"], sample_rate=EAR["sample_rate"], feature_dim=80,
            decoding_method="greedy_search", model_type="nemo_transducer", provider=EAR["provider"])
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
        self.speaking = speaking
        self.segments = queue.Queue()
        self.texts = queue.Queue()

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

    def start(self):
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
        text = f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{user}"
        return text + "<|im_end|>\n<|im_start|>assistant\n" if closed else text

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

    def complete(self, system, user, max_tokens):
        out = self.llm(self.prompt(system, user, True), max_tokens=max_tokens, temperature=0.0, stop=["<|im_end|>"])
        return out["choices"][0]["text"].strip()


class Mouth:
    def __init__(self, pipe, speaking):
        self.pipe = pipe
        self.speaking = speaking
        self.pool = ThreadPoolExecutor(max_workers=1)

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
        self.ear = Ear(speaking).start()
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
            self.mouth.say(self.parse(self.brain.complete(self.speak_prompt, request, 512)))
            self.mode = "idle"
            return
        code = self.brain.complete(PROMPTS["code"], request, 768)
        if code.startswith("```"):
            code = code.split("\n", 1)[1].rsplit("```", 1)[0]
        intent = code.strip().splitlines()[0]
        if not intent.startswith("# I will "):
            raise RuntimeError("brain: script has no intent line: " + intent)
        (MODELS / "tool.py").write_text(code.strip() + "\n", encoding="utf-8")
        self.mouth.say([self.line(intent[2:]), self.line("Say approve or reject.")])
        self.mode = "approval"

    def approve(self):
        run = subprocess.run([str(self.py), str(MODELS / "tool.py")], cwd=str(ROOT), capture_output=True, text=True, timeout=self.timeout)
        report = self.brain.complete(self.speak_prompt + PROMPTS["report"], run.stdout + run.stderr, 256)
        self.mouth.say(self.parse(report))
        self.mode = "idle"

    def step(self, text):
        print(f"hear {text}", flush=True)
        words = clean(text)
        if self.mode == "idle":
            wake = find(words, self.phrases["wake-phrase"])
            if not wake:
                return
            rest = words[wake.end():].strip()
            tool = rest.startswith(self.phrases["tool-phrase"])
            self.mode = "tool" if tool else "speak"
            self.transcript = []
            self.brain.llm.reset()
            words = rest[len(self.phrases["tool-phrase"]):].strip() if tool else rest
        if self.mode in ("speak", "tool"):
            stop = find(words, self.phrases["stop-phrase"])
            if stop:
                words = words[: stop.start()].strip()
            if words:
                self.transcript.append(words)
            if stop:
                self.finalize()
            else:
                self.brain.prefill(self.system(), " ".join(self.transcript))
            return
        if find(words, self.phrases["approve-phrase"]):
            self.approve()
        elif find(words, self.phrases["reject-phrase"]):
            self.mode = "idle"

    def run(self):
        print("listening", flush=True)
        while True:
            text = self.ear.texts.get()
            try:
                self.step(text)
            except RuntimeError as error:
                print(f"error {error}", flush=True)
                self.mode = "idle"
                self.mouth.say([self.line(str(error))])
