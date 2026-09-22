import json, subprocess, sys, time
from brain import ask
from install import MODELS, ROOT, reexec, venv_python
from settings import BRAIN, VARIANTS
LINES = MODELS / "ear" / "lines.txt"
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
        if self.peek().isalpha():
            start = self.i
            while self.i < len(self.text) and self.text[self.i].isalpha():
                self.i += 1
            return self.text[start:self.i]
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
class Lines:
    def __init__(self):
        self.offset = LINES.stat().st_size if LINES.is_file() else 0
    def take(self):
        if not LINES.is_file():
            return []
        size = LINES.stat().st_size
        if size < self.offset:
            self.offset = 0
        if size == self.offset:
            return []
        with LINES.open("rb") as handle:
            handle.seek(self.offset)
            data = handle.read()
        if not data.endswith(b"\n"):
            cut = data.rfind(b"\n")
            if cut < 0:
                return []
            data = data[:cut + 1]
        self.offset += len(data)
        return [line for line in data.decode("utf-8").splitlines() if line]
def speak(variant, text, language):
    subprocess.run([str(venv_python()), str(ROOT / "tts.py"), variant, "--hear", text, language], check=True)
class Session:
    def __init__(self, variant):
        self.variant = variant
        self.memory, self.speech, self.said = "", "", ""
        self.flush_at = None
        self.lines = None
    def user(self, body):
        note = f"{self.memory}\n\n" if self.memory else ""
        return f"{note}{body}"
    def own(self, text):
        heard = " ".join(text.casefold().split())
        said = " ".join(self.said.casefold().split())
        return bool(heard) and bool(said) and heard in said
    def turn(self, kind, body):
        print(kind, flush=True)
        print(body, flush=True)
        content = ask(self.user(body))
        print(content, flush=True)
        memory, tools = read_turn(content)
        print(json.dumps({"memory": memory, "tools": tools}, ensure_ascii=False), flush=True)
        self.memory = memory
        self.said = ""
        for tool in tools:
            name = tool["name"]
            if name == "say":
                text, language = tool["text"], tool["language"]
                if isinstance(text, str):
                    text = [text]
                if not isinstance(text, list) or not isinstance(language, str) or not language:
                    raise RuntimeError("say")
                for piece in text:
                    self.said = f"{self.said} {piece}".strip() if self.said else piece
                    speak(self.variant, piece, language)
            elif name == "listen":
                if self.lines is None:
                    self.lines = Lines()
            elif name == "quit":
                raise SystemExit
            else:
                raise RuntimeError("unknown tool " + name)
    def wait(self):
        flush = BRAIN["flush"]
        while True:
            while True:
                lines = self.lines.take()
                if lines or (self.speech and self.flush_at is not None and time.monotonic() >= self.flush_at):
                    break
                time.sleep(0.05)
            now = time.monotonic()
            if lines:
                heard = [text for text in lines if not self.own(text)]
                if not heard:
                    continue
                for text in heard:
                    self.speech = f"{self.speech} {text}".strip() if self.speech else text
                self.flush_at = now + flush
                continue
            body, self.speech = self.speech, ""
            self.flush_at = None
            return "speech", body
    def run(self, query):
        kind, body = "query", query
        while True:
            self.turn(kind, body)
            if self.lines is None:
                return
            kind, body = self.wait()
if __name__ == "__main__":
    reexec()
    argv = sys.argv
    if len(argv) != 3 or argv[1] not in VARIANTS or not argv[2]:
        raise SystemExit("query is required")
    Session(argv[1]).run(argv[2])
