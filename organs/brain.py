"""Brain: Gemma 4 E2B behind llama-server, addressed in the exact token format it was trained on.

Two kinds of request leave this file.

  think()   The agent turn. System text + tool declarations + short history, thinking on.
            The model answers with a thought, then either ONE tool call or the words to say.
            Tool calls are executed here and the same model turn continues with a
            <|tool_response>, so thoughts stay in context between calls, as Gemma's docs require.
            The agent never sees pixels and never writes coordinates.

  see() / locate() / ask_json()
            Vision and structured queries. One image, thinking off, output shape forced
            by a JSON schema that mirrors what Gemma emits natively anyway
            ([{"box_2d": [y0, x0, y1, x1], "label": ...}] on a 1000 x 1000 grid).
            Python does every bit of arithmetic afterwards.

This file knows nothing about Telegram, mice or memory. Tools are handed in.
Run alone:  python -m organs.brain "What is the capital of France?"
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
# llama.cpp's multimodal placeholder. mtmd swaps it for Gemma's own image tokens.
MEDIA = "<__media__>"

THOUGHT_RE = re.compile(r"<\|channel>thought\n?(.*?)(?:<channel\|>|$)", re.DOTALL)
CALL_RE = re.compile(r"<\|tool_call>call:(\w+)\{(.*?)\}<tool_call\|>", re.DOTALL)
ARG_RE = re.compile(r'(\w+):(?:<\|"\|>(.*?)<\|"\|>|([^,}]*))', re.DOTALL)
CONTROL_RE = re.compile(r"<\|[a-z_\"]+\|?>|<[a-z_]+\|>|<bos>|<eos>")

LOCATE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "box_2d": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 1000}, "minItems": 4, "maxItems": 4},
            "label": {"type": "string"},
        },
        "required": ["box_2d", "label"],
    },
}


@dataclass
class Tool:
    """One function Gemma may call. params: name -> {"description": str, "type": "STRING"|"INTEGER"|"NUMBER"|"BOOLEAN", "enum": [...]}."""

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
    """Render a declaration byte-for-byte like Google's chat template does (sorted keys, the stray spaces included)."""
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
    body = result if isinstance(result, dict) else {"result": result}
    return f"{TOOL_RESPONSE_OPEN}response:{name}{literal(body)}{TOOL_RESPONSE_CLOSE}"


def turn(role: str, body: str) -> str:
    return f"{TURN_OPEN}{role}\n{body}{TURN_CLOSE}\n"


def plain(text: str) -> str:
    """Spoken text only: thoughts, tool calls and control tokens removed."""
    text = THOUGHT_RE.sub(" ", text)
    text = CALL_RE.sub(" ", text)
    text = CONTROL_RE.sub(" ", text)
    return " ".join(text.split())


def parse(output: str) -> tuple[str, str, dict]:
    """-> (thought, tool name or "", args)."""
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

    # ------------------------------------------------------------------ server

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
        prompt = BOS + turn("user", f"{MEDIA}\n{question}") + f"{TURN_OPEN}model\n"
        return plain(self.complete(prompt, images=[png], max_tokens=300))

    def locate(self, png: bytes, target: str) -> list[dict]:
        """Boxes on Gemma's native 1000 x 1000 grid: [{"box_2d": [y0, x0, y1, x1], "label": str}]. Empty when absent."""
        prompt = BOS + turn("user", f"{MEDIA}\nDetect the {target}. Return the bounding box of that element only, or an empty list if it is not visible.") + f"{TURN_OPEN}model\n"
        out = self.complete(prompt, images=[png], schema=LOCATE_SCHEMA, max_tokens=120)
        return json.loads(out)

    def ask_json(self, question: str, schema: dict, png: bytes | None = None) -> object:
        body = f"{MEDIA}\n{question}" if png else question
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
                LOG.info("thought: %s", thought[:600])
            if not name:
                text = plain(out)
                LOG.info("say: %s", text)
                return Reply(text, steps)
            if name not in tools:
                result = f"unknown tool {name}"
            else:
                try:
                    result = tools[name].run(**args)
                except TypeError as exc:
                    result = f"bad arguments: {exc}"
            step = Step(thought, name, args, result)
            steps.append(step)
            LOG.info("tool %s %s -> %s", name, json.dumps(args, ensure_ascii=False)[:200], str(result)[:200])
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
