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
from pathlib import Path
from typing import Callable

from config import RESIDENT_SERVERS, ROOT, RUNTIMES
from log import note, open_sink

LOG_HINT = str(ROOT / "trident*.log")


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


def _spawn_detached(command: list[str], cwd: Path) -> tuple[subprocess.Popen, str, int]:
    chunk_name, offset, log = open_sink()
    log.write((f"trident ts_unix_ns={time.time_ns()} event=spawn command=" + json.dumps(command, separators=(",", ":")) + "\n").encode())
    kwargs = {"cwd": str(cwd), "stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT, "close_fds": True}
    if os.name == "nt": kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else: kwargs["start_new_session"] = True
    try:
        return subprocess.Popen(command, **kwargs), chunk_name, offset
    finally:
        log.close()


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
    raise RuntimeError(
        f"{name} resident server did not become ready within {timeout_s:g}s; "
        f"inspect {LOG_HINT} (pid {process.pid})"
    )


def _terminate(name: str) -> None:
    state = _read_state(name)
    pid = int(state.get("pid") or 0)
    if pid > 0:
        note(f"{name} resident: stopping pid={pid}")
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                )
            else:
                os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
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
    state_extra: dict | None = None,
) -> str:
    cfg = RESIDENT_SERVERS[name]
    ident = _identity(name, server, model, identity_extra)
    state = _read_state(name)
    if ready_probe():
        if state.get("identity") == ident:
            note(f"{name} resident: reuse pid={state.get('pid', '?')} url={cfg['url']}")
            return str(cfg["url"])
        if int(state.get("pid") or 0) > 0:
            note(f"{name} resident: configuration changed; restarting")
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

    note(f"{name} resident: starting persistent server")
    note(f"{name} resident command: " + " ".join(command))
    process, log_chunk, log_offset = _spawn_detached(command, server.parent)
    pid = int(process.pid)
    state_value = {
        "identity": ident,
        "pid": pid,
        "port": int(cfg["port"]),
        "url": str(cfg["url"]),
        "server": str(server),
        "model": str(model),
        "command": command,
        "identity_inputs": identity_extra,
        "log": str(LOG_HINT),
        "log_chunk": log_chunk,
        "log_offset": log_offset,
        "started_unix": time.time(),
    }
    if state_extra:
        state_value.update(state_extra)
    _write_state(name, state_value)
    try:
        _wait_ready(name, process, ready_probe, float(cfg["startup_timeout_s"]))
    except Exception:
        _state_path(name).unlink(missing_ok=True)
        raise
    note(f"{name} resident: ready pid={pid} url={cfg['url']}")
    return str(cfg["url"])


def ensure_parakeet(server: Path, model: Path) -> str:
    cfg = RESIDENT_SERVERS["parakeet"]
    host, port = str(cfg["host"]), int(cfg["port"])
    command = [str(server), "--model", str(model), "--port", str(port)]
    url = _ensure("parakeet", server, model, command, {"model_mode": "nemotron-streaming", "device": "auto", "port": port}, lambda: _port_open(host, port))
    note("parakeet resident: device=auto model-resident=1")
    return url


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
    probe = lambda: _http_status(probe_url, timeout=1.0) == 200
    identity = {
        "gpu_layers": str(runtime["gpu_layers"]), "context": int(runtime["context"]),
        "load_mode": str(runtime["load_mode"]), "flash_attn": str(runtime["flash_attn"]),
        "fit": str(runtime["fit"]), "cache_type_k": str(runtime["cache_type_k"]),
        "cache_type_v": str(runtime["cache_type_v"]), "parallel": int(runtime["parallel"]),
        "threads": int(runtime["threads"]), "threads_batch": int(runtime["threads_batch"]),
        "poll": int(runtime["poll"]), "poll_batch": int(runtime["poll_batch"]),
        "log_verbosity": 4, "log_prefix": True, "log_timestamps": True,
        "cors_origins": "localhost", "cache_prompt": True, "ui": False,
        "alias": "gemma", "port": port,
    }
    url = _ensure("gemma", server, model, command, identity, probe)
    note(f"gemma resident: device=auto kv={runtime['cache_type_k']}/{runtime['cache_type_v']} flash_attn={runtime['flash_attn']}")
    return url


def ensure_chatterbox(
    server: Path,
    t3_model: Path,
    codec_model: Path,
    reference: Path,
    family_name: str,
    language: str,
    runtime: dict,
    sample: dict,
    voice: dict,
    chunk: dict,
    stream: dict,
) -> str:
    cfg = RESIDENT_SERVERS["chatterbox"]
    host, port = str(cfg["host"]), int(cfg["port"])
    command = [
        str(server), "--family", family_name,
        "--model", str(t3_model), "--s3gen-gguf", str(codec_model),
        "--reference", str(reference), "--language", language,
        "--port", str(port),
        "--n-gpu-layers", str(runtime["gpu_layers"]),
        "--context", str(runtime["context"]), "--threads", str(runtime["threads"]),
        "--seed", str(sample["seed"]), "--max-tokens", str(sample["max_tokens"]),
        "--top-k", str(sample["top_k"]), "--top-p", str(sample["top_p"]),
        "--min-p", str(sample["min_p"]), "--temperature", str(sample["temperature"]),
        "--repeat-penalty", str(sample["repeat_penalty"]),
        "--cfg-weight", str(voice["cfg_weight"]), "--exaggeration", str(voice["exaggeration"]),
        "--cfm-steps", str(sample["cfm_steps"]),
        "--first-chunk-chars", str(chunk.get("first_chars", chunk["chars"])),
        "--chunk-chars", str(chunk["chars"]),
        "--stream-first-chunk-tokens", str(stream["first_tokens"]),
        "--stream-chunk-tokens", str(stream["tokens"]),
        "--fastconv", "1" if runtime.get("fastconv") else "0",
    ]
    probe = lambda: _port_open(host, port)
    identity_extra = {
        "codec": _file_signature(codec_model),
        "reference": _file_signature(reference),
        "family": family_name,
        "language": language,
        "runtime": runtime,
        "sample": sample,
        "voice": voice,
        "chunk": chunk,
        "stream_tokens": {"first": stream["first_tokens"], "next": stream["tokens"]},
        "port": port,
    }
    url = _ensure(
        "chatterbox", server, t3_model, command, identity_extra, probe,
        state_extra={
            "family": family_name,
            "language": language,
            "reference": str(reference.resolve()),
            "codec": str(codec_model.resolve()),
        },
    )
    note(f"chatterbox resident: gpu_layers={runtime['gpu_layers']} family={family_name} language={language} model-resident=1 voice-resident=1")
    return url


def stop_all() -> None:
    for name in ("parakeet", "gemma", "chatterbox"):
        _terminate(name)


def status() -> list[dict]:
    rows = []
    for name in ("parakeet", "gemma", "chatterbox"):
        cfg = RESIDENT_SERVERS[name]
        state = _read_state(name)
        ready = _port_open(str(cfg["host"]), int(cfg["port"]))
        identity = state.get("identity_inputs") or {}
        runtime = identity.get("runtime") or {}
        rows.append({
            "name": name, "ready": ready, "pid": state.get("pid"), "url": cfg["url"], "log": LOG_HINT,
            "family": state.get("family"), "language": state.get("language"), "reference": state.get("reference"),
            "device": "auto", "gpu_layers": identity.get("gpu_layers", runtime.get("gpu_layers")),
        })
    return rows


def stop(name: str) -> None:
    if name not in RESIDENT_SERVERS:
        raise RuntimeError(f"unknown resident: {name}")
    _terminate(name)
