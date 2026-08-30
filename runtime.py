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

from config import CHATTERBOX, FLASH_ATTN, GEMMA_FILE, GEMMA_GEN, HARDWARE, PARAKEET_FILE, PORTS, RUNTIMES, TTS_MODELS, TTS_PROFILES, V3_LANGUAGES, VULKAN_ENV, Paths, emit, find_exe, git_sha, load_settings, raise_worker_failure, run_file, voice_wav

_PROCS: dict[str, subprocess.Popen] = {}
_READERS: dict[str, threading.Thread] = {}
_READY = {"parakeet": "parakeet-server: listening on ", "gemma": "llama_server: listening on http://127.0.0.1:"}
_JOURNAL_SKIP = {"tts.frame"}

def _exe(folder: str, name: str) -> Path:
    path = find_exe(RUNTIMES / folder, name)
    if path is None:
        raise RuntimeError(f"{name} missing; run python main.py install")
    return path

def _forward(name: str, proc: subprocess.Popen, path: Path, ready: threading.Event) -> None:
    context = {}
    with path.open("wb") as out:
        for raw in proc.stdout:
            out.write(raw)
            out.flush()
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if name in _READY and _READY[name] in line:
                ready.set()
            if not line.startswith("{"):
                continue
            data = json.loads(line)
            event = data.pop("event")
            source_ts = data.pop("ts", None)
            if event == "tts.ready":
                ready.set()
            if event == "tts.batch.begin":
                context = {key: data[key] for key in ("epoch", "response_id", "piece_id", "piece_last_id", "pieces")}
            if event in ("t3", "t3.ready", "s3gen.begin", "s3gen"):
                data.update(context)
            if event not in _JOURNAL_SKIP:
                emit(event, producer=name, producer_ts=source_ts, **data)
            if event in ("tts.batch.done", "tts.batch.cancel"):
                context = {}

