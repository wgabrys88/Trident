from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from config import RESIDENT_SERVERS, ROOT, RUNTIMES
from log import note, open_sink, read_from

LOG_HINT = str(ROOT / "trident*.log")


def _vulkan_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GGML_VK_DISABLE_COOPMAT"] = "1"
    env["GGML_VK_DISABLE_COOPMAT2"] = "1"
    return env


def _state_dir() -> Path:
    path = RUNTIMES / ".resident"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(name: str) -> Path:
    return _state_dir() / f"{name}.json"


def _profile_path() -> Path:
    return _state_dir() / "pipeline-profile.json"


def _read_state(name: str) -> dict:
    path = _state_path(name)
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid resident state: {path}")
    return value


def _write_state(name: str, state: dict) -> None:
    path = _state_path(name)
    partial = path.with_suffix(".json.part")
    partial.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(partial, path)


def load_pipeline_profile() -> dict:
    path = _profile_path()
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid resident profile: {path}")
    return value


def save_pipeline_profile(profile: dict) -> None:
    path = _profile_path()
    partial = path.with_suffix(".json.part")
    partial.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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


def _read_log(name: str) -> str:
    state = _read_state(name)
    return read_from(int(state.get("log_offset") or 0), state.get("log_chunk"))


def _gemma_cpu(runtime: dict) -> bool:
    return str(runtime.get("device", "")).lower() in {"cpu", "none"} or str(runtime.get("gpu_layers")) == "0"


def _validate_gemma_residency(runtime: dict, timeout_s: float = 5.0) -> str:
    """Fail closed unless llama.cpp residency matches the explicitly requested CPU/GPU mode."""
    cpu = _gemma_cpu(runtime)
    device = "none" if cpu else str(runtime["device"])
    deadline = time.monotonic() + timeout_s
    text = ""
    matches = []
    kv_ok = False
    while time.monotonic() < deadline:
        text = _read_log("gemma")
        matches = list(re.finditer(r"offloaded\s+(\d+)/(\d+)\s+layers to GPU", text, flags=re.IGNORECASE))
        kv_ok = bool(re.search(
            r"\bCPU(?:_\w+)?\s+KV buffer size\s*=" if cpu else rf"\b{re.escape(device)}\s+KV buffer size\s*=",
            text, flags=re.IGNORECASE,
        ))
        if kv_ok and (cpu or matches):
            break
        time.sleep(0.05)

    if cpu:
        if any(int(m.group(1)) > 0 for m in matches) or re.search(r"\b(?:Vulkan|CUDA|Metal)\d*\s+KV buffer size\s*=", text, flags=re.IGNORECASE):
            raise RuntimeError(f"Gemma strict CPU residency failed: GPU allocation was reported; inspect {LOG_HINT}")
        if not kv_ok:
            raise RuntimeError(f"Gemma strict CPU residency failed: no CPU KV buffer was reported; inspect {LOG_HINT}")
        return "CPU"

    if not matches:
        raise RuntimeError(
            "Gemma server became healthy but its log did not report full layer offload; "
            f"inspect {LOG_HINT}"
        )
    loaded, total = map(int, matches[-1].groups())
    if loaded != total or total <= 0:
        raise RuntimeError(
            f"Gemma strict GPU residency failed: llama.cpp offloaded {loaded}/{total} layers; "
            f"inspect {LOG_HINT}"
        )
    if re.search(r"\bCPU(?:_\w+)?\s+KV buffer size\s*=", text, flags=re.IGNORECASE):
        raise RuntimeError(
            "Gemma strict GPU residency failed: llama.cpp allocated a CPU KV buffer; "
            f"inspect {LOG_HINT}"
        )
    if not kv_ok:
        raise RuntimeError(
            f"Gemma strict GPU residency failed: no {device} KV buffer was reported; "
            f"inspect {LOG_HINT}"
        )
    return device


def _validate_parakeet_backend(runtime: dict, timeout_s: float = 5.0) -> str:
    """Verify the selected primary backend; upstream may still schedule unsupported individual ops on CPU."""
    expected = str(runtime["device"])
    if expected.lower() == "cpu":
        return "cpu"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        text = _read_log("parakeet")
        matches = re.findall(r"pk::Backend using device:\s*([^\r\n]+)", text)
        if matches:
            actual = matches[-1].strip()
            if actual.lower() == expected.lower():
                return actual
            raise RuntimeError(f"Parakeet selected {actual!r}, expected {expected!r}; inspect {LOG_HINT}")
        if "falling back to CPU" in text:
            raise RuntimeError(f"Parakeet could not select {expected!r} and fell back to CPU; inspect {LOG_HINT}")
        time.sleep(0.05)
    raise RuntimeError(f"Parakeet did not report requested primary backend {expected!r}; inspect {LOG_HINT}")


