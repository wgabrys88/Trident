from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import struct
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path

from config import (
    CODEC_FILE, FLASH_ATTN, GEMMA_FILE, GEMMA_GEN, PARAKEET_FILE, PORTS, RUNTIMES,
    T3_FILE, TTS_KNOBS, URLS, VULKAN_ENV, find_exe, log,
)

_STILL_ACTIVE = 259


def _state_dir() -> Path:
    path = RUNTIMES / ".resident"
    path.mkdir(parents=True, exist_ok=True)
    return path


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


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def _probe(name: str) -> bool:
    if name != "gemma":
        return _port_open(PORTS[name])
    import urllib.request
    try:
        with urllib.request.urlopen(URLS["gemma"] + "/health", timeout=1) as r:
            return r.status == 200
    except OSError:
        return False


def _kill(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _ident(files: dict[str, Path], extra: dict) -> str:
    payload = {
        "files": {k: {"path": str(p.resolve()), "size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns} for k, p in sorted(files.items())},
        "extra": extra,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _write(name: str, state: dict) -> None:
    path = _state_dir() / f"{name}.json"
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read(name: str) -> dict:
    path = _state_dir() / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _wait_port(port: int, deadline_s: float) -> bool:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if _port_open(port):
            return True
        time.sleep(0.25)
    return False


def _wait_probe(name: str, proc: subprocess.Popen, deadline_s: float) -> None:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if _probe(name):
            log(f"ready {name} pid={proc.pid}")
            return
        if proc.poll() is not None:
            raise RuntimeError(f"{name} exited before ready: pid={proc.pid} exit={proc.returncode}")
        time.sleep(0.25)
    _kill(proc.pid)
    raise RuntimeError(f"{name} did not become ready")


def _launch(name: str, cmd: list[str], cwd: Path, files: dict[str, Path], extra: dict) -> str:
    ident = _ident(files, extra)
    state = _read(name)
    pid = int(state.get("pid") or 0)
    if _alive(pid) and _probe(name) and state.get("identity") == ident:
        log(f"reuse {name} pid={pid}")
        return URLS[name]
    if _alive(pid):
        log(f"replace {name} pid={pid}")
        _kill(pid)
        _wait_port(PORTS[name], 10)
    env = os.environ.copy()
    env.update(VULKAN_ENV)
    log_path = _state_dir() / f"{name}.log"
    log(f"start {name} log={log_path}")
    with log_path.open("ab") as log_file:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=log_file,
            stderr=subprocess.STDOUT, close_fds=True,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    _write(name, {"identity": ident, "pid": proc.pid, "port": PORTS[name], "url": URLS[name], **extra})
    _wait_probe(name, proc, 300 if name == "chatterbox" else 180)
    return URLS[name]


def start_parakeet(models_dir: Path) -> str:
    server = find_exe(RUNTIMES / "parakeet", "parakeet-server.exe")
    if server is None:
        raise RuntimeError("parakeet-server.exe missing; run python main.py install")
    gguf = Path(models_dir) / PARAKEET_FILE
    cmd = [str(server), "--model", str(gguf), "--port", str(PORTS["parakeet"])]
    return _launch("parakeet", cmd, server.parent, {"server": server, "model": gguf}, {"argv": cmd[1:]})


def start_gemma(models_dir: Path) -> str:
    server = find_exe(RUNTIMES / "gemma", "llama-server.exe")
    if server is None:
        raise RuntimeError("llama-server.exe missing; run python main.py install")
    gguf = Path(models_dir) / GEMMA_FILE
    cmd = [
        str(server), "-m", str(gguf), "--alias", "gemma", "--host", "127.0.0.1", "--port", str(PORTS["gemma"]), "--offline",
        "--n-gpu-layers", "all", "--ctx-size", "4096", "--no-mmproj", "--load-mode", "mmap",
        "--flash-attn", FLASH_ATTN, "--repack", "--fit", "off", "--kv-offload", "--op-offload",
        "--cache-type-k", "f16", "--cache-type-v", "f16", "--parallel", "1",
        "--threads", "2", "--threads-batch", "2", "--poll", "0", "--poll-batch", "0", "--threads-http", "1",
        "--cors-origins", "localhost", "--log-verbosity", "4", "--log-prefix", "--log-timestamps",
        "--cache-prompt", "--no-ui", "--reasoning", "off",
    ]
    return _launch("gemma", cmd, server.parent, {"server": server, "model": gguf}, {"argv": cmd[1:]})


def start_chatterbox(models_dir: Path, reference: Path) -> str:
    server = find_exe(RUNTIMES / "tts", "trident-tts-server.exe")
    if server is None:
        raise RuntimeError("trident-tts-server.exe missing; run python main.py install")
    t3 = Path(models_dir) / T3_FILE
    codec = Path(models_dir) / CODEC_FILE
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
    pid = int(_read(name).get("pid") or 0)
    if pid > 0:
        log(f"stop {name} pid={pid}")
        _kill(pid)
    (_state_dir() / f"{name}.json").unlink(missing_ok=True)
    _wait_port(PORTS[name], 10)


def stop_all() -> None:
    for name in PORTS:
        stop(name)


def status() -> str:
    out = []
    for name in PORTS:
        state = _read(name)
        pid = int(state.get("pid") or 0)
        ready = _alive(pid) and _probe(name)
        out.append(
            f"{name}: {'ready' if ready else 'stopped'} pid={pid or '-'} url={URLS[name]} "
            f"family={state.get('family') or '-'} language={state.get('language') or '-'}"
        )
    return "\n".join(out)


def _http_post(base: str, path: str, body: bytes, headers: dict, stream: bool = False):
    parsed = urllib.parse.urlsplit(base)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=3600)
    owned = True
    try:
        conn.putrequest("POST", path, skip_host=False, skip_accept_encoding=True)
        for k, v in headers.items():
            conn.putheader(k, v)
        conn.endheaders()
        conn.send(body)
        response = conn.getresponse()
        if not 200 <= response.status < 300:
            raise RuntimeError(f"{path} HTTP {response.status} " + response.read().decode("utf-8", "replace")[:500])
        if stream:
            owned = False
            return conn, response
        return response.read()
    finally:
        if owned:
            conn.close()


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
    body = head + blob + tail
    raw = _http_post(base, "/v1/audio/transcriptions", body, {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
        "Accept": "application/json",
    })
    return str(json.loads(raw.decode("utf-8")).get("text") or "").strip()


def gemma_stream(base: str, messages: list[dict[str, str]]):
    g = GEMMA_GEN
    payload = {
        "model": "gemma", "messages": messages, "stream": True, "cache_prompt": True,
        "temperature": g["temperature"], "top_p": g["top_p"], "top_k": g["top_k"], "min_p": g["min_p"],
        "repeat_penalty": g["repeat_penalty"], "seed": g["seed"], "max_tokens": g["max_tokens"],
        "chat_template_kwargs": {"enable_thinking": False},
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    conn, response = _http_post(base, "/v1/chat/completions", data, {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Content-Length": str(len(data)),
    }, stream=True)
    try:
        while line := response.readline():
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            chunk = line[5:].strip()
            if chunk == b"[DONE]":
                return
            text = str((json.loads(chunk.decode("utf-8")).get("choices") or [{}])[0].get("delta", {}).get("content") or "")
            if text:
                yield text
    finally:
        conn.close()


class Chatterbox:
    def __init__(self, url: str) -> None:
        self._parsed = urllib.parse.urlsplit(url)
        self._sock: socket.socket | None = None
        self._buf = bytearray()
        self._send_lock = threading.Lock()

    def open(self) -> None:
        self._sock = socket.create_connection((self._parsed.hostname, self._parsed.port or 17933), timeout=3600)

    def _recv_exact(self, n: int) -> bytes | None:
        while len(self._buf) < n:
            if self._sock is None:
                return None
            chunk = self._sock.recv(65536)
            if not chunk:
                return None
            self._buf.extend(chunk)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def send(self, epoch: int, pieces: list[str]) -> None:
        if self._sock is None:
            raise RuntimeError("TTS socket closed")
        payload = b""
        for piece in pieces:
            raw = piece.encode("utf-8")
            payload += struct.pack("<I", len(raw)) + raw
        with self._send_lock:
            self._sock.sendall(struct.pack("<II", len(pieces), epoch) + payload)

    def recv_frame(self) -> tuple[int, int, bytes] | None:
        header = self._recv_exact(12)
        if header is None:
            return None
        kind, epoch, length = struct.unpack("<III", header)
        payload = b""
        if length:
            got = self._recv_exact(length)
            if got is None:
                return None
            payload = got
        return kind, epoch, payload

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is None:
            return
        sock.shutdown(socket.SHUT_RDWR)
        sock.close()
