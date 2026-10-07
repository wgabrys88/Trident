import asyncio
import base64
import hashlib
import json
import mmap
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
import win32api
import win32con
import win32job

from log import publish
from store import CONFIG, ROOT, cancel, command, encode, save

IMAGE_CAP = 5
IMAGE_BYTES = 15 * 1024 * 1024
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


def attached(folder):
    folder = Path(folder)
    if not folder.is_dir():
        return [], []
    paths = sorted((path for path in folder.glob("*.png") if path.is_file()),
                   key=lambda path: path.stat().st_mtime, reverse=True)[:IMAGE_CAP]
    images, pairs = [], []
    for path in paths:
        data = path.read_bytes()
        if len(data) > IMAGE_BYTES:
            raise RuntimeError(f"Model image exceeds the 15 MB Cloud Agents API limit: {path.name}")
        images.append({"mimeType": "image/png", "data": base64.b64encode(data).decode("ascii")})
        pairs.append((path, hashlib.sha256(data).hexdigest()))
    return images, pairs


def visit_name(state, repair):
    state["agent_seq"] = int(state.get("agent_seq", 0)) + 1
    kind = "repair" if repair else "decision"
    return f"Trident {kind} {state['life'][:4]} #{state['agent_seq']}"


async def cursor_json(session, key, method, path, payload=None):
    options = {"auth": aiohttp.BasicAuth(key, ""), "headers": {"Accept": "application/json"}}
    if payload is not None:
        options["json"] = payload
    async with session.request(method, "https://api.cursor.com" + path, **options) as response:
        raw = await response.text()
        if response.status >= 400:
            raise RuntimeError(f"Cursor Cloud Agents API {method} {path} failed ({response.status}): {raw[:1500]}")
        return json.loads(raw) if raw else {}


async def watch_run(session, key, agent_id, run_id):
    terminal = {"FINISHED", "ERROR", "CANCELLED", "EXPIRED"}
    while True:
        body = await cursor_json(session, key, "GET", f"/v1/agents/{agent_id}/runs/{run_id}")
        status = body.get("status")
        if status in terminal:
            return status, body.get("result")
        if status not in {"CREATING", "RUNNING"}:
            raise RuntimeError(f"Luna run status is {status}")
        await asyncio.sleep(1)


async def stop_run(key, agent_id, run_id):
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            await cursor_json(session, key, "POST", f"/v1/agents/{agent_id}/runs/{run_id}/cancel")
    except RuntimeError as error:
        if "(409)" not in str(error):
            raise


async def decision(instruction, context, workspace, life, state, send):
    charge()
    key = cursor_key()
    machine, repo, branch = cursor_place()
    repair = Path(workspace).resolve() == ROOT
    prompt = {"text": instruction + "\n\nTrident context:\n" + encode(context)}
    images, pairs = attached(Path(life) / "images")
    if images:
        prompt["images"] = images
        await publish(pairs, state.setdefault("sent_images", []), send, lambda: save(life, state))
    name = visit_name(state, repair)
    save(life, state)
    payload = {
        "prompt": prompt,
        "model": {"id": CONFIG["luna"]["model"], "params": [{"id": "reasoning", "value": CONFIG["luna"]["reasoning"]}]},
        "name": name,
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
            status, reply = await watch_run(session, key, agent_id, run_id)
            finished = True
    except asyncio.CancelledError:
        if agent_id and not finished:
            await asyncio.shield(stop_run(key, agent_id, run_id))
        raise
    except Exception as error:
        if agent_id and not finished:
            try:
                await stop_run(key, agent_id, run_id)
            except Exception as cancel_error:
                raise RuntimeError(f"{error}; cancel failed: {cancel_error}") from None
        raise
    if status != "FINISHED" or not isinstance(reply, str) or not reply.strip():
        raise RuntimeError(f"Luna returned no successful response ({status}): {str(reply)[:500]}")
    return reply


class Models:
    def __init__(self, folder, emit):
        self.folder = Path(folder)
        self.emit = emit
        self.voice = None
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
        self.voice = await asyncio.create_subprocess_exec(
            *command(parts), cwd=ROOT, env=env,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        async with asyncio.timeout(300):
            while True:
                if self.voice.returncode is not None:
                    raise RuntimeError(f"voice worker exited {self.voice.returncode}")
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
        code = await self.voice.wait()
        self.emit("error", RuntimeError(f"voice worker exited {code}"))

    async def close(self):
        if self.watcher is not None:
            await cancel(self.watcher)
        failures = []
        if self.voice is not None and self.voice.returncode is None:
            try:
                self.voice.kill()
                await self.voice.wait()
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
