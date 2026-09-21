import json, queue, tarfile, threading, time
import numpy as np
from host import MODELS, PipeServer, download, write_wav
from settings import BRAIN, EAR, TOOLS
LIMIT = 300
class Scan:
    def __init__(self, text):
        self.text, self.i = text, 0
    def skip(self):
        while self.i < len(self.text) and self.text[self.i] in " \t\r\n":
            self.i += 1
    def peek(self, n=1):
        return self.text[self.i:self.i + n]
    def take(self, token):
        self.skip()
        if not self.text.startswith(token, self.i):
            raise RuntimeError("expected " + token)
        self.i += len(token)
    def value(self):
        self.skip()
        if self.peek(5) == '<|"|>':
            self.i += 5
            end = self.text.find('<|"|>', self.i)
            if end < 0:
                raise RuntimeError("open string")
            value, self.i = self.text[self.i:end], end + 5
            return value
        if self.peek() == "[":
            self.i += 1
            items = []
            self.skip()
            if self.peek() != "]":
                while True:
                    items.append(self.value())
                    self.skip()
                    if self.peek() != ",":
                        break
                    self.i += 1
            self.take("]")
            return items
        if self.peek() == "{":
            self.i += 1
            obj = {}
            self.skip()
            if self.peek() != "}":
                while True:
                    key = self.key()
                    self.take(":")
                    obj[key] = self.value()
                    self.skip()
                    if self.peek() != ",":
                        break
                    self.i += 1
            self.take("}")
            return obj
        if self.peek(4) == "true":
            self.i += 4
            return True
        if self.peek(5) == "false":
            self.i += 5
            return False
        if self.peek(4) == "null":
            self.i += 4
            return None
        start = self.i
        if self.peek() == "-":
            self.i += 1
        while self.i < len(self.text) and self.text[self.i] in "0123456789.":
            self.i += 1
        if self.i == start:
            raise RuntimeError("value")
        token = self.text[start:self.i]
        return float(token) if "." in token else int(token)
    def key(self):
        self.skip()
        start = self.i
        while self.i < len(self.text) and self.text[self.i] not in ":},] \t\r\n":
            self.i += 1
        if self.i == start:
            raise RuntimeError("key")
        return self.text[start:self.i]
def read_turn(text):
    parts, tools = [], []
    while text:
        call_at, think_at = text.find("<|tool_call>"), text.find("<|channel>")
        cuts = [i for i in (call_at, think_at) if i >= 0]
        if not cuts:
            parts.append(text)
            break
        at = min(cuts)
        parts.append(text[:at])
        if at == call_at:
            rest = text[at + len("<|tool_call>"):]
            end = rest.find("<tool_call|>")
            if end < 0:
                raise RuntimeError("open tool call")
            call = rest[:end].strip()
            if not call.startswith("call:") or not call.endswith("}"):
                raise RuntimeError("tool call")
            name, body = call[5:].split("{", 1)
            scan, args = Scan(body[:-1]), {}
            scan.skip()
            while scan.i < len(scan.text):
                key = scan.key()
                scan.take(":")
                args[key] = scan.value()
                scan.skip()
                if scan.peek() == ",":
                    scan.i += 1
                    scan.skip()
            tools.append({"name": name, **args})
            text = rest[end + len("<tool_call|>"):]
        else:
            rest = text[at + len("<|channel>"):]
            end = rest.find("<channel|>")
            if end < 0:
                raise RuntimeError("open channel")
            text = rest[end + len("<channel|>"):]
    return "".join(parts).strip(), tools
class Ear:
    def __init__(self):
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
        vad = home / "silero_vad.onnx"
        if not vad.is_file():
            download(EAR["vad"], vad)
        config = sherpa_onnx.VadModelConfig()
        s = config.silero_vad
        s.model, s.threshold, s.min_silence_duration, s.min_speech_duration, s.max_speech_duration = (
            str(vad), EAR["vad_threshold"], EAR["min_silence"], EAR["min_speech"], EAR["max_speech"])
        config.sample_rate, config.num_threads, config.provider = EAR["sample_rate"], 1, EAR["provider"]
        self.vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=100)
        self.segments, self.texts = queue.Queue(), queue.Queue()
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
                    self.segments.put((np.array(self.vad.front.samples, dtype=np.float32), time.monotonic()))
                    self.vad.pop()
    def decode(self):
        while True:
            segment, when = self.segments.get()
            stream = self.recognizer.create_stream()
            stream.accept_waveform(EAR["sample_rate"], segment)
            self.recognizer.decode_stream(stream)
            text = stream.result.text.strip()
            if text:
                self.texts.put((text, when))
    def start(self):
        for target in (self.capture, self.decode):
            threading.Thread(target=target, daemon=True).start()
