import asyncio
import base64
import binascii
import json
import os
from pathlib import Path


MARKERS = {"quiet", "write", "call", "hang", "again"}
PNG = b"\x89PNG\r\n\x1a\n"
JPEG = b"\xff\xd8\xff"


def command(parts):
    expanded = [os.path.expandvars(str(part)) for part in parts]
    if len(expanded) == 1 and Path(expanded[0]).is_dir():
        root = Path(expanded[0])
        versions = [item for item in root.iterdir() if (item / "node.exe").is_file() and (item / "index.js").is_file()]
        if not versions:
            raise RuntimeError("Cursor CLI is missing")
        latest = max(versions, key=lambda item: item.name)
        return [str(latest / "node.exe"), str(latest / "index.js")]
    return expanded


def image_bytes(text):
    if len(text) < 256:
        return None
    try:
        raw = base64.b64decode(text, validate=True)
    except binascii.Error:
        return None
    if raw.startswith(PNG) or raw.startswith(JPEG):
        return raw
    return None


def disposition(answer):
    lines = (answer or "").splitlines()
    flags = []
    while lines and lines[-1].strip() in MARKERS:
        flags.append(lines.pop().strip())
    return "\n".join(lines).strip(), flags


class Mind:
    def __init__(self, cfg, life, run_dir):
        self.cfg = cfg
        self.life = Path(life)
        self.run_dir = Path(run_dir)
        self.brief = (Path(__file__).resolve().parent / "brief.txt").read_text(encoding="utf-8")
        self.count = 0
        self.images = 0
        self.saved = []

    def check(self):
        parts = command(self.cfg["command"])
        for part in parts:
            if not Path(part).is_file():
                raise RuntimeError(f"Cursor CLI is missing: {part}")
        if self.cfg["model"] != "gpt-5.6-luna-none":
            raise RuntimeError("Luna must be gpt-5.6-luna-none")
        return parts

    def store_image(self, raw):
        folder = self.life / "images"
        folder.mkdir(parents=True, exist_ok=True)
        suffix = ".png" if raw.startswith(PNG) else ".jpg"
        path = folder / f"{self.images}{suffix}"
        self.images += 1
        path.write_bytes(raw)
        self.saved.append(str(path))
        return path.name

    def scrub(self, value):
        if isinstance(value, dict):
            return {key: self.scrub(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.scrub(item) for item in value]
        if isinstance(value, str):
            raw = image_bytes(value)
            if raw is not None:
                return self.store_image(raw)
        return value

    def recent(self, count):
        found = []
        if count <= 0 or not self.life.is_dir():
            return found
        for path in self.life.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                found.append(path)
        found.sort(key=lambda item: item.stat().st_mtime)
        return found[-count:]

    async def turn(self, task, transcript, send, recent_images):
        parts = self.check()
        images = self.recent(int(recent_images))
        argv = [
            *parts, "-p", "--trust", "--force", "--sandbox", "disabled",
            "--model", self.cfg["model"], "--output-format", "stream-json",
            "--workspace", str(self.life),
        ]
        for path in images:
            argv.extend(["--image", str(path)])
        stdin = self.brief + "\n\nTask:\n" + task + "\n\nTranscript:\n" + (transcript or "(empty)") + "\n"
        self.saved = [str(path) for path in images]
        stream = []
        tools = []
        answer = None
        sent = False
        code = None
        err = b""
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(self.life),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )

        async def errs():
            chunks = []
            while True:
                block = await proc.stderr.read(65536)
                if not block:
                    return b"".join(chunks)
                chunks.append(block)

        err_task = asyncio.create_task(errs())
        try:
            proc.stdin.write(stdin.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
            await proc.stdin.wait_closed()

            async def emit_task():
                nonlocal sent
                if not sent:
                    sent = True
                    await send(task)

            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    stream.append(line)
                    raise RuntimeError("Luna stream was not JSON")
                cleaned = self.scrub(event)
                stream.append(line if cleaned == event else json.dumps(cleaned, ensure_ascii=False))
                event = cleaned
                kind = event.get("type")
                if kind == "user":
                    await emit_task()
                elif kind == "tool_call":
                    await emit_task()
                    call_id = event.get("call_id") or event.get("tool_call_id") or ""
                    if event.get("subtype") == "completed":
                        text = f"completed {call_id}".strip()
                    else:
                        body = event.get("tool_call") or {}
                        name = next(iter(body)) if isinstance(body, dict) and body else "tool"
                        spec = body.get(name, {}) if isinstance(body, dict) else {}
                        args = spec.get("args", spec) if isinstance(spec, dict) else spec
                        text = name + " " + json.dumps(args, ensure_ascii=False)
                    tools.append(text)
                    await send(text)
                elif kind == "result":
                    if event.get("is_error"):
                        detail = event.get("result") or "Luna failed"
                        raise RuntimeError(" ".join(str(detail).split()))
                    answer = event.get("result")
            if not sent:
                await emit_task()
            code = await proc.wait()
            err = await err_task
            if not isinstance(answer, str):
                raise RuntimeError(f"Luna exited {code} with no answer")
            return answer, tools
        finally:
            if proc.returncode is None:
                proc.kill()
                code = await proc.wait()
            if not err_task.done():
                err = await err_task
            elif err == b"":
                problem = err_task.exception()
                if problem is None:
                    err = err_task.result()
                else:
                    err = str(problem).encode()
            record = {
                "args": argv,
                "stdin": stdin,
                "images": list(self.saved),
                "stream": stream,
                "stderr": err.decode("utf-8", "replace") if isinstance(err, bytes) else str(err),
                "exit": code if code is not None else proc.returncode,
            }
            path = self.run_dir / str(self.count)
            self.count += 1
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
