"""Gemma 4 E2B through llama-server, in the token format she was trained on.

think() is the agent turn: system text, tool declarations, and recent turns, with thinking on.
She returns a thought and then either one tool call or the words to say. That tool runs here, and the
same turn continues with a <|tool_response>, so the thought stays in context. That turn carries no picture.

see(), locate(), and ask_json() are separate requests with thinking off. see() and locate() each take one image. locate() and ask_json() force a JSON schema.
locate() answers with a point on a 1000 by 1000 grid. Pixel conversion happens outside this file.
Tools arrive as arguments. python -m organs.brain prints a structured answer and one short sentence. The question is the command line, or the built-in one when that is empty.
"""

import base64
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from organs import CONFIG, ROOT, log, path_of, state_dir

LOG = log("brain")
CFG = CONFIG["brain"]

# Gemma 4 control tokens. Do not paraphrase these; the tokenizer owns them.
BOS = "<bos>"
TURN_OPEN = "<|turn>"
TURN_CLOSE = "<turn|>"
THINK = "<|think|>"
QUOTE = '<|"|>'
TOOL_RESPONSE_OPEN = "<|tool_response>"
TOOL_RESPONSE_CLOSE = "<tool_response|>"

THOUGHT_RE = re.compile(r"<\|channel>thought\n?(.*?)(?:<channel\|>|$)", re.DOTALL)
CALL_RE = re.compile(r"<\|tool_call>call:(\w+)\{(.*?)\}<tool_call\|>", re.DOTALL)
ARG_RE = re.compile(r'(\w+):(?:<\|"\|>(.*?)<\|"\|>|([^,}]*))', re.DOTALL)
CONTROL_RE = re.compile(r"<\|[a-z_\"]+\|?>|<[a-z_]+\|>|<bos>|<eos>")

LOCATE_SCHEMA = {
    "type": "object",
    "properties": {
        "y": {"type": "integer", "minimum": 0, "maximum": 1000},
        "x": {"type": "integer", "minimum": 0, "maximum": 1000},
    },
    "required": ["y", "x"],
}
BUBBLE_SCHEMA = {
    "type": "object",
    "properties": {
        "messages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "who": {"type": "string", "enum": ["user", "assistant"]},
                    "text": {"type": "string"},
                },
                "required": ["who", "text"],
            },
        }
    },
    "required": ["messages"],
}


@dataclass
class Tool:
    """One action she may take. params maps a name to its description, a type of STRING, INTEGER, NUMBER, or BOOLEAN, and an optional enum. optional names can be left out. final ends the turn with no spoken words."""

    name: str
    description: str
    params: dict
    run: Callable[..., object]
    optional: tuple = ()
    # A final tool ends the turn with no words (hang_up).
    final: bool = False


@dataclass
class Step:
    thought: str
    tool: str
    args: dict
    result: object


@dataclass
class Reply:
    text: str
    steps: list = field(default_factory=list)


def quoted(text: object) -> str:
    return QUOTE + str(text).replace(QUOTE, "'") + QUOTE


