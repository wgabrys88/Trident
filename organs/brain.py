import base64
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from organs import CONFIG, ROOT, log, path_of, run_dir

LOG = log("brain")
CFG = CONFIG["brain"]

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


@dataclass
class Tool:
    name: str
    description: str
    params: dict
    run: Callable[..., object]
    optional: tuple = ()


class UserTurn:
    def __init__(self, text: str):
        self.text = text


@dataclass
class Reply:
    text: str
    follow: str = ""


def quoted(text: object) -> str:
    return QUOTE + str(text).replace(QUOTE, "'") + QUOTE


def literal(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        return "{" + ",".join(f"{k}:{literal(v)}" for k, v in sorted(value.items())) + "}"
    return quoted(value)


def declare(tool: Tool) -> str:
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
    text = THOUGHT_RE.sub(" ", text)
    text = CALL_RE.sub(" ", text)
    text = CONTROL_RE.sub(" ", text)
    return " ".join(text.split())


def parse(output: str) -> tuple[str, str, dict]:
    thought = "\n".join(m.group(1).strip("\n") for m in THOUGHT_RE.finditer(output))
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
        self.sink: Callable[[str, list[bytes]], None] | None = None

    def emit(self, text: str, images: list[bytes]) -> None:
        if self.sink is not None and (text or images):
            self.sink(text, images)

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
            "--ubatch-size", str(CFG["ubatch"]),
            "--image-min-tokens", str(CFG["image_tokens"]), "--image-max-tokens", str(CFG["image_tokens"]),
            "--no-webui", "--log-file", str(run_dir() / "llama-server.log"),
        ]
        self.proc = subprocess.Popen(args, cwd=str(exe.parent), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        LOG.info("llama-server starting pid %d", self.proc.pid)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited {self.proc.returncode}, see {run_dir() / 'llama-server.log'}")
            if self.alive():
                LOG.info("llama-server ready")
                return
            time.sleep(0.5)
        raise TimeoutError("llama-server did not become ready")

    def stop(self):
        proc = self.proc
        self.proc = None
        self.marker = None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(5)
        if self.alive():
            subprocess.run(["taskkill", "/IM", "llama-server.exe", "/F"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + 20
        while self.alive() and time.monotonic() < deadline:
            time.sleep(0.2)

    def complete(self, prompt: str, images: list[bytes] = (), schema: dict | None = None, stop: list[str] = ()) -> str:
        self.emit(prompt, list(images))
        body = {
            "prompt": {"prompt_string": prompt, "multimodal_data": [base64.b64encode(i).decode("ascii") for i in images]} if images else prompt,
            "n_predict": CFG["max_tokens"],
            "cache_prompt": not images,
            "stop": list(stop),
            "temperature": CFG["temperature"],
            "top_k": CFG["top_k"],
            "top_p": CFG["top_p"],
            "min_p": CFG["min_p"],
        }
        if schema is not None:
            body["json_schema"] = schema
        request = urllib.request.Request(self.url + "/completion", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=600) as r:
            data = json.load(r)
        timings = data.get("timings", {})
        LOG.info("gemma %.1fs prompt %d gen %d", time.monotonic() - started, timings.get("prompt_n", 0), timings.get("predicted_n", 0))
        text = data["content"]
        self.emit(text, [])
        return text

    def ask_json(self, question: str, schema: dict, png: bytes | None = None) -> object:
        body = f"{self.media()}\n{question}" if png else question
        prompt = BOS + turn("user", body) + f"{TURN_OPEN}model\n"
        return json.loads(self.complete(prompt, images=[png] if png else (), schema=schema))

    def think(self, system: str, tools: dict[str, Tool], history: list[tuple[str, str]], user: str) -> Reply:
        head = turn("system", f"{THINK}\n{system}" + "".join(declare(t) for t in tools.values()))
        past = "".join(turn("user", u) + turn("model", m) for u, m in history)
        prompt = BOS + head + past + turn("user", user) + f"{TURN_OPEN}model\n"
        for _ in range(CFG["max_tool_steps"]):
            out = self.complete(prompt, stop=[TOOL_RESPONSE_OPEN, TURN_CLOSE])
            _thought, name, args = parse(out)
            if not name:
                return Reply(plain(out))
            if name not in tools:
                result = f"unknown tool {name}"
            else:
                result = tools[name].run(**args)
            LOG.info("tool %s", name)
            if isinstance(result, UserTurn):
                return Reply("", result.text)
            prompt += out + tool_response(name, result)
        return Reply("I am still working on it.")


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
