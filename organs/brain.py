import base64
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from organs import CONFIG, ROOT, path_of
CFG = CONFIG["brain"]
BOS, TURN_OPEN, TURN_CLOSE, THINK = "<bos>", "<|turn>", "<turn|>", "<|think|>"
QUOTE, TOOL_RESPONSE_OPEN, TOOL_RESPONSE_CLOSE = '<|"|>', "<|tool_response>", "<tool_response|>"
THOUGHT_RE = re.compile(r"<\|channel>thought\n?(.*?)(?:<channel\|>|$)", re.DOTALL)
CALL_RE = re.compile(r"<\|tool_call>call:(\w+)\{(.*?)\}<tool_call\|>", re.DOTALL)
ARG_RE = re.compile(r'(\w+):(?:<\|"\|>(.*?)<\|"\|>|([^,}]*))', re.DOTALL)
CONTROL_RE = re.compile(r"<\|[a-z_\"]+\|?>|<[a-z_]+\|>|<bos>|<eos>")
_BRAIN, BILLS = None, 0
@dataclass
class Tool:
    name: str
    description: str
    params: dict
    run: Callable[..., object]
    optional: tuple = ()
class Stop:
    pass
@dataclass
class Reply:
    text: str = ""
    prompt: str = ""
    stop: bool = False
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
    for name, prop in tool.params.items():
        parts = [f"description:{quoted(prop['description'])}"]
        if "enum" in prop:
            parts.append("enum:[" + ",".join(quoted(v) for v in prop["enum"]) + "]")
        parts.append(f"type:{quoted(prop['type'])}")
        props.append(f"{name}:{{{','.join(parts)}}}")
    required = [n for n in tool.params if n not in tool.optional]
    return (
        f"<|tool>declaration:{tool.name}{{description:{quoted(tool.description)},"
        f"parameters:{{properties:{{{','.join(props)}}} }},required:[{','.join(quoted(n) for n in required)}],type:{quoted('OBJECT')}}} }}<tool|>"
    )
def tool_response(name: str, result: object) -> str:
    body = literal(result) if isinstance(result, dict) else "{value:" + literal(result) + "}"
    return f"{TOOL_RESPONSE_OPEN}response:{name}{body}{TOOL_RESPONSE_CLOSE}"
def turn(role: str, body: str) -> str:
    return f"{TURN_OPEN}{role}\n{body}{TURN_CLOSE}\n"
def kill_tree() -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(os.getpid())], creationflags=subprocess.CREATE_NO_WINDOW)
    os._exit(1)
def charge() -> None:
    global BILLS
    if BILLS >= int(CFG["request_limit"]):
        try:
            if _BRAIN is not None:
                _BRAIN.emit(f"request limit {CFG['request_limit']}", [])
        finally:
            kill_tree()
    BILLS += 1
def cursor_text(folder: Path, prompt: str) -> str:
    charge()
    root = Path(os.environ["LOCALAPPDATA"]) / "cursor-agent" / "versions"
    version = max(p for p in root.iterdir() if p.name[:1].isdigit() and (p / "node.exe").is_file())
    done = subprocess.run(
        [str(version / "node.exe"), str(version / "index.js"), "-p", "--mode", "ask", "--trust", "--model", CONFIG["cloud"]["model"], "--output-format", "text", "--workspace", str(folder), prompt],
        capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=1800, cwd=str(folder), creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr + done.stdout)
    if not done.stdout.strip():
        raise RuntimeError("The advisor returned nothing.")
    return done.stdout
def plain(text: str) -> str:
    text = CONTROL_RE.sub(" ", CALL_RE.sub(" ", THOUGHT_RE.sub(" ", text)))
    return " ".join(text.split())
def parse(output: str) -> tuple[str, dict]:
    call = CALL_RE.search(output)
    if not call:
        return "", {}
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
    return call.group(1), args
