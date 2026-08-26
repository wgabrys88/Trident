from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from config import GGML_VULKAN_ENV, RESIDENT_SERVERS, RUNTIMES, TTS_FIELDS, ggml_vulkan_environment
from log import note


def _state_dir() -> Path:
    path = RUNTIMES / ".resident"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(name: str) -> Path:
    return _state_dir() / f"{name}.json"


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


def _file_signature(path: Path) -> dict:
    resolved = path.resolve()
    st = resolved.stat()
    return {"path": str(resolved), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _identity(name: str, server: Path, model: Path, extra: dict) -> str:
    payload = {
        "name": name,
        "server": _file_signature(server),
        "model": _file_signature(model),
        **extra,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


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
    return subprocess.Popen(
        command, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT, close_fds=True,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )


def _wait_ready(
    name: str, process: subprocess.Popen, probe: Callable[[], bool], timeout_s: float
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if probe():
            return
        returncode = process.poll()
        if returncode is not None:
            raise RuntimeError(f"{name} resident exited before ready: pid={process.pid} exit={returncode}")
        time.sleep(0.25)
    raise RuntimeError(f"{name} resident server did not become ready within {timeout_s:g}s (pid {process.pid})")


def _terminate(name: str) -> None:
    state = _read_state(name)
    pid = int(state.get("pid") or 0)
    if pid > 0:
        note(f"component=resident event=stop name={name} pid={pid}")
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
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
    env: dict[str, str] | None = None,
    state_extra: dict | None = None,
) -> str:
    cfg = RESIDENT_SERVERS[name]
    ident = _identity(name, server, model, identity_extra)
    state = _read_state(name)
    if ready_probe():
        if state.get("identity") == ident:
            return str(cfg["url"])
        if int(state.get("pid") or 0) > 0:
            note(f"component=resident event=restart name={name} reason=identity_changed")
            _terminate(name)
            _wait_port_closed(name)
        else:
            raise RuntimeError(
                f"{name} resident port {cfg['port']} is already in use by a different configuration; "
                "run `python main.py resident stop` once and retry"
            )

    state_path = _state_path(name)
    if state_path.exists():
        state_path.unlink(missing_ok=True)

    policy = "vulkan_f16=disabled" if env and env.get("GGML_VK_DISABLE_F16") == "1" else "vulkan_f16=default"
    note(f"component=resident event=start name={name} policy={policy}")
    process = _spawn_detached(command, server.parent, env)
    pid = int(process.pid)
    state_value = {
        "identity": ident,
        "pid": pid,
        "port": int(cfg["port"]),
        "url": str(cfg["url"]),
        "server": str(server),
        "model": str(model),
        "identity_inputs": identity_extra,
        "started_unix": time.time(),
    }
    if state_extra:
        state_value.update(state_extra)
    _write_state(name, state_value)
    try:
        _wait_ready(name, process, ready_probe, float(cfg["startup_timeout_s"]))
    except Exception as exc:
        message = " ".join(str(exc).split())
        note(f"component=resident event=failed name={name} message={message}")
        _state_path(name).unlink(missing_ok=True)
        raise
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
    for _, section, key, _, flag, *_ in TTS_FIELDS:
        command += [flag, str(family[section][key])]
    command += ["--fastconv", "1" if family["TTS_RUNTIME"]["fastconv"] else "0"]
    return _ensure(
        "chatterbox", server, t3_model, command,
        {"argv": command[1:], "codec": _file_signature(codec_model), "reference": _file_signature(reference)},
        lambda: _port_open(host, port),
        state_extra={
            "family": family["name"], "language": language,
            "reference": str(reference.resolve()), "codec": str(codec_model.resolve()),
        },
    )


def stop_all() -> None:
    for name in ("parakeet", "gemma", "chatterbox"):
        _terminate(name)


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
