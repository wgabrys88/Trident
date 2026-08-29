from __future__ import annotations

import hashlib
import http.client
import json
import select
import shutil
import socket
import struct
import subprocess
import time
import urllib.parse
import urllib.request
import wave
from pathlib import Path

import msvcrt

from config import (
    CODEC_FILE, FLASH_ATTN, GEMMA_FILE, GEMMA_GEN, PARAKEET_FILE, PORTS, RUNTIMES, T3_FILE, TTS_KNOBS,
    TTS_RATE, URLS, VULKAN_ENV, find_exe, log, vulkan_env,
)

_STILL_ACTIVE = 259


def _state_dir() -> Path:
    path = RUNTIMES / ".resident"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _lock(name: str):
    path = _state_dir() / f"{name}.lock"
    handle = path.open("a+b")
    if path.stat().st_size == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    return handle


def _unlock(handle) -> None:
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    handle.close()


def _read(name: str) -> dict:
    path = _state_dir() / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _write(name: str, state: dict) -> None:
    path = _state_dir() / f"{name}.json"
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = k32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    code = wintypes.DWORD()
    ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
    k32.CloseHandle(handle)
    return bool(ok) and int(code.value) == _STILL_ACTIVE


def _port(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def _http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "trident/1"}), timeout=1) as r:
            return int(r.status) == 200
    except Exception:
        return False


def _probe(name: str) -> bool:
    if name == "gemma":
        return _http_ok(f"{URLS['gemma']}/health")
    return _port(PORTS[name])