def _start(name: str, cmd: list[str], cwd: Path, paths: Paths, tag: str) -> None:
    env = os.environ.copy()
    env.update(VULKAN_ENV)
    log = run_file(f"resident-{name}-{tag}")
    emit("resident.start", name=name, tag=tag, log=log.name)
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    _PROCS[name] = proc
    ready = threading.Event()
    reader = threading.Thread(target=_forward, args=(name, proc, log, ready), name=f"resident:{name}")
    _READERS[name] = reader
    reader.start()
    deadline = time.monotonic() + (300 if name == "chatterbox" else 180)
    while not ready.wait(.1):
        raise_worker_failure()
        if proc.poll() is not None:
            raise RuntimeError(f"{name} exited before ready pid={proc.pid} exit={proc.returncode}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"{name} did not become ready")
    emit("resident.ready", name=name, pid=proc.pid)

def boot(paths: Paths, family: str = "nano", language: str = "en") -> None:
    family, language = family.strip().lower(), language.strip().lower()
    if family not in TTS_MODELS:
        raise RuntimeError(f"unknown TTS family {family!r}")
    if family != "v3" and language != "en":
        raise RuntimeError(f"{family} supports English only")
    if family == "v3" and language not in V3_LANGUAGES:
        raise RuntimeError(f"V3 language {language!r} is not supported")
    parakeet, gemma, tts = _exe("parakeet", "parakeet-server.exe"), _exe("gemma", "llama-server.exe"), _exe("tts", "trident-tts-server.exe")
    k = TTS_PROFILES[family]
    t3_file, codec_file = TTS_MODELS[family]
    settings = load_settings(paths.data_dir)
    commands = (
        ("parakeet", Path(PARAKEET_FILE).stem, parakeet, [str(parakeet), "--model", str(paths.models_dir / PARAKEET_FILE), "--port", str(PORTS["parakeet"])]),
        ("gemma", Path(GEMMA_FILE).stem, gemma, [str(gemma), "-m", str(paths.models_dir / GEMMA_FILE), "--alias", "gemma", "--host", "127.0.0.1", "--port", str(PORTS["gemma"]), "--offline", "--n-gpu-layers", "all", "--ctx-size", "4096", "--no-mmproj", "--flash-attn", FLASH_ATTN, "--threads", "2", "--threads-batch", "2", "--poll", "0", "--poll-batch", "0", "--threads-http", "1", "--no-ui", "--reasoning", "off"]),
        ("chatterbox", f"{family}-{Path(t3_file).stem}", tts, [str(tts), "--family", family, "--model", str(paths.models_dir / t3_file), "--s3gen-gguf", str(paths.models_dir / codec_file), "--reference", str(voice_wav(paths.data_dir, settings["tts_voice"])), "--language", language, "--port", str(PORTS["chatterbox"]), "--n-gpu-layers", str(k["gpu_layers"]), "--context", str(k["context"]), "--threads", str(k["threads"]), "--seed", str(k["seed"]), "--max-tokens", str(k["max_tokens"]), "--top-k", str(k["top_k"]), "--top-p", str(k["top_p"]), "--min-p", str(k["min_p"]), "--temperature", str(k["temperature"]), "--repeat-penalty", str(k["repeat_penalty"]), "--cfg-weight", str(k["cfg_weight"]), "--exaggeration", str(k["exaggeration"]), "--cfm-steps", str(k["cfm_steps"]), "--fastconv", str(k["fastconv"])]),
    )
    emit("boot.begin", family=family, language=language, voice=settings["tts_voice"], hardware=HARDWARE, t3=t3_file, codec=codec_file, chatterbox_sha=git_sha(CHATTERBOX), knobs=k)
    for name, tag, exe, cmd in commands:
        _start(name, cmd, exe.parent, paths, tag)
    emit("boot.ready", family=family, language=language, voice=settings["tts_voice"])

def require_alive(name: str) -> str:
    proc = _PROCS.get(name)
    if proc is None or proc.poll() is not None:
        raise RuntimeError(f"{name} is not running")
    return f"http://127.0.0.1:{PORTS[name]}"

def check_residents() -> None:
    raise_worker_failure()
    for name, proc in _PROCS.items():
        if proc.poll() is not None:
            raise RuntimeError(f"{name} exited pid={proc.pid} exit={proc.returncode}")

def stop_all() -> None:
    for name, proc in reversed(tuple(_PROCS.items())):
        running = proc.poll() is None
        emit("resident.stop", name=name, pid=proc.pid, running=running)
        if running:
            proc.kill()
        code = proc.wait()
        _READERS[name].join()
        emit("resident.stopped", name=name, pid=proc.pid, exit_code=code)
    _READERS.clear()
    _PROCS.clear()

def transcribe(base: str, wav: bytes) -> str:
    boundary = "----trident" + secrets.token_hex(8)
    body = f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"utterance.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode() + wav + f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nparakeet\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(base + "/v1/audio/transcriptions", body, {"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=3600) as response:
        return str(json.loads(response.read()).get("text") or "").strip()

def gemma_stream(base: str, messages: list[dict[str, str]]):
    payload = {"model": "gemma", "messages": messages, "stream": True, "cache_prompt": True, **GEMMA_GEN, "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(base + "/v1/chat/completions", json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(), {"Content-Type": "application/json", "Accept": "text/event-stream"})
    with urllib.request.urlopen(req, timeout=3600) as response:
        while line := response.readline():
            if line.startswith(b"data:"):
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
            sock = self.sock
            if sock is None:
                return None
            try:
                chunk = sock.recv(65536)
            except OSError:
                if self.sock is None:
                    return None
                raise
            if not chunk:
                if self.sock is None:
                    return None
                raise RuntimeError("unexpected TTS socket EOF")
            self.buf.extend(chunk)
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def send(self, epoch: int, response_id: int, piece_id: int, text: str = "") -> None:
        raw = text.encode()
        with self.lock:
            if self.sock is None:
                raise RuntimeError("TTS socket closed")
            self.sock.sendall(struct.pack("<IIII", epoch, response_id, piece_id, len(raw)) + raw)

    def recv_frame(self) -> tuple[int, int, int, int, int, bytes] | None:
        header = self._recv(24)
        if header is None:
            return None
        kind, epoch, response_id, piece_id, chunk_id, length = struct.unpack("<IIIIII", header)
        payload = self._recv(length) if length else b""
        return None if payload is None else (kind, epoch, response_id, piece_id, chunk_id, payload)

    def close(self) -> None:
        with self.lock:
            sock, self.sock = self.sock, None
        if sock is not None:
            sock.shutdown(socket.SHUT_RDWR)
            sock.close()
