from __future__ import annotations

import json
import os
import secrets
import socket
import struct
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from config import CODEC_FILE, FLASH_ATTN, GEMMA_FILE, GEMMA_GEN, PARAKEET_FILE, PORTS, RUNTIMES, T3_FILE, TTS_KNOBS, VULKAN_ENV, Paths, find_exe, load_settings, log, voice_wav

_PROCS: dict[str, subprocess.Popen] = {}

def _probe(name: str) -> bool:
    try:
        if name == "gemma":
            with urllib.request.urlopen(f"http://127.0.0.1:{PORTS[name]}/health", timeout=1) as r:
                return r.status == 200
        with socket.create_connection(("127.0.0.1", PORTS[name]), timeout=.25):
            return True
    except OSError:
        return False

def _exe(folder: str, name: str) -> Path:
    path = find_exe(RUNTIMES / folder, name)
    if path is None:
        raise RuntimeError(f"{name} missing; run python main.py install")
    return path

def _start(name: str, cmd: list[str], cwd: Path, paths: Paths) -> None:
    if _probe(name):
        raise RuntimeError(f"{name} port {PORTS[name]} already in use")
    env = os.environ.copy()
    env.update(VULKAN_ENV)
    native_log = paths.run_dir / f"{name}.log"
    log(f"resident start name={name} log={native_log}")
    with native_log.open("ab") as out:
        try:
            proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, close_fds=True)
        except OSError:
            stop_all()
            raise
    _PROCS[name] = proc
    end = time.monotonic() + (300 if name == "chatterbox" else 180)
    while time.monotonic() < end:
        if proc.poll() is not None:
            stop_all()
            raise RuntimeError(f"{name} exited before ready pid={proc.pid} exit={proc.returncode}")
        if _probe(name):
            log(f"resident ready name={name} pid={proc.pid}")
            return
        time.sleep(.25)
    stop_all()
    raise RuntimeError(f"{name} did not become ready")

def boot(paths: Paths) -> None:
    stop_all()
    parakeet, gemma, tts = _exe("parakeet", "parakeet-server.exe"), _exe("gemma", "llama-server.exe"), _exe("tts", "trident-tts-server.exe")
    k = TTS_KNOBS
    settings = load_settings(paths.data_dir)
    commands = (
        ("parakeet", parakeet, [str(parakeet), "--model", str(paths.models_dir / PARAKEET_FILE), "--port", str(PORTS["parakeet"])]),
        ("gemma", gemma, [str(gemma), "-m", str(paths.models_dir / GEMMA_FILE), "--alias", "gemma", "--host", "127.0.0.1", "--port", str(PORTS["gemma"]), "--offline", "--n-gpu-layers", "all", "--ctx-size", "4096", "--no-mmproj", "--flash-attn", FLASH_ATTN, "--threads", "2", "--threads-batch", "2", "--poll", "0", "--poll-batch", "0", "--threads-http", "1", "--no-ui", "--reasoning", "off"]),
        ("chatterbox", tts, [str(tts), "--family", "nano", "--model", str(paths.models_dir / T3_FILE), "--s3gen-gguf", str(paths.models_dir / CODEC_FILE), "--reference", str(voice_wav(paths.data_dir, settings["tts_voice"])), "--language", "en", "--port", str(PORTS["chatterbox"]), "--n-gpu-layers", str(k["gpu_layers"]), "--context", str(k["context"]), "--threads", str(k["threads"]), "--seed", str(k["seed"]), "--max-tokens", str(k["max_tokens"]), "--top-k", str(k["top_k"]), "--top-p", str(k["top_p"]), "--min-p", str(k["min_p"]), "--temperature", str(k["temperature"]), "--repeat-penalty", str(k["repeat_penalty"]), "--cfg-weight", str(k["cfg_weight"]), "--exaggeration", str(k["exaggeration"]), "--cfm-steps", str(k["cfm_steps"]), "--fastconv", str(k["fastconv"])]),
    )
    log("boot begin")
    for name, exe, cmd in commands:
        _start(name, cmd, exe.parent, paths)
    log("boot ready family=nano language=en")

def require_alive(name: str) -> str:
    proc = _PROCS.get(name)
    if proc is None or proc.poll() is not None or not _probe(name):
        raise RuntimeError(f"{name} is not running")
    return f"http://127.0.0.1:{PORTS[name]}"

def stop_all() -> None:
    for name, proc in tuple(_PROCS.items()):
        if proc.poll() is None:
            log(f"resident stop name={name} pid={proc.pid}")
            proc.kill()
            proc.wait()
    _PROCS.clear()

def transcribe(base: str, wav: bytes) -> str:
    boundary = "----trident" + secrets.token_hex(8)
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"turn.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode() + wav + f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nparakeet\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(base + "/v1/audio/transcriptions", body, {"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=3600) as r:
        return str(json.loads(r.read()).get("text") or "").strip()

def gemma_stream(base: str, messages: list[dict[str, str]]):
    payload = {"model": "gemma", "messages": messages, "stream": True, "cache_prompt": True, **GEMMA_GEN, "chat_template_kwargs": {"enable_thinking": False}}
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    req = urllib.request.Request(base + "/v1/chat/completions", data, {"Content-Type": "application/json", "Accept": "text/event-stream"})
    with urllib.request.urlopen(req, timeout=3600) as r:
        while line := r.readline():
            if not line.startswith(b"data:"):
                continue
            chunk = line[5:].strip()
            if chunk == b"[DONE]":
                return
            text = str((json.loads(chunk).get("choices") or [{}])[0].get("delta", {}).get("content") or "")
            if text:
                yield text

class Chatterbox:
    def __init__(self) -> None:
        self.sock: socket.socket | None = None
        self.buf = bytearray()
        self.lock = threading.Lock()

    def open(self) -> None:
        self.sock = socket.create_connection(("127.0.0.1", PORTS["chatterbox"]), timeout=3600)

    def _recv(self, n: int) -> bytes | None:
        while len(self.buf) < n:
            if self.sock is None:
                return None
            chunk = self.sock.recv(65536)
            if not chunk:
                return None
            self.buf.extend(chunk)
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def send(self, epoch: int, pieces: list[str]) -> None:
        if self.sock is None:
            raise RuntimeError("TTS socket closed")
        body = b"".join(struct.pack("<I", len(raw)) + raw for raw in map(str.encode, pieces))
        with self.lock:
            self.sock.sendall(struct.pack("<II", len(pieces), epoch) + body)

    def recv_frame(self) -> tuple[int, int, bytes] | None:
        header = self._recv(12)
        if header is None:
            return None
        kind, epoch, length = struct.unpack("<III", header)
        payload = self._recv(length) if length else b""
        return None if payload is None else (kind, epoch, payload)

    def close(self) -> None:
        sock, self.sock = self.sock, None
        if sock is not None:
            sock.shutdown(socket.SHUT_RDWR)
            sock.close()