class Brain:
    def __init__(self):
        from llama_cpp import Llama
        path = MODELS / BRAIN["file"]
        if not path.is_file():
            download(BRAIN["url"], path)
        self.llm = Llama(model_path=str(path), n_ctx=BRAIN["n_ctx"], n_threads=BRAIN["n_threads"],
                         n_gpu_layers=BRAIN["n_gpu_layers"], chat_format="chat_template.default", verbose=False)
    def complete(self, user):
        content = self.llm.create_chat_completion(
            messages=[{"role": "user", "content": user}], tools=TOOLS, stop=["<turn|>"], **BRAIN["decode"]
        )["choices"][0]["message"]["content"] or ""
        print(content, flush=True)
        memory, tools = read_turn(content)
        if len(memory) > LIMIT:
            raise RuntimeError("memory exceeds 300 characters")
        print(json.dumps({"memory": memory, "tools": tools}, ensure_ascii=False), flush=True)
        return memory, tools
class Mouth:
    def __init__(self, pipe):
        self.pipe, self.n, self.audio = pipe, 0, queue.Queue()
        threading.Thread(target=self.play, daemon=True).start()
    def play(self):
        import sounddevice as sd
        while True:
            pcm = self.audio.get()
            try:
                sd.play(pcm, 24000)
                sd.wait()
            finally:
                self.audio.task_done()
    def say(self, text):
        if not isinstance(text, list):
            raise RuntimeError("say text is not an array")
        clips = []
        for piece in text:
            if not isinstance(piece, str) or len(piece) > LIMIT:
                raise RuntimeError("say piece exceeds 300 characters")
            pcm = np.frombuffer(PipeServer().synthesize(self.pipe, "", piece), dtype=np.int16)
            self.n += 1
            print(write_wav(pcm.tobytes(), str(self.n)), flush=True)
            print(piece, flush=True)
            clips.append(pcm)
        if clips:
            self.audio.put(np.concatenate(clips))
    def finish(self):
        self.audio.join()
class Session:
    def __init__(self, pipe):
        self.mouth, self.brain, self.ear = Mouth(pipe), Brain(), None
        self.memory, self.speech, self.quit_armed = "", "", False
        self.sent, self.watchdog_at, self.flush_at = 0.0, None, None
    def user(self, kind, body):
        lead = "A quit was proposed.\n" if self.quit_armed else ""
        return f"{lead}memory\n{self.memory}\n\n{kind}\n{body}"
    def turn(self, kind, body):
        proposed = self.quit_armed
        print(kind, flush=True)
        print(body, flush=True)
        memory, tools = self.brain.complete(self.user(kind, body))
        self.memory = memory
        called = False
        for tool in tools:
            name = tool["name"]
            if name == "say":
                self.mouth.say(tool["text"])
            elif name == "listen":
                if self.ear is None:
                    self.ear = Ear()
                    self.ear.start()
            elif name == "quit":
                called = True
                if proposed:
                    raise SystemExit
                self.quit_armed = True
            else:
                raise RuntimeError("unknown tool " + name)
        if not called:
            self.quit_armed = False
        if kind == "watchdog":
            self.watchdog_at = time.monotonic() + BRAIN["watchdog_repeat"]
        else:
            self.sent, self.watchdog_at = time.monotonic(), None
    def wait(self):
        arm, flush = BRAIN["watchdog_arm"], BRAIN["flush"]
        while True:
            now = time.monotonic()
            if self.speech:
                timeout = self.flush_at - now
            else:
                due = self.watchdog_at if self.watchdog_at is not None else self.sent + arm
                timeout = due - now
            try:
                text, when = self.ear.texts.get(timeout=max(0.0, timeout))
            except queue.Empty:
                now = time.monotonic()
                if self.speech and now >= self.flush_at:
                    body, self.speech = self.speech, ""
                    return "speech", body
                due = self.watchdog_at if self.watchdog_at is not None else self.sent + arm
                if not self.speech and now >= due:
                    return "watchdog", "No new speech."
                continue
            self.speech = f"{self.speech} {text}".strip() if self.speech else text
            self.flush_at = when + flush
    def run(self, query):
        kind, body = "query", query
        while True:
            self.turn(kind, body)
            if self.ear is None:
                self.mouth.finish()
                return
            kind, body = self.wait()
