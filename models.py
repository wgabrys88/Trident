import asyncio
import json
import os
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from process import execute
from store import CONFIG, ROOT, cancel, command, encode


async def decision(record, instruction, context, workspace, low=False):
    record.append("request", {"system": instruction, "context": context}, "TRIDENT", "LUNA")
    raw = await execute([
        *CONFIG["luna"]["command"], "-p", "--trust", "--force", "--model", CONFIG["luna"]["model"],
        "--output-format", "stream-json", "--workspace", str(workspace),
    ], (instruction + "\n\nTrident context:\n" + encode(context)).encode("utf-8"),
        record.folder, record, low=low, stream=True)
    events = []
    for line in raw.splitlines():
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
        except json.JSONDecodeError:
            pass  # The complete diagnostic line is already in the canonical stream.
    result = next((event for event in reversed(events) if event.get("type") == "result"), None)
    if not result or result.get("is_error") or not isinstance(result.get("result"), str) or not result["result"].strip():
        raise RuntimeError("Luna returned no successful executable response; see the stream record")
    record.append("response", result, "LUNA", "TRIDENT")
    return result["result"]


class Models:
    def __init__(self, record, emit):
        self.record = record
        self.emit = emit
        self.workers = []
        self.watchers = []
        self.http = None

    async def open(self):
        for name in ("voice",):
            parts = CONFIG[name]["command"]
            (ROOT / parts[0]).stat()
            for part in parts[1:]:
                if part.startswith("artifacts/"):
                    (ROOT / part).stat()
        for part in (CONFIG["ears"]["library"], CONFIG["ears"]["model"]):
            (ROOT / part).stat()
        for part in command(CONFIG["luna"]["command"]):
            Path(part).stat()
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600))
        for name in ("voice",):
            endpoint = urlsplit(CONFIG[name]["url"])
            with socket.socket() as port:
                port.bind((endpoint.hostname, endpoint.port))
            output = (self.record.folder / f"{name}.log").open("ab")
            env = os.environ.copy()
            if name == "voice":
                env["CRISPASR_CHATTERBOX_FORCE_GPU"] = "1"
            try:
                process = await asyncio.create_subprocess_exec(
                    *command(CONFIG[name]["command"]), cwd=ROOT, env=env,
                    stdout=output, stderr=output, creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except BaseException:
                output.close()
                raise
            self.workers.append((process, output))
            self.record.append("worker_started", {"worker": name, "pid": process.pid, "log": output.name})
            async with asyncio.timeout(300):
                while True:
                    if process.returncode is not None:
                        raise self.failure(name, process.returncode)
                    try:
                        async with self.http.get(CONFIG[name]["url"] + "/health") as response:
                            if response.status == 200:
                                break
                            if response.status != 503:
                                raise RuntimeError(await response.text())
                    except aiohttp.ClientConnectorError:
                        pass
                    await asyncio.sleep(0.1)
            self.watchers.append(asyncio.create_task(self.watch(name, process)))

    async def watch(self, name, process):
        code = await process.wait()
        self.emit("error", self.failure(name, code))

    def failure(self, name, code):
        detail = (self.record.folder / f"{name}.log").read_text(encoding="utf-8")
        return RuntimeError(f"{name} worker exited {code}\n{detail}")

    async def close(self):
        await cancel(*self.watchers)
        failures = []
        for process, output in reversed(self.workers):
            try:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            except Exception as error:
                failures.append(str(error))
            try:
                output.close()
                log = self.record.artifact(Path(output.name).read_bytes(), "log")
                self.record.append("worker_closed", {"exit": process.returncode}, images=[str(log)])
            except Exception as error:
                failures.append(str(error))
        if self.http is not None:
            try:
                await self.http.close()
            except Exception as error:
                failures.append(str(error))
        self.record.append("models_closed", {"failures": failures})
        if failures:
            raise RuntimeError("; ".join(failures))

    async def luna(self, context):
        instruction = (ROOT / "instructions.txt").read_text(encoding="utf-8")
        return await decision(self.record, instruction, context, self.record.folder, low=True)

    async def voice(self, text):
        request = {"input": text, "response_format": "wav"}
        self.record.append("request", request, "LUNA", "CHATTERBOX")
        path = self.record.artifact(await self.post("voice", "/v1/audio/speech", request, "CHATTERBOX"), "wav")
        self.record.append("voice_ready", {"audio": str(path), "words": text}, "CHATTERBOX", "LUNA")
        return path

    async def post(self, name, endpoint, request, source):
        async with self.http.post(CONFIG[name]["url"] + endpoint, json=request) as response:
            payload = await response.read()
            if response.status != 200:
                text = payload.decode("utf-8")
                self.record.append("response", text, source, "LUNA")
                raise RuntimeError(text)
            return payload
