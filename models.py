import asyncio
import hashlib
import json
import mmap
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
import win32api
import win32con
import win32job

from log import publish
from store import CONFIG, ROOT, cancel, command, encode, save

_calls = None


def bind_calls(page):
    global _calls
    _calls = page


def calls_page():
    if _calls is None:
        name = os.environ.get("TRIDENT_CALLS", "")
        if not name:
            raise RuntimeError("TRIDENT_CALLS is not set")
        bind_calls(mmap.mmap(-1, 4, tagname=name, access=mmap.ACCESS_WRITE))
    return _calls


def call_count():
    page = calls_page()
    page.seek(0)
    return int.from_bytes(page.read(4), "little")


def stop_if_capped():
    if call_count() >= 100:
        print("Trident shut down: 100 model calls since start.", file=sys.stderr, flush=True)
        raise SystemExit(100)


def charge():
    stop_if_capped()
    page = calls_page()
    count = call_count() + 1
    page.seek(0)
    page.write(count.to_bytes(4, "little"))
    return count


def begin_job():
    job = win32job.CreateJobObject(None, "")
    limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
    return job


async def run_process(parts, data, cwd):
    parts = command(parts)
    job = begin_job()
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *parts, cwd=cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, process.pid)
        try:
            win32job.AssignProcessToJobObject(job, handle)
        finally:
            handle.Close()
        stdout, stderr = await process.communicate(data)
        if process.returncode:
            detail = stderr.decode("utf-8", "replace")[-1500:]
            raise RuntimeError(f"Process exited {process.returncode}: {detail}")
        return stdout.decode("utf-8", "replace")
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        job.Close()


def require_luna():
    parts = command(CONFIG["luna"]["command"])
    for part in parts:
        if not Path(part).is_file():
            raise RuntimeError(f"Cursor CLI is missing: {part}")
    model = CONFIG["luna"]["model"]
    if model != "gpt-5.6-luna-none":
        raise RuntimeError("Luna must be gpt-5.6-luna-none")
    try:
        output = subprocess.check_output(
            [*parts, "--list-models"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"Cursor CLI model list exited {error.returncode}") from None
    offered = {line.split(" - ", 1)[0].strip() for line in output.splitlines() if " - " in line}
    if model not in offered:
        raise RuntimeError(f"Cursor CLI does not offer {model}")


def pictures(life):
    folder = Path(life) / "images"
    if not folder.is_dir():
        return []
    paths = sorted((path for path in folder.glob("*.png") if path.is_file()), key=lambda path: path.stat().st_mtime)
    return [(path, hashlib.sha256(path.read_bytes()).hexdigest()) for path in paths]


def cli_workspace(workspace):
    root = Path(workspace).resolve()
    if root == ROOT:
        return root
    folder = root / "workspace"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def reply(raw):
    result = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if isinstance(event, dict) and event.get("type") == "result":
            result = event
    text = result.get("result") if isinstance(result, dict) and not result.get("is_error") else None
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("Luna returned no successful response")
    return text


async def decision(instruction, context, workspace, life, state, send):
    charge()
    folder = cli_workspace(workspace)
    pairs = pictures(life)
    if pairs:
        await publish(pairs, state.setdefault("sent_images", []), send, lambda: save(life, state))
    parts = [
        *CONFIG["luna"]["command"], "-p", "--trust", "--force", "--sandbox", "disabled",
        "--model", CONFIG["luna"]["model"], "--output-format", "stream-json",
        "--workspace", str(folder),
    ]
    for path, _digest in pairs:
        parts.extend(["--image", str(path)])
    prompt = (
        instruction + "\n\nTrident context:\n" + encode(context)
        + "\n\nThe reply is one JSON object and no other text. The first character is { and the last character is }.\n"
    )
    return reply(await run_process(parts, prompt.encode("utf-8"), folder))


class Models:
    def __init__(self, folder, emit):
        self.folder = Path(folder)
        self.emit = emit
        self.worker = None
        self.watcher = None
        self.http = None

    async def open(self):
        parts = CONFIG["voice"]["command"]
        (ROOT / parts[0]).stat()
        for part in parts[1:]:
            if str(part).startswith("artifacts/"):
                (ROOT / part).stat()
        for part in (CONFIG["ears"]["library"], CONFIG["ears"]["model"]):
            (ROOT / part).stat()
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600))
        endpoint = urlsplit(CONFIG["voice"]["url"])
        with socket.socket() as port:
            port.bind((endpoint.hostname, endpoint.port))
        env = os.environ.copy()
        env["CRISPASR_CHATTERBOX_FORCE_GPU"] = "1"
        self.worker = await asyncio.create_subprocess_exec(
            *command(parts), cwd=ROOT, env=env,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        async with asyncio.timeout(300):
            while True:
                if self.worker.returncode is not None:
                    raise RuntimeError(f"voice worker exited {self.worker.returncode}")
                try:
                    async with self.http.get(CONFIG["voice"]["url"] + "/health") as response:
                        if response.status == 200:
                            break
                        if response.status != 503:
                            raise RuntimeError(await response.text())
                except aiohttp.ClientConnectorError:
                    pass
                await asyncio.sleep(0.1)
        self.watcher = asyncio.create_task(self.exited())

    async def exited(self):
        code = await self.worker.wait()
        self.emit("error", RuntimeError(f"voice worker exited {code}"))

    async def close(self):
        if self.watcher is not None:
            await cancel(self.watcher)
        failures = []
        if self.worker is not None and self.worker.returncode is None:
            try:
                self.worker.kill()
                await self.worker.wait()
            except Exception as error:
                failures.append(str(error))
        if self.http is not None:
            try:
                await self.http.close()
            except Exception as error:
                failures.append(str(error))
        if failures:
            raise RuntimeError("; ".join(failures))

    async def luna(self, context, state, send):
        instruction = (ROOT / "instructions.txt").read_text(encoding="utf-8")
        return await decision(instruction, context, self.folder, self.folder, state, send)

    async def voice(self, text):
        return await self.post("/v1/audio/speech", {"input": text, "response_format": "wav"})

    async def post(self, endpoint, request):
        async with self.http.post(CONFIG["voice"]["url"] + endpoint, json=request) as response:
            payload = await response.read()
            if response.status != 200:
                raise RuntimeError(payload.decode("utf-8", "replace"))
            return payload
