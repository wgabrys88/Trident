from __future__ import annotations

import ctypes
import hashlib
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from config import FAMILIES, GGML_VULKAN_ENV, RESIDENT_SERVERS, RUNTIMES, TTS_FIELDS, ggml_vulkan_environment
from log import note

import msvcrt

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_booted = False


def _state_dir() -> Path:
    path = RUNTIMES / ".resident"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(name: str) -> Path:
    return _state_dir() / f"{name}.json"


@contextmanager
def _resident_lock(name: str):
    path = _state_dir() / f"{name}.lock"
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _read_state(name: str) -> dict:
    path = _state_path(name)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_state(name: str, state: dict) -> None:
    path = _state_path(name)
    partial = path.with_suffix(".json.part")
    partial.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(partial, path)


def _file_manifest(path: Path) -> dict:
    resolved = path.resolve()
    st = resolved.stat()
    return {"path": str(resolved), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _identity(files: dict[str, Path], extra: dict) -> str:
    payload = {
        "files": {key: _file_manifest(path) for key, path in sorted(files.items())},
        "extra": extra,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_status(url: str, timeout: float = 1.0) -> int | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "trident/1"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    code = wintypes.DWORD()
    ok = _kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    _kernel32.CloseHandle(handle)
    if not ok:
        raise OSError(f"GetExitCodeProcess failed for pid {pid}")
    return int(code.value) == _STILL_ACTIVE


def _probe(name: str) -> bool:
    cfg = RESIDENT_SERVERS[name]
    host, port = str(cfg["host"]), int(cfg["port"])
    if name == "gemma":
        return _http_status(f"http://{host}:{port}/health", timeout=1.0) == 200
    return _port_open(host, port)


def _spawn_detached(command: list[str], cwd: Path, env: dict[str, str] | None) -> subprocess.Popen:
    return subprocess.Popen(
        command, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT, close_fds=True,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )


def _wait_ready(name: str, process: subprocess.Popen, probe: Callable[[], bool], timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if probe():
            return
        returncode = process.poll()
        if returncode is not None:
            raise RuntimeError(f"{name} resident exited before ready: pid={process.pid} exit={returncode}")
        time.sleep(0.25)
    raise RuntimeError(f"{name} resident server did not become ready within {timeout_s:g}s (pid {process.pid})")


def _kill_pid(name: str, pid: int) -> None:
    note(f"component=resident event=stop name={name} pid={pid}")
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )


def _terminate(name: str) -> None:
    pid = int(_read_state(name).get("pid") or 0)
    if pid > 0:
        _kill_pid(name, pid)
    _state_path(name).unlink(missing_ok=True)


def _wait_port_closed(name: str, timeout_s: float = 10.0) -> None:
    cfg = RESIDENT_SERVERS[name]
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _port_open(str(cfg["host"]), int(cfg["port"])):
            return
        time.sleep(0.1)
    raise RuntimeError(f"{name} resident port {cfg['port']} did not close after restart request")


def stop_owned(name: str) -> None:
    if name not in RESIDENT_SERVERS:
        raise ValueError(f"unknown resident component: {name}")
    with _resident_lock(name):
        state = _read_state(name)
        if int(state.get("pid") or 0) <= 0:
            cfg = RESIDENT_SERVERS[name]
            if _port_open(str(cfg["host"]), int(cfg["port"])):
                raise RuntimeError(f"{name} resident port {cfg['port']} is in use by an unowned process")
            return
        _terminate(name)
        _wait_port_closed(name)


def _launch(
    name: str,
    server: Path,
    model: Path,
    command: list[str],
    identity_extra: dict,
    ready_probe: Callable[[], bool],
    *,
    identity_files: dict[str, Path] | None = None,
    env: dict[str, str] | None = None,
    state_extra: dict | None = None,
    allow_replace: bool = False,
) -> str:
    with _resident_lock(name):
        cfg = RESIDENT_SERVERS[name]
        files = {"server": server, "model": model, **(identity_files or {})}
        ident = _identity(files, identity_extra)
        state = _read_state(name)
        pid = int(state.get("pid") or 0)
        alive = _pid_alive(pid) and ready_probe()
        if alive and state.get("identity") == ident:
            note(f"component=resident event=reuse name={name} pid={pid}")
            return str(cfg["url"])
        if alive:
            if not allow_replace:
                raise RuntimeError(f"{name} resident pid {pid} is running with a different identity")
            note(f"component=resident event=replace name={name} reason=identity")
            _terminate(name)
            _wait_port_closed(name)
        else:
            if _pid_alive(pid):
                raise RuntimeError(f"{name} resident pid {pid} is not answering on port {cfg['port']}")
            if _booted and state.get("identity") == ident:
                raise RuntimeError(f"{name} resident pid {pid or '-'} is not alive")
            if _booted and not allow_replace:
                raise RuntimeError(f"{name} resident is not alive")
            if _port_open(str(cfg["host"]), int(cfg["port"])):
                raise RuntimeError(f"{name} resident port {cfg['port']} is already in use by an unowned process")
            _state_path(name).unlink(missing_ok=True)

        policy = "vulkan_f16=disabled" if env and env.get("GGML_VK_DISABLE_F16") == "1" else "vulkan_f16=default"
        note(f"component=resident event=start name={name} policy={policy}")
        process = _spawn_detached(command, server.parent, env)
        spawned = int(process.pid)
        state_value = {
            "identity": ident,
            "pid": spawned,
            "port": int(cfg["port"]),
            "url": str(cfg["url"]),
            "server": str(server),
            "model": str(model),
            "identity_inputs": identity_extra,
            "started_unix": time.time(),
            **(state_extra or {}),
        }
        _write_state(name, state_value)
        try:
            _wait_ready(name, process, ready_probe, float(cfg["startup_timeout_s"]))
        except Exception as exc:
            message = " ".join(str(exc).split())
            note(f"component=resident event=failed name={name} message={message}")
            _kill_pid(name, spawned)
            _state_path(name).unlink(missing_ok=True)
            _wait_port_closed(name)
            raise
        if not _pid_alive(spawned):
            raise RuntimeError(f"{name} resident pid {spawned} died after ready probe")
        note(f"component=resident event=ready name={name} pid={spawned}")
        return str(cfg["url"])


def start_parakeet(server: Path, model: Path) -> str:
    cfg = RESIDENT_SERVERS["parakeet"]
    host, port = str(cfg["host"]), int(cfg["port"])
    command = [str(server), "--model", str(model), "--port", str(port)]
    return _launch(
        "parakeet", server, model, command,
        {"argv": command[1:], "vulkan_env": GGML_VULKAN_ENV},
        lambda: _port_open(host, port), env=ggml_vulkan_environment(),
        allow_replace=not _booted,
    )


def start_gemma(server: Path, model: Path, runtime: dict) -> str:
    cfg = RESIDENT_SERVERS["gemma"]
    host, port = str(cfg["host"]), int(cfg["port"])
    command = [
        str(server), "-m", str(model), "--alias", "gemma",
        "--host", host, "--port", str(port), "--offline",
        "--n-gpu-layers", str(runtime["gpu_layers"]),
        "--ctx-size", str(runtime["context"]),
        "--no-mmproj", "--load-mode", str(runtime["load_mode"]),
        "--flash-attn", str(runtime["flash_attn"]), "--repack",
        "--fit", str(runtime["fit"]), "--kv-offload", "--op-offload",
        "--cache-type-k", str(runtime["cache_type_k"]),
        "--cache-type-v", str(runtime["cache_type_v"]),
        "--parallel", str(runtime["parallel"]),
        "--threads", str(runtime["threads"]), "--threads-batch", str(runtime["threads_batch"]),
        "--poll", str(runtime["poll"]), "--poll-batch", str(runtime["poll_batch"]),
        "--threads-http", str(runtime["threads_http"]),
        "--cors-origins", "localhost",
        "--log-verbosity", "4", "--log-prefix", "--log-timestamps",
        "--cache-prompt", "--no-ui", "--reasoning", "off",
    ]
    probe_url = f"http://{host}:{port}/health"
    return _launch(
        "gemma", server, model, command,
        {"argv": command[1:], "vulkan_env": GGML_VULKAN_ENV},
        lambda: _http_status(probe_url, timeout=1.0) == 200,
        env=ggml_vulkan_environment(),
        allow_replace=not _booted,
    )


def use_chatterbox(server: Path, t3_model: Path, codec_model: Path, reference: Path, family: dict, language: str) -> str:
    if family["name"] not in FAMILIES:
        raise RuntimeError(f"unknown Chatterbox family {family['name']!r}")
    if language not in family["TTS_LANGUAGES"]:
        raise RuntimeError(f"language {language!r} is not wired in {family['name']}")
    cfg = RESIDENT_SERVERS["chatterbox"]
    host, port = str(cfg["host"]), int(cfg["port"])
    command = [
        str(server), "--family", family["name"],
        "--model", str(t3_model), "--s3gen-gguf", str(codec_model),
        "--reference", str(reference), "--language", language, "--port", str(port),
    ]
    for _, section, key, _, flag, *_ in TTS_FIELDS:
        command += [flag, str(family[section][key])]
    command += ["--fastconv", "1" if family["TTS_RUNTIME"]["fastconv"] else "0"]
    return _launch(
        "chatterbox", server, t3_model, command,
        {"argv": command[1:]}, lambda: _port_open(host, port),
        identity_files={"codec": codec_model, "reference": reference},
        state_extra={
            "family": family["name"], "language": language,
            "reference": str(reference.resolve()), "codec": str(codec_model.resolve()),
        },
        allow_replace=True,
    )


def require_alive(name: str) -> str:
    if name not in RESIDENT_SERVERS:
        raise ValueError(f"unknown resident component: {name}")
    cfg = RESIDENT_SERVERS[name]
    state = _read_state(name)
    pid = int(state.get("pid") or 0)
    if not _pid_alive(pid):
        raise RuntimeError(f"{name} resident pid {pid or '-'} is not alive")
    if not _probe(name):
        raise RuntimeError(f"{name} resident pid {pid} is not answering on port {cfg['port']}")
    return str(cfg["url"])


def mark_booted() -> None:
    global _booted
    _booted = True
    note("component=resident event=boot_complete names=" + ",".join(RESIDENT_SERVERS))


def stop_all() -> None:
    global _booted
    for name in ("parakeet", "gemma", "chatterbox"):
        stop_owned(name)
    _booted = False


def status() -> list[dict]:
    rows = []
    for name in ("parakeet", "gemma", "chatterbox"):
        cfg = RESIDENT_SERVERS[name]
        state = _read_state(name)
        pid = int(state.get("pid") or 0)
        rows.append({
            "name": name, "ready": _pid_alive(pid) and _probe(name),
            "pid": state.get("pid"), "url": cfg["url"],
            "family": state.get("family"), "language": state.get("language"), "reference": state.get("reference"),
        })
    return rows
