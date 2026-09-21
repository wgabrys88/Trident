import json, sys, time
from brain import ask
from install import MODELS, reexec
from settings import BRAIN, VARIANTS
from tts import say
LIMIT = 300
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
class Session:
    def __init__(self, pipe):
        self.pipe, self.n = pipe, 0
        self.memory, self.speech, self.quit_armed = "", "", False
        self.sent, self.watchdog_at, self.flush_at = 0.0, None, None
        self.lines = None
    def user(self, kind, body):
        lead = "A quit was proposed.\n" if self.quit_armed else ""
        return f"{lead}memory\n{self.memory}\n\n{kind}\n{body}"
    def turn(self, kind, body):
        proposed = self.quit_armed
        print(kind, flush=True)
        print(body, flush=True)
        content = ask(self.user(kind, body))
        print(content, flush=True)
        memory, tools = read_turn(content)
        if len(memory) > LIMIT:
            raise RuntimeError("memory exceeds 300 characters")
        print(json.dumps({"memory": memory, "tools": tools}, ensure_ascii=False), flush=True)
        self.memory = memory
        called = False
        for tool in tools:
            name = tool["name"]
            if name == "say":
                self.n = say(self.pipe, tool["text"], self.n)
            elif name == "listen":
                if self.lines is None:
                    self.lines = Lines()
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
            due = self.flush_at if self.speech else (self.watchdog_at if self.watchdog_at is not None else self.sent + arm)
            lines = []
            while True:
                lines = self.lines.take()
                if lines or time.monotonic() >= due:
                    break
                time.sleep(0.05)
            now = time.monotonic()
            if lines:
                for text in lines:
                    self.speech = f"{self.speech} {text}".strip() if self.speech else text
                self.flush_at = now + flush
                continue
            if self.speech:
                body, self.speech = self.speech, ""
                return "speech", body
            return "watchdog", "No new speech."
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
    Session(rf"\\.\pipe\chatterbox-{argv[1]}").run(argv[2])
