import json, subprocess, sys, time
from brain import ask
from install import MODELS, ROOT, reexec, venv_python
from settings import VARIANTS
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
        raise RuntimeError("value")
    def key(self):
        self.skip()
        start = self.i
        while self.i < len(self.text) and self.text[self.i] not in ":},] \t\r\n":
            self.i += 1
        if self.i == start:
            raise RuntimeError("key")
        return self.text[start:self.i]
def read_turn(text):
    tools = []
    while True:
        at = text.find("<|tool_call>")
        if at < 0:
            if text.strip():
                raise RuntimeError("tool call")
            if not tools:
                raise RuntimeError("tool call")
            return tools
        if text[:at].strip():
            raise RuntimeError("tool call")
        rest = text[at + len("<|tool_call>"):]
        end = rest.find("<tool_call|>")
        if end < 0:
            raise RuntimeError("open tool call")
        call = rest[:end].strip()
        if not call.startswith("call:") or not call.endswith("}"):
            raise RuntimeError("tool call")
        name, body = call[5:].split("{", 1)
        scan = Scan(body[:-1])
        args = {}
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
    subprocess.run([str(venv_python()), str(ROOT / "tts.py"), variant, text, language], check=True)
class Session:
    def __init__(self, variant):
        self.variant = variant
        self.said, self.note = "", ""
        self.lines = None
    def own(self, text):
        def words(value):
            return " ".join("".join(ch for ch in value.casefold() if ch.isalpha() or ch.isspace()).split())
        heard, said = words(text), words(self.said)
        return bool(heard) and bool(said) and heard in said
    def turn(self, body):
        message = f"{self.note}\n\n{body}" if self.note else body
        print(message, flush=True)
        content = ask(message)
        print(content, flush=True)
        tools = read_turn(content)
        print(json.dumps(tools, ensure_ascii=False), flush=True)
        self.said = ""
        for tool in tools:
            name = tool["name"]
            if name == "say":
                text, language = tool["text"], tool["language"]
                if not isinstance(text, list) or not isinstance(language, str) or not language:
                    raise RuntimeError("say")
                for piece in text:
                    self.said = f"{self.said} {piece}".strip() if self.said else piece
                    speak(self.variant, piece, language)
            elif name == "note":
                text = tool["text"]
                if not isinstance(text, str) or not text:
                    raise RuntimeError("note")
                self.note = text
            elif name == "listen":
                pass
            elif name == "quit":
                raise SystemExit
            else:
                raise RuntimeError("unknown tool " + name)
    def wait(self):
        while True:
            heard = [text for text in self.lines.take() if not self.own(text)]
            if heard:
                return " ".join(heard)
            time.sleep(0.05)
    def run(self):
        self.lines = Lines()
        print("ready", flush=True)
        while True:
            self.turn(self.wait())
if __name__ == "__main__":
    reexec()
    if len(sys.argv) != 2 or sys.argv[1] not in VARIANTS:
        raise SystemExit("usage: python trident.py <variant>")
    Session(sys.argv[1]).run()
