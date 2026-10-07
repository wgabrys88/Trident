import asyncio
import json
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
import win32api
import win32con
import win32job

from store import CONFIG, ROOT, cancel, command, encode


def begin_job():
    job = win32job.CreateJobObject(None, "")
    limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
    return job


async def run_process(record, parts, data, cwd, stream=False):
    parts = command(parts)
    job = begin_job()
    process = None
    stdout, stderr = [], []

    async def pump(pipe, bucket, channel):
        pending = b""

        def take(raw):
            text = raw.decode("utf-8", "replace").rstrip("\r")
            bucket.append(text)
            if stream and channel == "stdout":
                try:
                    value = json.loads(text)
                except json.JSONDecodeError:
                    value = text
                record.append("model_event", {"channel": channel, "data": value})

        while chunk := await pipe.read(65536):
            pending += chunk
            while b"\n" in pending:
                raw, pending = pending.split(b"\n", 1)
                take(raw)
        if pending:
            take(pending)

    try:
        process = await asyncio.create_subprocess_exec(
            *parts, cwd=cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, process.pid)
            try:
                win32job.AssignProcessToJobObject(job, handle)
            finally:
                handle.Close()
        except Exception as error:
            record.append("process_output", {"channel": "job", "data": str(error)})
        try:
            process.stdin.write(data)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        process.stdin.close()
        await asyncio.gather(pump(process.stdout, stdout, "stdout"), pump(process.stderr, stderr, "stderr"))
        code = await process.wait()
        if stderr:
            record.append("process_output", {"channel": "stderr", "data": "\n".join(stderr)[-8000:]})
        record.append("process_exit", {"exit": code})
        if code:
            raise RuntimeError(f"Process exited {code}: " + "\n".join(stderr)[-1500:])
        return "\n".join(stdout)
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        try:
            job.Close()
        except Exception:
            pass


async def decision(record, instruction, context, workspace):
    record.append("request", {"system": instruction, "context": context}, "TRIDENT", "LUNA")
    raw = await run_process(record, [
        *CONFIG["luna"]["command"], "-p", "--trust", "--force", "--model", CONFIG["luna"]["model"],
        "--output-format", "stream-json", "--workspace", str(workspace),
    ], (instruction + "\n\nTrident context:\n" + encode(context)).encode("utf-8"), workspace, stream=True)
    events = []
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    result = next((event for event in reversed(events) if event.get("type") == "result"), None)
    if not result or result.get("is_error") or not isinstance(result.get("result"), str) or not result["result"].strip():
        raise RuntimeError("Luna returned no successful executable response")
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
                if str(part).startswith("artifacts/"):
                    (ROOT / part).stat()
        for part in (CONFIG["ears"]["library"], CONFIG["ears"]["model"]):
            (ROOT / part).stat()
        for part in command(CONFIG["luna"]["command"]):
            Path(part).stat()
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600))
        endpoint = urlsplit(CONFIG["voice"]["url"])
        with socket.socket() as port:
            port.bind((endpoint.hostname, endpoint.port))
        output = (self.record.folder / "voice.log").open("ab")
        env = os.environ.copy()
        env["CRISPASR_CHATTERBOX_FORCE_GPU"] = "1"
        try:
            process = await asyncio.create_subprocess_exec(
                *command(CONFIG["voice"]["command"]), cwd=ROOT, env=env,
                stdout=output, stderr=output, creationflags=subprocess.CREATE_NO_WINDOW)
        except BaseException:
            output.close()
            raise
        self.workers.append((process, output))
        self.record.append("worker_started", {"worker": "voice", "pid": process.pid, "log": output.name})
        async with asyncio.timeout(300):
            while True:
                if process.returncode is not None:
                    raise self.failure("voice", process.returncode)
                try:
                    async with self.http.get(CONFIG["voice"]["url"] + "/health") as response:
                        if response.status == 200:
                            break
                        if response.status != 503:
                            raise RuntimeError(await response.text())
                except aiohttp.ClientConnectorError:
                    pass
                await asyncio.sleep(0.1)
        self.watchers.append(asyncio.create_task(self.watch("voice", process)))

    async def watch(self, name, process):
        code = await process.wait()
        self.emit("error", self.failure(name, code))

    def failure(self, name, code):
        detail = (self.record.folder / f"{name}.log").read_text(encoding="utf-8", errors="replace")[-2000:]
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
                self.record.append("worker_closed", {"exit": process.returncode, "log": str(log)})
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
        scratch = Path(tempfile.mkdtemp(prefix="trident-"))
        try:
            for path in self.record.folder.glob("*.png"):
                shutil.copyfile(path, scratch / path.name)
            return await decision(self.record, instruction, context, scratch)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    async def voice(self, text):
        request = {"input": text, "response_format": "wav"}
        self.record.append("request", request, "LUNA", "CHATTERBOX")
        path = self.record.artifact(await self.post("/v1/audio/speech", request), "wav")
        self.record.append("voice_ready", {"audio": str(path), "words": text}, "CHATTERBOX", "LUNA")
        return path

    async def post(self, endpoint, request):
        async with self.http.post(CONFIG["voice"]["url"] + endpoint, json=request) as response:
            payload = await response.read()
            if response.status != 200:
                text = payload.decode("utf-8", "replace")
                self.record.append("response", text, "CHATTERBOX", "LUNA")
                raise RuntimeError(text)
            return payload