def _kill(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _ident(files: dict[str, Path], extra: dict) -> str:
    payload = {
        "files": {k: {"path": str(p.resolve()), "size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns} for k, p in sorted(files.items())},
        "extra": extra,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _launch(name: str, cmd: list[str], cwd: Path, files: dict[str, Path], extra: dict, env=None) -> str:
    handle = _lock(name)
    try:
        ident = _ident(files, extra)
        state = _read(name)
        pid = int(state.get("pid") or 0)
        if _alive(pid) and _probe(name) and state.get("identity") == ident:
            log(f"reuse {name} pid={pid}")
            return URLS[name]
        if _alive(pid):
            log(f"replace {name} pid={pid}")
            _kill(pid)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and _port(PORTS[name]):
                time.sleep(0.1)
        policy = "vulkan_f16=disabled" if env and env.get("GGML_VK_DISABLE_F16") == "1" else "vulkan_f16=default"
        log(f"start {name} {policy}")
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT, close_fds=True,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        _write(name, {"identity": ident, "pid": proc.pid, "port": PORTS[name], "url": URLS[name], **extra})
        deadline = time.monotonic() + (300 if name == "chatterbox" else 180)
        while time.monotonic() < deadline:
            if _probe(name):
                log(f"ready {name} pid={proc.pid}")
                return URLS[name]
            if proc.poll() is not None:
                raise RuntimeError(f"{name} exited before ready: pid={proc.pid} exit={proc.returncode}")
            time.sleep(0.25)
        _kill(proc.pid)
        raise RuntimeError(f"{name} did not become ready")
    finally:
        _unlock(handle)


def model(models_dir: Path, name: str) -> Path:
    path = Path(models_dir) / name
    if not path.is_file():
        raise RuntimeError(f"missing {path}; run: python main.py")
    return path


def start_parakeet(models_dir: Path) -> str:
    server = find_exe(RUNTIMES / "parakeet", "parakeet-server.exe")
    gguf = model(models_dir, PARAKEET_FILE)
    cmd = [str(server), "--model", str(gguf), "--port", str(PORTS["parakeet"])]
    return _launch("parakeet", cmd, server.parent, {"server": server, "model": gguf}, {"argv": cmd[1:], "vulkan_env": VULKAN_ENV}, vulkan_env())


def start_gemma(models_dir: Path) -> str:
    server = find_exe(RUNTIMES / "gemma", "llama-server.exe")
    gguf = model(models_dir, GEMMA_FILE)
    host, port = "127.0.0.1", PORTS["gemma"]
    cmd = [
        str(server), "-m", str(gguf), "--alias", "gemma", "--host", host, "--port", str(port), "--offline",
        "--n-gpu-layers", "all", "--ctx-size", "4096", "--no-mmproj", "--load-mode", "mmap",
        "--flash-attn", FLASH_ATTN, "--repack", "--fit", "off", "--kv-offload", "--op-offload",
        "--cache-type-k", "f16", "--cache-type-v", "f16", "--parallel", "1",
        "--threads", "2", "--threads-batch", "2", "--poll", "0", "--poll-batch", "0", "--threads-http", "1",
        "--cors-origins", "localhost", "--log-verbosity", "4", "--log-prefix", "--log-timestamps",
        "--cache-prompt", "--no-ui", "--reasoning", "off",
    ]
    return _launch("gemma", cmd, server.parent, {"server": server, "model": gguf}, {"argv": cmd[1:], "vulkan_env": VULKAN_ENV}, vulkan_env())


def start_chatterbox(models_dir: Path, reference: Path) -> str:
    server = find_exe(RUNTIMES / "tts", "trident-tts-server.exe")
    t3, codec = model(models_dir, T3_FILE), model(models_dir, CODEC_FILE)
    k = TTS_KNOBS
    cmd = [
        str(server), "--family", "nano", "--model", str(t3), "--s3gen-gguf", str(codec),
        "--reference", str(reference), "--language", "en", "--port", str(PORTS["chatterbox"]),
        "--n-gpu-layers", str(k["gpu_layers"]), "--context", str(k["context"]), "--threads", str(k["threads"]),
        "--seed", str(k["seed"]), "--max-tokens", str(k["max_tokens"]), "--top-k", str(k["top_k"]),
        "--top-p", str(k["top_p"]), "--min-p", str(k["min_p"]), "--temperature", str(k["temperature"]),
        "--repeat-penalty", str(k["repeat_penalty"]), "--cfg-weight", str(k["cfg_weight"]),
        "--exaggeration", str(k["exaggeration"]), "--cfm-steps", str(k["cfm_steps"]), "--fastconv", str(k["fastconv"]),
    ]
    return _launch(
        "chatterbox", cmd, server.parent,
        {"server": server, "model": t3, "codec": codec, "reference": reference},
        {"argv": cmd[1:], "family": "nano", "language": "en", "reference": str(reference.resolve())},
    )


def require_alive(name: str) -> str:
    state = _read(name)
    pid = int(state.get("pid") or 0)
    if not _alive(pid) or not _probe(name):
        raise RuntimeError(f"{name} is not running")
    return URLS[name]


def stop(name: str) -> None:
    handle = _lock(name)
    try:
        pid = int(_read(name).get("pid") or 0)
        if pid > 0:
            log(f"stop {name} pid={pid}")
            _kill(pid)
        (_state_dir() / f"{name}.json").unlink(missing_ok=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and _port(PORTS[name]):
            time.sleep(0.1)
    finally:
        _unlock(handle)


def stop_all() -> None:
    for name in ("parakeet", "gemma", "chatterbox"):
        stop(name)


def status() -> str:
    lines = []
    for name in ("parakeet", "gemma", "chatterbox"):
        state = _read(name)
        pid = int(state.get("pid") or 0)
        ready = _alive(pid) and _probe(name)
        lines.append(
            f"{name}: {'ready' if ready else 'stopped'} pid={pid or '-'} url={URLS[name]} "
            f"family={state.get('family') or '-'} language={state.get('language') or '-'}"
        )
    return "\n".join(lines)


def pcm24(src: Path, cache: Path) -> Path:
    src = src.resolve()
    try:
        with wave.open(str(src), "rb") as audio:
            ok = audio.getsampwidth() == 2 and audio.getnchannels() == 1 and audio.getframerate() == TTS_RATE and audio.getnframes() > 0
    except wave.Error:
        ok = False
    if ok:
        return src
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / f"{src.stem}.wav"
    if dest.is_file() and dest.stat().st_mtime_ns >= src.stat().st_mtime_ns:
        return dest
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not installed")
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
           "-i", str(src), "-vn", "-ac", "1", "-ar", str(TTS_RATE), "-c:a", "pcm_s16le", str(dest)]
    log("ffmpeg reference wav")
    subprocess.check_call(cmd, creationflags=subprocess.CREATE_NO_WINDOW)
    return dest


def transcribe(base: str, wav: Path) -> str:
    import secrets
    boundary = "----------trident" + secrets.token_hex(8)
    blob = wav.read_bytes()
    head = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{wav.name}\"\r\n"
        "Content-Type: audio/wav\r\n\r\n"
    ).encode("ascii")
    tail = (
        f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nparakeet\r\n--{boundary}--\r\n"
    ).encode("ascii")
    parsed = urllib.parse.urlsplit(base)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=3600)
    try:
        conn.putrequest("POST", "/v1/audio/transcriptions")
        conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        conn.putheader("Content-Length", str(len(head) + len(blob) + len(tail)))
        conn.putheader("Accept", "application/json")
        conn.endheaders()
        conn.send(head + blob + tail)
        resp = conn.getresponse()
        body = resp.read()
        if not 200 <= resp.status < 300:
            raise RuntimeError("Parakeet HTTP " + str(resp.status) + " " + body.decode("utf-8", "replace")[:500])
        return str(json.loads(body.decode("utf-8")).get("text") or "").strip()
    finally:
        conn.close()


def gemma_stream(base: str, messages: list) -> object:
    g = GEMMA_GEN
    payload = {
        "model": "gemma", "messages": messages, "stream": True, "cache_prompt": True,
        "temperature": g["temperature"], "top_p": g["top_p"], "top_k": g["top_k"], "min_p": g["min_p"],
        "repeat_penalty": g["repeat_penalty"], "seed": g["seed"], "max_tokens": g["max_tokens"],
        "chat_template_kwargs": {"enable_thinking": False},
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    parsed = urllib.parse.urlsplit(base)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=3600)
    try:
        conn.putrequest("POST", "/v1/chat/completions")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Accept", "text/event-stream")
        conn.putheader("Content-Length", str(len(data)))
        conn.endheaders()
        conn.send(data)
        response = conn.getresponse()
        if not 200 <= response.status < 300:
            raise RuntimeError("Gemma HTTP " + str(response.status) + " " + response.read().decode("utf-8", "replace")[:500])
        while line := response.readline():
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            chunk = line[5:].strip()
            if chunk == b"[DONE]":
                break
            text = str((json.loads(chunk.decode("utf-8")).get("choices") or [{}])[0].get("delta", {}).get("content") or "")
            if text:
                yield text
    finally:
        conn.close()


class Chatterbox:
    def __init__(self, url: str, cancel=None) -> None:
        self._parsed = urllib.parse.urlsplit(url)
        self._cancel = cancel
        self._sock = None
        self._closed = False

    def open(self) -> None:
        self._sock = socket.create_connection((self._parsed.hostname, self._parsed.port or 17933), timeout=3600)

    def _recv(self, n: int) -> bytes:
        data = bytearray()
        while len(data) < n:
            if self._closed:
                raise InterruptedError
            if self._cancel and self._cancel():
                raise InterruptedError
            r, _, _ = select.select([self._sock], [], [], 0.05)
            if not r:
                continue
            part = self._sock.recv(n - len(data))
            if not part:
                raise InterruptedError if self._closed else RuntimeError("TTS closed early")
            data.extend(part)
        return bytes(data)

    def send(self, text: str) -> None:
        raw = text.encode("utf-8")
        self._sock.sendall(struct.pack("<I", 1) + struct.pack("<I", len(raw)) + raw)

    def __iter__(self):
        while True:
            kind, length = struct.unpack("<II", self._recv(8))
            payload = self._recv(length) if length else b""
            if kind == 2:
                yield payload
            elif kind == 0:
                return
            elif kind == 1:
                raise RuntimeError("TTS: " + payload.decode("utf-8", "replace"))
            else:
                raise RuntimeError(f"TTS frame {kind}")

    def close(self) -> None:
        self._closed = True
        if self._sock is None:
            return
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
        self._sock = None
