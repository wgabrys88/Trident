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

from organs import CONFIG, ROOT, log, path_of, state_dir

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
    final: bool = False


@dataclass
class Step:
    thought: str
    tool: str
    args: dict


@dataclass
class Reply:
    text: str


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

    def start(self, section: str = "brain"):
        if self.alive():
            return
        exe = ROOT / CONFIG["paths"]["bin"] / "llama" / "llama-server.exe"
        if not exe.is_file():
            raise FileNotFoundError(f"{exe} missing: run install.py")
        if section == "brain":
            ctx, slots, ubatch = CFG["context"], CFG["slots"], CFG["ubatch"]
        else:
            vis = CONFIG["vision"]
            ctx, slots, ubatch = vis["context"], vis["slots"], vis["ubatch"]
        args = [
            str(exe),
            "--model", str(path_of(section, "model")),
            "--mmproj", str(path_of(section, "mmproj")),
            "--host", CFG["host"], "--port", str(CFG["port"]),
            "--ctx-size", str(ctx), "--parallel", str(slots),
            "--n-gpu-layers", str(CFG["gpu_layers"]), "--threads", str(CFG["threads"]),
            "--flash-attn", "off", "--cache-type-k", "f16", "--cache-type-v", "f16",
            "--ubatch-size", str(ubatch),
            "--no-webui", "--log-file", str(state_dir() / "llama-server.log"),
        ]
        if section == "brain":
            args += ["--image-min-tokens", str(CFG["image_tokens"]), "--image-max-tokens", str(CFG["image_tokens"])]
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
        self.marker = None

    def survey(self, png: bytes, question: str) -> str:
        self.stop()
        self.start("vision")
        try:
            body = {
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}},
                    {"type": "text", "text": question + "\nList every interactive control and what is happening. The scene may be a desktop, a movie, a camera feed, or a game."},
                ]}],
                "temperature": CFG["temperature"],
                "top_k": CFG["top_k"],
                "top_p": CFG["top_p"],
                "min_p": CFG["min_p"],
                "max_tokens": CFG["max_tokens"],
            }
            request = urllib.request.Request(self.url + "/v1/chat/completions", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
            started = time.monotonic()
            with urllib.request.urlopen(request, timeout=600) as response:
                data = json.load(response)
            text = data["choices"][0]["message"]["content"].strip()
            LOG.info("survey %.1fs %d chars", time.monotonic() - started, len(text))
        finally:
            self.stop()
            self.start()
        return text

    def complete(self, prompt: str, images: list[bytes] = (), schema: dict | None = None, stop: list[str] = ()) -> str:
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
        (state_dir() / "last_prompt.txt").write_text(prompt, encoding="utf-8")
        if images:
            (state_dir() / "last_image.png").write_bytes(images[0])
        request = urllib.request.Request(self.url + "/completion", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=600) as r:
            data = json.load(r)
        timings = data.get("timings", {})
        LOG.info("gemma %.1fs prompt %d gen %d", time.monotonic() - started, timings.get("prompt_n", 0), timings.get("predicted_n", 0))
        return data["content"]

    def ask_json(self, question: str, schema: dict, png: bytes | None = None) -> object:
        body = f"{self.media()}\n{question}" if png else question
        prompt = BOS + turn("user", body) + f"{TURN_OPEN}model\n"
        return json.loads(self.complete(prompt, images=[png] if png else (), schema=schema))

    def think(self, system: str, tools: dict[str, Tool], history: list[tuple[str, str]], user: str, on_step: Callable[[Step], None] | None = None) -> Reply:
        head = turn("system", f"{THINK}\n{system}" + "".join(declare(t) for t in tools.values()))
        past = "".join(turn("user", u) + turn("model", m) for u, m in history)
        prompt = BOS + head + past + turn("user", user) + f"{TURN_OPEN}model\n"
        for _ in range(CFG["max_tool_steps"]):
            out = self.complete(prompt, stop=[TOOL_RESPONSE_OPEN, TURN_CLOSE])
            thought, name, args = parse(out)
            if thought:
                LOG.info("thought: %s", thought)
            if not name:
                text = plain(out)
                LOG.info("say: %s", text)
                if on_step and thought:
                    on_step(Step(thought, "", {}))
                return Reply(text)
            if on_step:
                on_step(Step(thought, name, args))
            if name not in tools:
                result = f"unknown tool {name}"
            else:
                try:
                    result = tools[name].run(**args)
                except Exception as exc:
                    result = f"bad arguments: {exc}"
            LOG.info("tool %s %s -> %s", name, json.dumps(args, ensure_ascii=False), result)
            if name in tools and tools[name].final:
                return Reply("")
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