def _validate_chatterbox_backend(runtime: dict, timeout_s: float = 5.0) -> str:
    expected = "Vulkan" if int(runtime["gpu_layers"]) > 0 else "CPU"
    deadline = time.monotonic() + timeout_s
    roles = {}
    while time.monotonic() < deadline:
        text = _read_log("chatterbox")
        roles = dict(re.findall(
            r"tts\b[^\n]*\bevent=backend\s+role=(t3|s3gen)\s+backend=(Vulkan|CPU)\b", text
        ))
        if roles.get("t3") == expected and roles.get("s3gen") == expected:
            return expected
        if roles and any(backend != expected for backend in roles.values()):
            break
        time.sleep(0.05)
    raise RuntimeError(
        f"Chatterbox backend validation failed: gpu_layers={runtime['gpu_layers']} requires "
        f"T3+S3Gen={expected}, reported={roles or 'none'}; inspect {LOG_HINT}"
    )


def _spawn_detached(
    command: list[str], cwd: Path, env: dict[str, str]
) -> tuple[subprocess.Popen, str, int]:
    chunk_name, offset, log = open_sink()
    runtime_env = {
        key: env.get(key, "")
        for key in (
            "GGML_VK_DISABLE_COOPMAT", "GGML_VK_DISABLE_COOPMAT2",
            "GGML_VK_MEMORY_LOGGER", "GGML_VK_PERF_LOGGER", "GGML_VK_SYNC_LOGGER",
            "PARAKEET_DEVICE", "TRIDENT_FASTCONV",
        )
        if key in env
    }
    line = (
        f"trident ts_unix_ns={time.time_ns()} event=spawn command="
        + json.dumps(command, separators=(",", ":"))
        + " env=" + json.dumps(runtime_env, sort_keys=True, separators=(",", ":")) + "\n"
    )
    log.write(line.encode("utf-8"))
    try:
        kwargs: dict = {
            "cwd": str(cwd),
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": log,
            "stderr": subprocess.STDOUT,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **kwargs)
        return process, chunk_name, offset
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
            text = _read_log(name).replace("\r\n", "\n").rstrip()
            tail = "\n".join(text.splitlines()[-20:])
            detail = f"\nLast native output:\n{tail}" if tail else ""
            raise RuntimeError(
                f"{name} resident exited before becoming ready "
                f"(pid {process.pid}, exit code {returncode}){detail}"
            )
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
    """Stop one resident process only when it is owned by this Trident state directory."""
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
    env: dict[str, str],
    identity_extra: dict,
    ready_probe: Callable[[], bool],
    *,
    replace_owned_mismatch: bool = False,
    state_extra: dict | None = None,
) -> str:
    cfg = RESIDENT_SERVERS[name]
    ident = _identity(name, server, model, identity_extra)
    state = _read_state(name)
    if ready_probe():
        if state.get("identity") == ident:
            note(f"{name} resident: reuse pid={state.get('pid', '?')} url={cfg['url']}")
            return str(cfg["url"])
        if replace_owned_mismatch and int(state.get("pid") or 0) > 0:
            note(f"{name} resident: configuration changed; replacing the owned warm process")
            _terminate(name)
            _wait_port_closed(name)
            state = {}
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
    process, log_chunk, log_offset = _spawn_detached(command, server.parent, env)
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


def ensure_parakeet(server: Path, model: Path, runtime: dict) -> str:
    cfg = RESIDENT_SERVERS["parakeet"]
    host, port = str(cfg["host"]), int(cfg["port"])
    env = _vulkan_env()
    env["PARAKEET_DEVICE"] = str(runtime["device"])
    command = [str(server), "--model", str(model), "--port", str(port)]
    probe = lambda: _port_open(host, port)
    url = _ensure(
        "parakeet", server, model, command, env,
        {
            "device": str(runtime["device"]), "decoder": "tdt", "port": port,
            "disable_coopmat": True, "disable_coopmat2": True,
        },
        probe,
    )
    backend = _validate_parakeet_backend(runtime)
    note(
        f"parakeet resident: primary_backend={backend} verified model-resident=1 "
        "language=auto-detect(v3); unsupported individual ops may scheduler-fallback to CPU"
    )
    return url


