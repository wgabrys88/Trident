import asyncio
import base64
import subprocess
import socket
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
import win32api
import win32con
import win32job

from store import CONFIG, ROOT, command, encode, read


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
        for part in (CONFIG["ears"]["command"], CONFIG["ears"]["model"]):
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
        for watcher in self.watchers:
            watcher.cancel()
        for watcher in self.watchers:
            try:
                await watcher
            except asyncio.CancelledError:
                pass
        for process, output in reversed(self.workers):
            if process.returncode is None:
                process.kill()
                await process.wait()
            output.close()
        if self.http is not None:
            await self.http.close()

    async def luna(self, instruction, context):
        request = (
            instruction + "\n\nTrident context:\n" + encode(context) +
            "\n\nReturn exactly one JSON object in the requested format. "
            "The entire response is parsed as JSON. No Markdown fences, explanation, or proposed actions outside JSON."
        )
        path = self.record.folder / ("luna-" + uuid.uuid4().hex + ".request.txt")
        path.write_text(request, encoding="utf-8")
        self.record.append("luna_request", {"path": str(path)})
        raw = await execute([
            *CONFIG["luna"]["command"], "-p", "--trust", "--model", CONFIG["luna"]["model"],
            "--output-format", "text", "--workspace", str(ROOT), "--mode", "ask",
        ], request.encode("utf-8"))
        text = raw.decode("utf-8").strip()
        if not text:
            raise RuntimeError("Luna returned an empty response")
        self.record.append("luna_response", text)
        return text

    async def gemma(self, question, images):
        mind = read(ROOT / "mind.json")
        content = [{"type": "text", "text": question}]
        for path in images:
            payload = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + payload}})
        self.record.append("gemma_request", {"question": question, "images": [str(p) for p in images]})
        async with self.http.post(CONFIG["gemma"]["url"] + "/v1/chat/completions", json={
            "model": "gemma", "messages": [
                {"role": "system", "content": mind["student"]},
                {"role": "user", "content": content},
            ], "temperature": 0.2, "max_tokens": 4096,
            "chat_template_kwargs": {"enable_thinking": False},
        }) as response:
            if response.status != 200:
                raise RuntimeError(await response.text())
            result = await response.json()
        choice = result["choices"][0]
        if choice["finish_reason"] != "stop":
            raise RuntimeError(f"Gemma stopped with {choice['finish_reason']}")
        text = choice["message"]["content"].strip()
        if not text:
            raise RuntimeError("Gemma returned an empty response")
        self.record.append("gemma_response", text)
        return text

    async def voice(self, text):
        async with self.http.post(CONFIG["voice"]["url"] + "/v1/audio/speech", json={
            "input": text, "response_format": "wav",
        }) as response:
            if response.status != 200:
                raise RuntimeError(await response.text())
            return await response.read()