class Brain:
    def __init__(self):
        global _BRAIN
        _BRAIN = self
        self.url = f"http://{CFG['host']}:{CFG['port']}"
        self.proc = self.marker = self.sink = None
        self.sent = ""
        self.prompt_tokens = self.shown = 0
        self.frames: list[bytes] = []
    def fresh(self) -> None:
        self.sent = ""
        self.prompt_tokens = self.shown = 0
        self.frames.clear()
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
            str(exe), "--model", str(path_of("brain", "model")), "--mmproj", str(path_of("brain", "mmproj")),
            "--host", CFG["host"], "--port", str(CFG["port"]), "--ctx-size", str(CFG["context"]), "--parallel", str(CFG["slots"]),
            "--n-gpu-layers", str(CFG["gpu_layers"]), "--threads", str(CFG["threads"]), "--flash-attn", "off",
            "--cache-type-k", "f16", "--cache-type-v", "f16", "--ubatch-size", str(CFG["ubatch"]),
            "--image-min-tokens", str(CFG["image_tokens"]), "--image-max-tokens", str(CFG["image_tokens"]), "--no-webui",
        ]
        self.proc = subprocess.Popen(args, cwd=str(exe.parent), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited {self.proc.returncode}")
            if self.alive():
                return
            time.sleep(0.5)
        raise TimeoutError("llama-server did not become ready")
    def stop(self):
        proc, self.proc, self.marker = self.proc, None, None
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
    def complete(self, prompt: str, images: list[bytes] = (), schema: dict | None = None, stop: list[str] = (), track: bool = True) -> str:
        charge()
        frames = list(images)
        continuing = bool(track and self.sent and prompt.startswith(self.sent))
        self.emit(prompt[len(self.sent):] if continuing else prompt, frames[self.shown:] if continuing else frames)
        body = {
            "prompt": {"prompt_string": prompt, "multimodal_data": [base64.b64encode(i).decode("ascii") for i in frames]} if frames else prompt,
            "n_predict": CFG["max_tokens"], "cache_prompt": not frames, "stop": list(stop),
            "temperature": CFG["temperature"], "top_k": CFG["top_k"], "top_p": CFG["top_p"], "min_p": CFG["min_p"],
        }
        if schema is not None:
            body["json_schema"] = schema
        request = urllib.request.Request(self.url + "/completion", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=600) as r:
                data = json.load(r)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").strip()
            raise RuntimeError(detail or str(exc))
        text = data["content"]
        self.emit(text, [])
        if track:
            self.sent, self.shown = prompt + text, len(frames)
            self.prompt_tokens = int(data.get("tokens_evaluated") or 0) - len(frames) * int(CFG["image_tokens"])
        return text
    def think(self, system: str, tools: dict[str, Tool], user: str, prompt: str = "") -> Reply:
        if not prompt:
            self.fresh()
            prompt = BOS + turn("system", f"{THINK}\n{system}" + "".join(declare(t) for t in tools.values())) + turn("user", user) + f"{TURN_OPEN}model\n"
        out = self.complete(prompt, images=self.frames, stop=[TOOL_RESPONSE_OPEN, TURN_CLOSE])
        name, args = parse(out)
        if not name:
            return Reply(plain(out))
        try:
            if name not in tools:
                raise ValueError(f"unknown tool {name}")
            result = invoke(tools[name], args)
        except Exception as exc:
            result = str(exc)
        if isinstance(result, Stop):
            return Reply(stop=True)
        return Reply(prompt=prompt + out + tool_response(name, result))
def invoke(tool: Tool, args: dict) -> object:
    unknown = next((key for key in args if key not in tool.params), "")
    if unknown:
        raise ValueError(f"unknown argument {unknown}")
    missing = next((key for key in tool.params if key not in tool.optional and key not in args), "")
    if missing:
        raise ValueError(f"missing {missing}")
    for key, value in args.items():
        allowed = tool.params[key].get("enum")
        if allowed is not None and value not in allowed:
            raise ValueError(f"invalid {key}")
    return tool.run(**args)
