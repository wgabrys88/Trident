from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from config import GGML_VULKAN_ENV, RESIDENT_SERVERS, RUNTIMES, TTS_FIELDS, ggml_vulkan_environment
from log import note

if os.name == "nt":
    import msvcrt
else:
    import fcntl


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
        if os.name == "nt":
            if path.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_state(name: str) -> dict:
    path = _state_path(name)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _write_state(name: str, state: dict) -> None:
    path = _state_path(name)
    partial = path.with_suffix(".json.part")
    partial.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(partial, path)


def _file_manifest(path: Path) -> dict:
    resolved = path.resolve()
    st = resolved.stat()
    return {"path": str(resolved), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _manifest(files: dict[str, Path], extra: dict) -> dict:
    return {"files": {key: _file_manifest(path) for key, path in sorted(files.items())}, "extra": extra}


def _identity(files: dict[str, Path], extra: dict) -> str:
    digest = hashlib.sha256()
    for key, path in sorted(files.items()):
        resolved = path.resolve()
        header = json.dumps([key, str(resolved)], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        digest.update(len(header).to_bytes(8, "little"))
        digest.update(header)
        with resolved.open("rb") as source:
            for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
                digest.update(block)
    payload = json.dumps(extra, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest.update(len(payload).to_bytes(8, "little"))
    digest.update(payload)
    return digest.hexdigest()


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


def _spawn_detached(command: list[str], cwd: Path, env: dict[str, str] | None) -> subprocess.Popen:
    kwargs = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    return subprocess.Popen(
        command, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT, close_fds=True, **kwargs,
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
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
    else:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            note(f"component=resident event=already_stopped name={name} pid={pid}")


def _terminate(name: str) -> None:
    state = _read_state(name)
    pid = int(state.get("pid") or 0)
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
                raise RuntimeError(
                    f"{name} resident port {cfg['port']} is in use but no owned PID is recorded; "
                    "stop that process before installation"
                )
            return
        _terminate(name)
        _wait_port_closed(name)


def _ensure(
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
) -> str:
    with _resident_lock(name):
        cfg = RESIDENT_SERVERS[name]
        files = {"server": server, "model": model, **(identity_files or {})}
        manifest = _manifest(files, identity_extra)
        state = _read_state(name)
        ident = state.get("identity") if state.get("manifest") == manifest else None
        if not ident:
            ident = _identity(files, identity_extra)
        if ready_probe():
            if state.get("identity") == ident:
                if state.get("manifest") != manifest:
                    state["manifest"] = manifest
                    _write_state(name, state)
                return str(cfg["url"])
            if int(state.get("pid") or 0) > 0:
                note(f"component=resident event=restart name={name} reason=identity_changed")
                _terminate(name)
                _wait_port_closed(name)
            else:
                raise RuntimeError(f"{name} resident port {cfg['port']} is already in use by an unowned process")
        state_path = _state_path(name)
        state_path.unlink(missing_ok=True)
        policy = "vulkan_f16=disabled" if env and env.get("GGML_VK_DISABLE_F16") == "1" else "vulkan_f16=default"
        note(f"component=resident event=start name={name} policy={policy}")
        process = _spawn_detached(command, server.parent, env)
        pid = int(process.pid)
        state_value = {"identity": ident, "manifest": manifest, "pid": pid, **(state_extra or {})}
        _write_state(name, state_value)
        ready = False
        try:
            _wait_ready(name, process, ready_probe, float(cfg["startup_timeout_s"]))
            ready = True
        finally:
            if not ready:
                note(f"component=resident event=failed name={name}")
                _kill_pid(name, pid)
                state_path.unlink(missing_ok=True)
                _wait_port_closed(name)
        note(f"component=resident event=ready name={name} pid={pid}")
        return str(cfg["url"])


def ensure_parakeet(server: Path, model: Path) -> str:
    cfg = RESIDENT_SERVERS["parakeet"]
    host, port = str(cfg["host"]), int(cfg["port"])
    command = [str(server), "--model", str(model), "--port", str(port)]
    return _ensure(
        "parakeet", server, model, command,
        {"argv": command[1:], "vulkan_env": GGML_VULKAN_ENV},
        lambda: _port_open(host, port), env=ggml_vulkan_environment(),
    )


def ensure_gemma(server: Path, model: Path, runtime: dict) -> str:
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
    return _ensure(
        "gemma", server, model, command,
        {"argv": command[1:], "vulkan_env": GGML_VULKAN_ENV},
        lambda: _http_status(probe_url, timeout=1.0) == 200,
        env=ggml_vulkan_environment(),
    )


def ensure_chatterbox(server: Path, t3_model: Path, codec_model: Path, reference: Path, family: dict, language: str) -> str:
    cfg = RESIDENT_SERVERS["chatterbox"]
    host, port = str(cfg["host"]), int(cfg["port"])
    command = [
        str(server), "--family", family["name"],
        "--model", str(t3_model), "--s3gen-gguf", str(codec_model),
        "--reference", str(reference), "--language", language, "--port", str(port),
    ]
    for name, section, key, *_ in TTS_FIELDS:
        command += ["--" + name.replace("_", "-"), str(family[section][key])]
    command += ["--fastconv", "1" if family["TTS_RUNTIME"]["fastconv"] else "0"]
    return _ensure(
        "chatterbox", server, t3_model, command, {"argv": command[1:]},
        lambda: _port_open(host, port), identity_files={"codec": codec_model, "reference": reference},
        state_extra={"family": family["name"], "language": language, "reference": str(reference.resolve())},
    )


def stop_all() -> None:
    for name in ("parakeet", "gemma", "chatterbox"):
        stop_owned(name)


def status() -> list[dict]:
    rows = []
    for name in ("parakeet", "gemma", "chatterbox"):
        cfg = RESIDENT_SERVERS[name]
        state = _read_state(name)
        rows.append({
            "name": name, "ready": _port_open(str(cfg["host"]), int(cfg["port"])),
            "pid": state.get("pid"), "url": cfg["url"],
            "family": state.get("family"), "language": state.get("language"), "reference": state.get("reference"),
        })
    return rows
