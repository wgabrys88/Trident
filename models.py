import asyncio
import base64
import json
import subprocess
import socket
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
import win32api
import win32con
import win32job

from store import CONFIG, ROOT, cancel, command, encode, read


async def execute(parts, data=b""):
    job = win32job.CreateJobObject(None, "")
    limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
    process = await asyncio.create_subprocess_exec(
        *command(parts), cwd=ROOT, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, process.pid)
        try:
            win32job.AssignProcessToJobObject(job, handle)
        finally:
            handle.Close()
        output, errors = await process.communicate(data)
        if process.returncode:
            raise RuntimeError(f"{parts[0]} exited {process.returncode}: " + (output + errors).decode("utf-8").strip())
        return output
    finally:
        job.Close()
        if process.returncode is None:
            await process.wait()


class Models:
    def __init__(self, record, inbox):
        self.record = record
        self.inbox = inbox
        self.workers = []
        self.watchers = []
        self.http = None

    async def open(self):
        for name in ("gemma", "voice"):
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
        for name in ("gemma", "voice"):
            endpoint = urlsplit(CONFIG[name]["url"])
            with socket.socket() as port:
                port.bind((endpoint.hostname, endpoint.port))
            output = (self.record.folder / f"{name}.log").open("ab")
            process = await asyncio.create_subprocess_exec(
                *command(CONFIG[name]["command"]), cwd=ROOT,
                stdout=output, stderr=output, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.workers.append((process, output))
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
        self.inbox.put_nowait(("error", self.failure(name, code)))

    def failure(self, name, code):
        detail = (self.record.folder / f"{name}.log").read_text(encoding="utf-8")
        return RuntimeError(f"{name} worker exited {code}\n{detail}")

    async def close(self):
        await cancel(*self.watchers)
        for process, output in reversed(self.workers):
            if process.returncode is None:
                process.kill()
                await process.wait()
            output.close()
        if self.http is not None:
            await self.http.close()
        self.record.append("models_closed", {})

    async def luna(self, instruction, context):
        instruction += "\nReturn exactly one JSON object in the requested format, without Markdown or prose outside JSON."
        self.record.append("request", {"system": instruction, "context": context}, "TRIDENT", "LUNA")
        raw = await execute([
            *CONFIG["luna"]["command"], "-p", "--trust", "--model", CONFIG["luna"]["model"],
            "--output-format", "json", "--show-thinking", "--workspace", str(ROOT), "--mode", "ask",
        ], (instruction + "\n\nTrident context:\n" + encode(context)).encode("utf-8"))
        result = json.loads(raw)
        self.record.append("thinking", result["thinking_blocks"], "LUNA", "TRIDENT")
        text = result["result"].strip()
        self.record.append("response", text, "LUNA", "TRIDENT")
        if not text:
            raise RuntimeError("Luna returned an empty response")
        return text

    async def gemma(self, question, images):
        mind = read(ROOT / "mind.json")
        images = [self.record.artifact(payload, "png") for payload in images]
        content = [{"type": "text", "text": encode(question)}]
        for path in images:
            payload = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + payload}})
        request = {
            "model": "gemma", "messages": [
                {"role": "system", "content": mind["student"]},
                {"role": "user", "content": content},
            ], "temperature": 0.2, "max_tokens": 4096,
            "chat_template_kwargs": {"enable_thinking": True},
        }
        self.record.append("request", {"system": mind["student"], "question": question,
                                      "parameters": {key: value for key, value in request.items() if key != "messages"}},
                           "LUNA", "GEMMA", images)
        result = json.loads(await self.post("gemma", "/v1/chat/completions", request, "GEMMA"))
        self.record.append("response", result, "GEMMA", "LUNA")
        choice = result["choices"][0]
        if choice["finish_reason"] != "stop":
            raise RuntimeError(f"Gemma stopped with {choice['finish_reason']}")
        text = choice["message"]["content"].strip()
        if not text:
            raise RuntimeError("Gemma returned an empty response")
        return {"report": text, "images": [str(path) for path in images]}

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
