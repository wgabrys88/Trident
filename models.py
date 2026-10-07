import asyncio
import base64
import json
import os
import socket
import subprocess
import urllib.error
import urllib.request
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


def cursor_key():
    key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "CURSOR_API_KEY is not set. Create a user API key at https://cursor.com/dashboard "
            "(Dashboard → API Keys) and set CURSOR_API_KEY in the same PowerShell session before "
            "starting Trident. The saved agent login on this PC is not that key, and a team Admin "
            "API key cannot place a visit on My Machines."
        )
    return key


def cursor_place():
    machine = str(CONFIG["luna"].get("machine", "")).strip()
    if not machine:
        raise RuntimeError("config.toml [luna] machine is empty. Set it to the My Machines worker name, trident-nvidia.")
    flags = subprocess.CREATE_NO_WINDOW
    repo = subprocess.check_output(["git", "remote", "get-url", "origin"], cwd=ROOT, text=True, creationflags=flags).strip()
    branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT, text=True, creationflags=flags).strip()
    if not repo or branch == "HEAD":
        raise RuntimeError("A visit on this machine needs origin and a named branch")
    return machine, repo, branch


def require_cursor():
    key = cursor_key()
    cursor_place()
    token = base64.b64encode(f"{key}:".encode()).decode()
    request = urllib.request.Request(
        "https://api.cursor.com/v1/agents?limit=1",
        headers={"Authorization": f"Basic {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read(64)
    except urllib.error.HTTPError as error:
        detail = error.read(500).decode("utf-8", "replace")
        raise RuntimeError(
            f"CURSOR_API_KEY was rejected by https://api.cursor.com ({error.code}): {detail}. "
            "Create a user API key at https://cursor.com/dashboard (Dashboard → API Keys) and set "
            "CURSOR_API_KEY in this PowerShell session."
        ) from None


def png_inputs(folder):
    shots = []
    paths = [path for path in Path(folder).glob("*.png") if path.is_file()]
    paths.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for path in paths:
        if path.stat().st_size > 15 * 1024 * 1024:
            continue
        shots.append({"mimeType": "image/png", "data": base64.b64encode(path.read_bytes()).decode("ascii")})
        if len(shots) == 5:
            break
    return shots


async def cursor_json(session, key, method, path, payload=None):
    options = {"auth": aiohttp.BasicAuth(key, ""), "headers": {"Accept": "application/json"}}
    if payload is not None:
        options["json"] = payload
    async with session.request(method, "https://api.cursor.com" + path, **options) as response:
        raw = await response.text()
        if response.status >= 400:
            raise RuntimeError(f"Cursor Cloud Agents API {method} {path} failed ({response.status}): {raw[:1500]}")
        return json.loads(raw) if raw else {}


async def watch_run(session, key, agent_id, run_id, record):
    terminal = {"FINISHED", "ERROR", "CANCELLED", "EXPIRED"}
    last = None
    while True:
        try:
            status, reply, last, done = await read_stream(session, key, agent_id, run_id, record, last)
        except aiohttp.ClientError:
            done = False
        if done:
            return status, reply
        try:
            body = await cursor_json(session, key, "GET", f"/v1/agents/{agent_id}/runs/{run_id}")
        except aiohttp.ClientError:
            await asyncio.sleep(1)
            continue
        if body.get("status") in terminal:
            return body.get("status"), body.get("result")
        await asyncio.sleep(1)


async def read_stream(session, key, agent_id, run_id, record, last):
    headers = {"Accept": "text/event-stream"}
    if last:
        headers["Last-Event-ID"] = last
    url = f"https://api.cursor.com/v1/agents/{agent_id}/runs/{run_id}/stream"
    async with session.get(url, headers=headers, auth=aiohttp.BasicAuth(key, "")) as response:
        if response.status == 410:
            return None, None, last, False
        if response.status >= 400:
            detail = await response.text()
            raise RuntimeError(f"Cursor Cloud Agents API stream failed ({response.status}): {detail[:1500]}")
        event, data, current = "message", [], last
        while True:
            raw = await response.content.readline()
            if not raw:
                return None, None, current, False
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("id:"):
                current = line[3:].strip()
            elif line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
            elif line == "" and data:
                payload = json.loads("\n".join(data))
                kind, event, data = event, "message", []
                if kind == "heartbeat":
                    continue
                record.append("model_event", {"channel": kind, "data": payload})
                if kind == "result":
                    return payload.get("status"), payload.get("text"), current, True
                if kind == "error":
                    return None, None, current, False


async def stop_run(key, agent_id, run_id, record):
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            await cursor_json(session, key, "POST", f"/v1/agents/{agent_id}/runs/{run_id}/cancel")
    except Exception as error:
        record.append("model_event", {"channel": "agent", "data": {
            "id": agent_id, "run": run_id, "cancel_error": str(error),
        }})


async def decision(record, instruction, context, workspace):
    key = cursor_key()
    machine, repo, branch = cursor_place()
    repair = Path(workspace).resolve() == ROOT
    record.append("request", {"system": instruction, "context": context, "machine": machine}, "TRIDENT", "LUNA")
    prompt = {"text": instruction + "\n\nTrident context:\n" + encode(context)}
    if not repair:
        shots = png_inputs(workspace)
        if shots:
            prompt["images"] = shots
    payload = {
        "prompt": prompt, "model": {"id": CONFIG["luna"]["model"]},
        "name": "Trident repair" if repair else "Trident decision",
        "env": {"type": "machine", "name": machine},
        "repos": [{"url": repo, "startingRef": branch}],
        "autoCreatePR": False, "workOnCurrentBranch": False, "mode": "agent",
    }
    agent_id = run_id = None
    finished = False
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=30)) as session:
            created = await cursor_json(session, key, "POST", "/v1/agents", payload)
            agent, run = created["agent"], created["run"]
            agent_id, run_id = agent["id"], run["id"]
            url = agent.get("url") or f"https://cursor.com/agents/{agent_id}"
            record.append("model_event", {"channel": "agent", "data": {
                "id": agent_id, "run": run_id, "url": url, "machine": machine,
            }})
            status, reply = await watch_run(session, key, agent_id, run_id, record)
            finished = True
    except asyncio.CancelledError:
        if agent_id and not finished:
            await asyncio.shield(stop_run(key, agent_id, run_id, record))
        raise
    except Exception:
        if agent_id and not finished:
            await stop_run(key, agent_id, run_id, record)
        raise
    if status != "FINISHED" or not isinstance(reply, str) or not reply.strip():
        raise RuntimeError(f"Luna returned no successful executable response ({status}): {str(reply)[:1500]}")
    record.append("response", {"result": reply, "agent": agent_id, "url": url, "run": run_id}, "LUNA", "TRIDENT")
    return reply


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
        return await decision(self.record, instruction, context, self.record.folder)

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