def literal(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        return "{" + ",".join(f"{k}:{literal(v)}" for k, v in sorted(value.items())) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(literal(v) for v in value) + "]"
    return quoted(value)


def declare(tool: Tool) -> str:
    """The tool's declaration in the chat-template spelling: sorted keys, quoted strings, and the template's extra spaces."""
    props = []
    for name, prop in sorted(tool.params.items()):
        parts = [f"description:{quoted(prop['description'])}"]
        if "enum" in prop:
            parts.append("enum:[" + ",".join(quoted(v) for v in prop["enum"]) + "]")
        parts.append(f"type:{quoted(prop['type'])}")
        props.append(f"{name}:{{{','.join(parts)}}}")
    required = [n for n in sorted(tool.params) if n not in tool.optional]
    return (
        f"<|tool>declaration:{tool.name}{{description:{quoted(tool.description)},"
        f"parameters:{{properties:{{{','.join(props)}}} }},required:[{','.join(quoted(n) for n in required)}],type:{quoted('OBJECT')}}} }}<tool|>"
    )


def tool_response(name: str, result: object) -> str:
    body = literal(result) if isinstance(result, dict) else "{value:" + literal(result) + "}"
    return f"{TOOL_RESPONSE_OPEN}response:{name}{body}{TOOL_RESPONSE_CLOSE}"


def turn(role: str, body: str) -> str:
    return f"{TURN_OPEN}{role}\n{body}{TURN_CLOSE}\n"


def plain(text: str) -> str:
    """Words left after the thought channel, the tool call, and the control tokens are gone."""
    text = THOUGHT_RE.sub(" ", text)
    text = CALL_RE.sub(" ", text)
    text = CONTROL_RE.sub(" ", text)
    return " ".join(text.split())


def parse(output: str) -> tuple[str, str, dict]:
    """(thought, tool name, args) from one completion. No call means an empty tool name."""
    thought = " ".join(" ".join(m.group(1).split()) for m in THOUGHT_RE.finditer(output))
    call = CALL_RE.search(output)
    if not call:
        return thought, "", {}
    args = {}
    for key, text, bare in ARG_RE.findall(call.group(2)):
        value = text if text else bare.strip()
        if not text:
            low = value.lower()
            if low in ("true", "false"):
                value = low == "true"
            elif re.fullmatch(r"-?\d+", value):
                value = int(value)
            elif re.fullmatch(r"-?\d+\.\d+", value):
                value = float(value)
        args[key] = value
    return thought, call.group(1), args


class Brain:
    def __init__(self):
        self.url = f"http://{CFG['host']}:{CFG['port']}"
        self.proc = None
        self.marker = None

    # ------------------------------------------------------------------ server

    def media(self) -> str:
        if self.marker is None:
            with urllib.request.urlopen(self.url + "/props", timeout=10) as r:
                self.marker = json.load(r)["media_marker"]
        return self.marker

    def alive(self) -> bool:
        try:
            with urllib.request.urlopen(self.url + "/health", timeout=2) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def start(self):
        if self.alive():
            return
        exe = ROOT / CONFIG["paths"]["bin"] / "llama" / "llama-server.exe"
        if not exe.is_file():
            raise FileNotFoundError(f"{exe} missing: run install.py")
        args = [
            str(exe),
            "--model", str(path_of("brain", "model")),
            "--mmproj", str(path_of("brain", "mmproj")),
            "--host", CFG["host"], "--port", str(CFG["port"]),
            "--ctx-size", str(CFG["context"]), "--parallel", str(CFG["slots"]),
            "--n-gpu-layers", str(CFG["gpu_layers"]), "--threads", str(CFG["threads"]),
            "--flash-attn", "off", "--cache-type-k", "f16", "--cache-type-v", "f16",
            "--image-min-tokens", str(CFG["image_tokens"]), "--image-max-tokens", str(CFG["image_tokens"]),
            "--no-webui", "--log-file", str(state_dir() / "llama-server.log"),
        ]
        self.proc = subprocess.Popen(args, cwd=str(exe.parent), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        LOG.info("llama-server starting pid %d", self.proc.pid)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited {self.proc.returncode}, see state/llama-server.log")
            if self.alive():
                LOG.info("llama-server ready")
                return
            time.sleep(0.5)
        raise TimeoutError("llama-server did not become ready")

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(10)
        self.proc = None

    # ------------------------------------------------------------------ raw completion

    def complete(self, prompt: str, images: list[bytes] = (), schema: dict | None = None, max_tokens: int | None = None, stop: list[str] = ()) -> str:
        body = {
            "prompt": {"prompt_string": prompt, "multimodal_data": [base64.b64encode(i).decode("ascii") for i in images]} if images else prompt,
            "n_predict": max_tokens or CFG["max_tokens"],
            "cache_prompt": True,
            "stop": list(stop),
            "temperature": CFG["temperature"],
            "top_k": CFG["top_k"],
            "top_p": CFG["top_p"],
            "min_p": CFG["min_p"],
        }
        if schema is not None:
            body["json_schema"] = schema
        (state_dir() / "last_prompt.txt").write_text(prompt, encoding="utf-8")
        request = urllib.request.Request(self.url + "/completion", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=600) as r:
            data = json.load(r)
        timings = data.get("timings", {})
        LOG.info("gemma %.1fs prompt %d gen %d", time.monotonic() - started, timings.get("prompt_n", 0), timings.get("predicted_n", 0))
        return data["content"]

    # ------------------------------------------------------------------ vision and structure

    def see(self, png: bytes, question: str) -> str:
        prompt = BOS + turn("user", f"{self.media()}\n{question}") + f"{TURN_OPEN}model\n"
        return plain(self.complete(prompt, images=[png], max_tokens=80))

    def bubbles(self, png: bytes) -> str:
        question = "List every chat bubble from top to bottom. A blue bubble on the right is the user. A reply on the left is the assistant. If a reply continues on the next line, append that line to the same text. The long paragraph on the left is the assistant. Skip the sidebar, the composer, and Task Manager."
        data = self.ask_json(question, BUBBLE_SCHEMA, png)
        return " | ".join(f"{item['who']}: {item['text']}" for item in data["messages"])

    def locate(self, png: bytes, target: str) -> list[dict]:
        """One point on the 1000-grid as [y, x, y, x], the center of the named element."""
        prompt = BOS + turn("user", f'{self.media()}\nPoint at the center of the "{target}" element. y and x are 0 to 1000, origin at the top left, y vertical. The message box is the field at the bottom of a chat where the next message is typed.') + f"{TURN_OPEN}model\n"
        data = json.loads(self.complete(prompt, images=[png], schema=LOCATE_SCHEMA, max_tokens=40))
        return [{"box_2d": [data["y"], data["x"], data["y"], data["x"]], "label": target}]

    def ask_json(self, question: str, schema: dict, png: bytes | None = None) -> object:
        body = f"{self.media()}\n{question}" if png else question
        prompt = BOS + turn("user", body) + f"{TURN_OPEN}model\n"
        return json.loads(self.complete(prompt, images=[png] if png else (), schema=schema, max_tokens=400))

    # ------------------------------------------------------------------ the agent turn

    def think(self, system: str, tools: dict[str, Tool], history: list[tuple[str, str]], user: str, on_step: Callable[[Step], None] | None = None) -> Reply:
        head = turn("system", f"{THINK}\n{system}" + "".join(declare(t) for t in tools.values()))
        past = "".join(turn("user", u) + turn("model", m) for u, m in history)
        prompt = BOS + head + past + turn("user", user) + f"{TURN_OPEN}model\n"
        steps: list[Step] = []
        for _ in range(CFG["max_tool_steps"]):
            out = self.complete(prompt, stop=[TOOL_RESPONSE_OPEN, TURN_CLOSE])
            thought, name, args = parse(out)
            if thought:
                LOG.info("thought: %s", thought)
            if not name:
                text = plain(out)
                LOG.info("say: %s", text)
                return Reply(text, steps)
            if name not in tools:
                result = f"unknown tool {name}"
            else:
                try:
                    result = tools[name].run(**args)
                except Exception as exc:
                    result = f"bad arguments: {exc}"
            step = Step(thought, name, args, result)
            steps.append(step)
            LOG.info("tool %s %s -> %s", name, json.dumps(args, ensure_ascii=False), result)
            if on_step:
                on_step(step)
            if name in tools and tools[name].final:
                return Reply("", steps)
            prompt += out + tool_response(name, result)
        return Reply("I am still working on it.", steps)


def main():
    brain = Brain()
    brain.start()
    question = " ".join(sys.argv[1:]) or "What is the capital of France and what are its GPS coordinates?"
    schema = {"type": "object", "properties": {"capital": {"type": "string"}, "latitude": {"type": "number"}, "longitude": {"type": "number"}}, "required": ["capital", "latitude", "longitude"]}
    print(json.dumps(brain.ask_json(question, schema), indent=2))
    reply = brain.think("You are Gemma. Answer in one short sentence.", {}, [], question)
    print(reply.text)


if __name__ == "__main__":
    main()