def ensure_gemma(server: Path, model: Path, runtime: dict) -> str:
    cfg = RESIDENT_SERVERS["gemma"]
    host, port = str(cfg["host"]), int(cfg["port"])
    cpu = _gemma_cpu(runtime)
    device = "none" if cpu else str(runtime["device"])
    command = [
        str(server), "-m", str(model), "--alias", "gemma",
        "--host", host, "--port", str(port), "--offline",
        "--device", device,
        "--n-gpu-layers", "0" if cpu else str(runtime["gpu_layers"]),
        "--split-mode", str(runtime["split_mode"]),
        "--main-gpu", str(runtime["main_gpu"]),
        "--ctx-size", str(runtime["context"]),
        "--no-mmproj", "--load-mode", str(runtime["load_mode"]),
        "--flash-attn", str(runtime["flash_attn"]), "--repack",
        "--fit", str(runtime["fit"]),
        "--no-kv-offload" if cpu else "--kv-offload",
        "--no-op-offload" if cpu else "--op-offload",
        "--cache-type-k", str(runtime["cache_type_k"]),
        "--cache-type-v", str(runtime["cache_type_v"]),
        "--parallel", str(runtime["parallel"]),
        "--threads", str(runtime["threads"]), "--threads-batch", str(runtime["threads_batch"]),
        "--poll", str(runtime["poll"]), "--poll-batch", str(runtime["poll_batch"]),
        "--threads-http", str(runtime["threads_http"]),
        "--cors-origins", "localhost",
        "--log-verbosity", "4", "--log-prefix", "--log-timestamps",
        "--no-cache-prompt", "--no-ui", "--reasoning", "off",
    ]
    env = _vulkan_env()
    probe_url = f"http://{host}:{port}/health"
    probe = lambda: _http_status(probe_url, timeout=1.0) == 200
    url = _ensure(
        "gemma", server, model, command, env,
        {
            "device": device, "gpu_layers": "0" if cpu else str(runtime["gpu_layers"]),
            "split_mode": str(runtime["split_mode"]), "main_gpu": int(runtime["main_gpu"]),
            "context": int(runtime["context"]), "load_mode": str(runtime["load_mode"]),
            "flash_attn": str(runtime["flash_attn"]), "fit": str(runtime["fit"]),
            "cache_type_k": str(runtime["cache_type_k"]), "cache_type_v": str(runtime["cache_type_v"]),
            "parallel": int(runtime["parallel"]), "threads": int(runtime["threads"]),
            "threads_batch": int(runtime["threads_batch"]),
            "poll": int(runtime["poll"]), "poll_batch": int(runtime["poll_batch"]),
            "log_verbosity": 4, "log_prefix": True, "log_timestamps": True, "cors_origins": "localhost",
            "cache_prompt": False, "ui": False, "alias": "gemma", "port": port,
            "disable_coopmat": True, "disable_coopmat2": True,
        },
        probe,
    )
    backend = _validate_gemma_residency({**runtime, "device": device, "gpu_layers": 0 if cpu else runtime["gpu_layers"]})
    note(
        f"gemma resident: backend={backend} strict residency verified "
        f"kv={runtime['cache_type_k']}/{runtime['cache_type_v']} flash_attn={runtime['flash_attn']}"
    )
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
    ]
    env = _vulkan_env()
    env["TRIDENT_FASTCONV"] = "1" if runtime.get("fastconv") else "0"
    for key in ("GGML_VK_MEMORY_LOGGER", "GGML_VK_PERF_LOGGER", "GGML_VK_SYNC_LOGGER"):
        env.setdefault(key, "1")
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
        "vulkan": {"disable_coopmat": True, "disable_coopmat2": True},
        "port": port,
    }
    url = _ensure(
        "chatterbox", server, t3_model, command, env, identity_extra, probe,
        replace_owned_mismatch=True,
        state_extra={
            "family": family_name,
            "language": language,
            "reference": str(reference.resolve()),
            "codec": str(codec_model.resolve()),
        },
    )
    backend = _validate_chatterbox_backend(runtime)
    note(
        f"chatterbox resident: backend={backend} verified family={family_name} language={language} "
        "model-resident=1 reference-conditionals-resident=1 watermark=absent"
    )
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
            "device": identity.get("device"), "gpu_layers": identity.get("gpu_layers", runtime.get("gpu_layers")),
        })
    return rows
