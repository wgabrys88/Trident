import base64
import io
import json
import math
import re
import struct
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
SEE = "Mark only what is asked. This picture may be a desktop, a web page, a game, or a camera. bbox_2d is x1, y1, x2, y2 in pixels of this picture, origin top left, x to the right and y down. label is what is drawn. If it is absent, items is empty. Do not invent a box.\n"
MARKS = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}, "bbox_2d": {"type": "array", "items": {"type": "integer"}}}, "required": ["label", "bbox_2d"]}}}, "required": ["items"]}


def _f32(value: float) -> float:
    return struct.unpack("f", struct.pack("f", float(value)))[0]


def _canvas(width: int, height: int) -> tuple[int, int, int, int, int]:
    factor = 28
    cap = int(CONFIG["vision"]["image_tokens"]) * factor * factor

    def c_round(x: float) -> int:
        return int(math.floor(_f32(_f32(x) / _f32(factor)) + 0.5)) * factor

    def by(fn, x: float) -> int:
        return int(fn(_f32(_f32(x) / _f32(factor)))) * factor

    w_bar, h_bar = max(factor, c_round(width)), max(factor, c_round(height))
    if h_bar * w_bar > cap:
        beta = _f32(math.sqrt(_f32(_f32(_f32(height) * _f32(width)) / _f32(cap))))
        h_bar, w_bar = max(factor, by(math.floor, _f32(_f32(height) / beta))), max(factor, by(math.floor, _f32(_f32(width) / beta)))
    elif h_bar * w_bar < cap:
        beta = _f32(math.sqrt(_f32(_f32(cap) / _f32(_f32(height) * _f32(width)))))
        h_bar, w_bar = by(math.ceil, _f32(_f32(height) * beta)), by(math.ceil, _f32(_f32(width) * beta))
    scale = min(_f32(_f32(w_bar) / _f32(width)), _f32(_f32(h_bar) / _f32(height)))
    content_w = min(int(math.ceil(_f32(_f32(width) * scale))), w_bar)
    content_h = min(int(math.ceil(_f32(_f32(height) * scale))), h_bar)
    return (w_bar // factor) * (h_bar // factor), (w_bar - content_w) // 2, (h_bar - content_h) // 2, content_w, content_h


def _fit(png: bytes) -> bytes:
    width, height = struct.unpack(">II", png[16:24])
    limit = int(CONFIG["vision"]["image_tokens"])
    if _canvas(width, height)[0] <= limit:
        return png
    scale, nw, nh = 1.0, width, height
    while scale > 0.25 and _canvas(nw, nh)[0] > limit:
        scale *= 0.92
        nw, nh = max(28, int(width * scale)), max(28, int(height * scale))
    from PIL import Image
    image = Image.open(io.BytesIO(png)).convert("RGB").resize((nw, nh), Image.BICUBIC)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _items(text: str, png: bytes) -> list:
    raw = text.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            return []
    rows = data.get("items", []) if isinstance(data, dict) else []
    width, height = struct.unpack(">II", png[16:24])
    _, off_x, off_y, content_w, content_h = _canvas(width, height)
    found = []
    for item in rows if isinstance(rows, list) else []:
        box = item.get("bbox_2d") if isinstance(item, dict) else None
        if not isinstance(box, list) or len(box) != 4:
            continue
        try:
            x0, y0, x1, y1 = (float(v) for v in box)
            name = " ".join(str(item.get("label", "")).split())[:40]
        except (TypeError, ValueError):
            continue
        def axis(value: float, off: int, span: int) -> int:
            return min(1000, max(0, int(round((value - off) / span * 1000))))
        grid = [axis(min(y0, y1), off_y, content_h), axis(min(x0, x1), off_x, content_w), axis(max(y0, y1), off_y, content_h), axis(max(x0, x1), off_x, content_w)]
        if name and grid[2] > grid[0] and grid[3] > grid[1]:
            found.append({"name": name, "y0": grid[0], "x0": grid[1], "y1": grid[2], "x1": grid[3]})
    return found

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
            ctx, slots, ubatch, image_tokens = CFG["context"], CFG["slots"], CFG["ubatch"], CFG["image_tokens"]
        else:
            vis = CONFIG["vision"]
            ctx, slots, ubatch, image_tokens = vis["context"], vis["slots"], vis["ubatch"], vis["image_tokens"]
        args = [
            str(exe),
            "--model", str(path_of(section, "model")),
            "--mmproj", str(path_of(section, "mmproj")),
            "--host", CFG["host"], "--port", str(CFG["port"]),
            "--ctx-size", str(ctx), "--parallel", str(slots),
            "--n-gpu-layers", str(CFG["gpu_layers"]), "--threads", str(CFG["threads"]),
            "--flash-attn", "off", "--cache-type-k", "f16", "--cache-type-v", "f16",
            "--ubatch-size", str(ubatch),
            "--image-min-tokens", str(image_tokens), "--image-max-tokens", str(image_tokens),
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

    def _erase(self):
        for slot in (0, 1):
            try:
                request = urllib.request.Request(f"{self.url}/slots/{slot}?action=erase", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
                urllib.request.urlopen(request, timeout=5).read()
            except (urllib.error.URLError, OSError):
                continue

    def _see(self, png: bytes, question: str) -> list:
        png = _fit(png)
        self._erase()
        body = {
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}},
                {"type": "text", "text": SEE + question},
            ]}],
            "temperature": CFG["temperature"],
            "top_k": CFG["top_k"],
            "top_p": CFG["top_p"],
            "min_p": CFG["min_p"],
            "max_tokens": 1536,
            "cache_prompt": False,
            "response_format": {"type": "json_schema", "json_schema": {"name": "marks", "strict": True, "schema": MARKS}},
        }
        request = urllib.request.Request(self.url + "/v1/chat/completions", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            if "response_format" not in body:
                raise RuntimeError(exc.read().decode("utf-8", "replace")[:400]) from exc
            LOG.info("see schema %s", exc.read().decode("utf-8", "replace")[:200])
            body.pop("response_format")
            request = urllib.request.Request(self.url + "/v1/chat/completions", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=600) as response:
                data = json.load(response)
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
        text = str(content).strip()
        LOG.info("see %.1fs %s", time.monotonic() - started, text[:240].replace("\n", " "))
        return _items(text, png)

    def survey(self, png: bytes, question: str) -> list:
        from organs.eyes import crop, embed
        self.stop()
        self.start("vision")
        started = time.monotonic()
        try:
            found = []
            for item in self._see(png, question)[:32]:
                pad = [max(0, item["y0"] - 80), max(0, item["x0"] - 80), min(1000, item["y1"] + 80), min(1000, item["x1"] + 80)]
                closer = self._see(crop(png, pad, False), item["name"])
                picked = item
                if closer:
                    back = embed(pad, [closer[0]["y0"], closer[0]["x0"], closer[0]["y1"], closer[0]["x1"]])
                    refined = {"name": item["name"], **{key: min(1000, max(0, int(round(back[i])))) for i, key in enumerate(("y0", "x0", "y1", "x1"))}}
                    if refined["y1"] > refined["y0"] and refined["x1"] > refined["x0"]:
                        picked = refined
                found.append(picked)
            LOG.info("survey %.1fs %s", time.monotonic() - started, ", ".join(item["name"] for item in found) or "none")
            return found
        finally:
            self.stop()
            self.start()

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
        (run_dir() / "last_prompt.txt").write_text(prompt, encoding="utf-8")
        if images:
            (run_dir() / "last_image.png").write_bytes(images[0])
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
                nudge = on_step(Step(thought, "", {})) if on_step else None
                if nudge:
                    LOG.info("hold: %s", nudge)
                    prompt += out + turn("user", nudge) + f"{TURN_OPEN}model\n"
                    continue
                LOG.info("say: %s", text)
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
