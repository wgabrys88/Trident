from __future__ import annotations

import http.client
import json
import os
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from config import (
    CHATTERBOX, FLASH_ATTN, GEMMA_CONTEXT, GEMMA_FILE, GEMMA_GEN, PARAKEET_FILE, PORTS, RUNTIMES,
    TTS_MODELS, TTS_PROFILES, V3_LANGUAGES, VULKAN_ENV, Paths, find_exe, git_sha,
    load_settings, voice_wav,
)

PROTOCOL_MAGIC = 0x32525454
PROTOCOL_VERSION = 2
REQ_SYNTH, REQ_ADVANCE, REQ_CLOSE = 1, 2, 3
RESP_PCM, RESP_DONE, RESP_CANCELLED, RESP_ERROR, RESP_CLOSED = 1, 2, 3, 4, 5
_READY = {"parakeet": "parakeet-server: listening on ", "gemma": "llama_server: listening on http://127.0.0.1:"}
_CTRL_C_HELPER = """import ctypes,sys,time
k=ctypes.WinDLL('kernel32', use_last_error=True)
pid=int(sys.argv[1])
k.FreeConsole()
if not k.AttachConsole(pid): raise ctypes.WinError(ctypes.get_last_error())
if not k.SetConsoleCtrlHandler(None, True): raise ctypes.WinError(ctypes.get_last_error())
if not k.GenerateConsoleCtrlEvent(0, 0): raise ctypes.WinError(ctypes.get_last_error())
time.sleep(0.5)
k.FreeConsole()
"""


def _exe(folder: str, name: str) -> Path:
    path = find_exe(RUNTIMES / folder, name)
    if path is None:
        raise RuntimeError(f"{name} missing; run python main.py install")
    return path


def _listening_ports() -> list[str]:
    listening = []
    for name, port in PORTS.items():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0: listening.append(f"{name}:{port}")
    return listening


class Residents:
    def __init__(self, paths: Paths) -> None:
        self.paths = paths
        self.journal = paths.journal
        self.supervisor = paths.supervisor
        self.procs: dict[str, subprocess.Popen] = {}
        self.readers: dict[str, threading.Thread] = {}
        self.ready: dict[str, threading.Event] = {}
        self.ready_deadlines: dict[str, float] = {}
        self.chatterbox: Chatterbox | None = None
        self.chatterbox_reader: threading.Thread | None = None
        self.chatterbox_closed = threading.Event()
        self.chatterbox_close_requested = False

    def _forward(self, name: str, proc: subprocess.Popen, path: Path, ready: threading.Event) -> None:
        with path.open("wb", buffering=0) as out:
            assert proc.stdout is not None
            for raw in proc.stdout:
                out.write(raw)
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if name in _READY and _READY[name] in line:
                    ready.set()
                if not line.startswith("{"):
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event = str(data.pop("event", "native"))
                component = str(data.pop("component", name))
                if name == "chatterbox" and event == "server.ready":
                    ready.set()
                native = {
                    f"native_{key}": value for key, value in data.items()
                    if key in ("schema_version", "run_id", "sequence", "wall_timestamp", "monotonic_ns")
                }
                for key in tuple(native):
                    data.pop(key.removeprefix("native_"), None)
                self.journal.emit(component, event, producer=name, **native, **data)

    def _start(self, name: str, cmd: list[str], cwd: Path, timeout: float) -> None:
        env = os.environ.copy(); env.update(VULKAN_ENV)
        log = self.journal.sidecar(name)
        self.journal.emit("resident", "start", name=name, executable=str(cmd[0]), sidecar=log.name)
        startup = subprocess.STARTUPINFO(); startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW; startup.wShowWindow = subprocess.SW_HIDE
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup,
        )
        self.procs[name] = proc
        ready = self.ready[name] = threading.Event()
        self.ready_deadlines[name] = time.monotonic() + timeout
        self.readers[name] = self.supervisor.start(f"resident-{name}", self._forward, name, proc, log, ready)
        while not ready.wait(.1):
            self.supervisor.check()
            if proc.poll() is not None:
                raise RuntimeError(f"{name} exited before ready pid={proc.pid} exit={proc.returncode}")
            if time.monotonic() >= self.ready_deadlines[name]:
                raise RuntimeError(f"{name} did not become ready")
        self.journal.emit("resident", "ready", name=name, pid=proc.pid)

    def boot(self, family: str = "nano", language: str = "en") -> None:
        occupied = _listening_ports()
        if occupied: raise RuntimeError(f"Trident ports already occupied: {', '.join(occupied)}")
        family, language = family.strip().lower(), language.strip().lower()
        if family not in TTS_MODELS:
            raise RuntimeError(f"unknown TTS family {family!r}")
        if family != "v3" and language != "en":
            raise RuntimeError(f"{family} supports English only")
        if family == "v3" and language not in V3_LANGUAGES:
            raise RuntimeError(f"V3 language {language!r} is not supported")
        tts = _exe("tts", "trident-tts-server.exe")
        knobs = TTS_PROFILES[family]
        t3_file, codec_file = TTS_MODELS[family]
        settings = load_settings(self.paths.data_dir)
        chatterbox_cmd = [
            str(tts), "--run-id", self.journal.run_id, "--family", family,
            "--model", str(self.paths.models_dir / t3_file), "--s3gen-gguf", str(self.paths.models_dir / codec_file),
            "--reference", str(voice_wav(self.paths.data_dir, settings["tts_voice"])), "--language", language,
            "--port", str(PORTS["chatterbox"]), "--n-gpu-layers", str(knobs["gpu_layers"]),
            "--context", str(knobs["context"]), "--threads", str(knobs["threads"]), "--seed", str(knobs["seed"]),
            "--max-tokens", str(knobs["max_tokens"]), "--top-k", str(knobs["top_k"]), "--top-p", str(knobs["top_p"]),
            "--min-p", str(knobs["min_p"]), "--temperature", str(knobs["temperature"]),
            "--repeat-penalty", str(knobs["repeat_penalty"]), "--cfg-weight", str(knobs["cfg_weight"]),
            "--exaggeration", str(knobs["exaggeration"]), "--cfm-steps", str(knobs["cfm_steps"]),
            "--fastconv", str(knobs["fastconv"]),
        ]
        commands: list[tuple[str, Path, list[str], float]] = []
        if self.paths.command == "talk":
            parakeet, gemma = _exe("parakeet", "parakeet-server.exe"), _exe("gemma", "llama-server.exe")
            commands.extend([
                ("parakeet", parakeet.parent, [str(parakeet), "--model", str(self.paths.models_dir / PARAKEET_FILE), "--port", str(PORTS["parakeet"])], 180),
                ("gemma", gemma.parent, [str(gemma), "-m", str(self.paths.models_dir / GEMMA_FILE), "--alias", "gemma", "--host", "127.0.0.1", "--port", str(PORTS["gemma"]), "--offline", "--device", "Vulkan0", "--n-gpu-layers", "all", "--ctx-size", str(GEMMA_CONTEXT), "--no-mmproj", "--flash-attn", FLASH_ATTN, "--threads", "2", "--threads-batch", "2", "--poll", "0", "--poll-batch", "0", "--threads-http", "1", "--no-ui", "--reasoning", "off"], 180),
            ])
        elif self.paths.command != "tts":
            raise RuntimeError(f"cannot boot command {self.paths.command!r}")
        commands.append(("chatterbox", tts.parent, chatterbox_cmd, 300))
        self.journal.emit("runtime", "boot.start", command=self.paths.command, family=family, language=language, voice=settings["tts_voice"], t3=t3_file, codec=codec_file, chatterbox_sha=git_sha(CHATTERBOX), knobs=knobs)
        for name, cwd, command, timeout in commands:
            self._start(name, command, cwd, timeout)
            if name == "chatterbox":
                self.chatterbox_client()
        self.journal.emit("runtime", "boot.ready", command=self.paths.command, family=family, language=language)

    def require_alive(self, name: str) -> str:
        proc = self.procs.get(name)
        if proc is None or proc.poll() is not None:
            raise RuntimeError(f"{name} is not running")
        return f"http://127.0.0.1:{PORTS[name]}"

    def check(self) -> None:
        self.supervisor.check()
        for name, proc in self.procs.items():
            if proc.poll() is not None and not (name == "chatterbox" and self.chatterbox_closed.is_set() and proc.returncode == 0):
                raise RuntimeError(f"{name} exited pid={proc.pid} exit={proc.returncode}")

    def chatterbox_client(self) -> Chatterbox:
        if self.chatterbox is None:
            self.chatterbox = Chatterbox()
        if self.chatterbox.sock is None and not self.chatterbox_closed.is_set():
            self.chatterbox.open()
            self.journal.emit("resident", "client.connected", name="chatterbox", protocol_version=PROTOCOL_VERSION)
        return self.chatterbox

    def register_chatterbox_reader(self, reader: threading.Thread) -> None:
        self.chatterbox_reader = reader

    def close_chatterbox(self) -> None:
        if self.chatterbox_closed.is_set(): return
        client = self.chatterbox_client()
        if not self.chatterbox_close_requested:
            client.request_close(); self.chatterbox_close_requested = True
            self.journal.emit("resident", "protocol.close.requested", name="chatterbox", protocol_version=PROTOCOL_VERSION)
        deadline = time.monotonic() + 10
        reader = self.chatterbox_reader
        if reader is not None and reader.is_alive():
            while not self.chatterbox_closed.wait(.05):
                self.supervisor.check()
                if time.monotonic() >= deadline: raise RuntimeError("native close handshake timed out")
            reader.join(timeout=2)
            if reader.is_alive(): raise RuntimeError("native TTS reader survived close handshake")
        else:
            assert client.sock is not None
            client.sock.settimeout(10)
            while time.monotonic() < deadline:
                frame = client.recv_frame()
                if frame is not None and frame[0] == RESP_CLOSED:
                    self.chatterbox_closed.set(); break
            if not self.chatterbox_closed.is_set(): raise RuntimeError("native close handshake timed out")
        self.journal.emit("resident", "protocol.closed", name="chatterbox", protocol_version=PROTOCOL_VERSION)

    def _wait_ready(self, name: str, proc: subprocess.Popen) -> None:
        ready = self.ready.get(name)
        if ready is None or ready.is_set() or proc.poll() is not None: return
        deadline = self.ready_deadlines.get(name, time.monotonic() + 10)
        self.journal.emit("resident", "stop.waiting_for_ready", name=name, pid=proc.pid)
        while proc.poll() is None and not ready.wait(.1):
            self.supervisor.check()
            if time.monotonic() >= deadline: raise RuntimeError(f"{name} did not become ready for graceful shutdown")

    def _ctrl_c(self, name: str, proc: subprocess.Popen) -> None:
        result = subprocess.run(
            [sys.executable, "-c", _CTRL_C_HELPER, str(proc.pid)], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
            text=True, encoding="utf-8", errors="replace", timeout=5,
        )
        if result.returncode:
            raise RuntimeError(f"{name} CTRL_C helper failed exit={result.returncode}: {result.stdout.strip()}")
        self.journal.emit("resident", "signal.delivered", name=name, pid=proc.pid, signal="CTRL_C_EVENT", scope="resident-console")

    def stop(self) -> None:
        forced: list[str] = []
        bad_exits: list[str] = []
        reader_survivors: list[str] = []
        protocol_errors: list[str] = []
        signal_errors: list[str] = []
        chatterbox_proc = self.procs.get("chatterbox")
        if chatterbox_proc is not None and chatterbox_proc.poll() is None and not self.chatterbox_closed.is_set():
            try:
                self._wait_ready("chatterbox", chatterbox_proc); self.close_chatterbox()
            except BaseException as error:
                protocol_errors.append(str(error)); self.journal.failure("resident.chatterbox-close", error)
        for name, proc in reversed(tuple(self.procs.items())):
            running = proc.poll() is None
            self.journal.emit("resident", "stop", name=name, pid=proc.pid, running=running)
            if running:
                try:
                    if name == "chatterbox" and self.chatterbox_closed.is_set():
                        proc.wait(timeout=10)
                    else:
                        self._wait_ready(name, proc)
                        if name != "chatterbox": self._ctrl_c(name, proc)
                        proc.wait(timeout=10)
                except (ValueError, OSError, subprocess.TimeoutExpired, RuntimeError) as error:
                    signal_errors.append(f"{name}:{error}")
                    if proc.poll() is None:
                        forced.append(name); proc.kill(); proc.wait(timeout=5)
            code = proc.wait()
            reader = self.readers.get(name)
            if reader is not None:
                reader.join(timeout=5)
                if reader.is_alive(): reader_survivors.append(name)
            clean = code == 0 and name not in forced
            if not clean: bad_exits.append(f"{name}:{code}")
            self.journal.emit("resident", "stopped", name=name, pid=proc.pid, exit_code=code, clean=clean)
        if self.chatterbox is not None: self.chatterbox.disconnect()
        self.procs.clear(); self.readers.clear(); self.ready.clear(); self.ready_deadlines.clear()
        listening = _listening_ports()
        if forced or bad_exits or reader_survivors or protocol_errors or signal_errors or listening:
            detail = {"forced": forced, "bad_exits": bad_exits, "reader_survivors": reader_survivors, "protocol_errors": protocol_errors, "signal_errors": signal_errors, "listeners": listening}
            self.journal.emit("resident", "failed", error="shutdown survivors", **detail)
            raise RuntimeError(f"resident shutdown incomplete: {detail}")
        self.journal.emit("resident", "drained", ports=list(PORTS.values()))


class CancelableHTTP:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: tuple[http.client.HTTPConnection, http.client.HTTPResponse | None] | None = None
        self._generation = 0

    @staticmethod
    def _interrupt(conn: http.client.HTTPConnection) -> None:
        sock = conn.sock
        if sock is not None:
            try: sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass

    @classmethod
    def _disconnect(cls, conn: http.client.HTTPConnection) -> None:
        cls._interrupt(conn); conn.close()

    def open(self, url: str, body: bytes, headers: dict[str, str]) -> http.client.HTTPResponse:
        target = urllib.parse.urlsplit(url)
        if target.scheme != "http" or target.hostname not in ("127.0.0.1", "localhost"):
            raise RuntimeError("resident HTTP target must be loopback HTTP")
        conn = http.client.HTTPConnection(target.hostname, target.port, timeout=3600)
        with self._lock:
            if self._active is not None: raise RuntimeError("HTTP channel already has an active request")
            generation = self._generation; self._active = (conn, None)
        try:
            path = target.path or "/"
            if target.query: path += "?" + target.query
            conn.request("POST", path, body=body, headers=headers)
            response = conn.getresponse()
            with self._lock:
                if generation != self._generation or self._active is None or self._active[0] is not conn:
                    cancelled = True
                else:
                    self._active = (conn, response); cancelled = False
            if cancelled:
                response.close(); self._disconnect(conn); raise OSError("HTTP request cancelled before response ownership")
            return response
        except BaseException:
            with self._lock:
                if self._active is not None and self._active[0] is conn: self._active = None
            self._disconnect(conn)
            raise

    def clear(self, response: http.client.HTTPResponse) -> None:
        active = None
        with self._lock:
            if self._active is not None and self._active[1] is response:
                active, self._active = self._active, None
        try: response.close()
        finally:
            if active is not None: self._disconnect(active[0])

    def close(self) -> None:
        with self._lock:
            self._generation += 1; active, self._active = self._active, None
        if active is not None: self._interrupt(active[0])


def transcribe(base: str, wav: bytes, channel: CancelableHTTP) -> str:
    boundary = "----trident" + secrets.token_hex(8)
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"utterance.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode()
            + wav + f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nparakeet\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson\r\n--{boundary}--\r\n".encode())
    response = channel.open(base + "/v1/audio/transcriptions", body, {"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"})
    try:
        return str(json.loads(response.read()).get("text") or "").strip()
    finally:
        channel.clear(response)


def gemma_stream(base: str, messages: list[dict[str, str]], channel: CancelableHTTP):
    payload = {"model": "gemma", "messages": messages, "stream": True, "cache_prompt": True, **GEMMA_GEN, "chat_template_kwargs": {"enable_thinking": False}}
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    response = channel.open(base + "/v1/chat/completions", body, {"Content-Type": "application/json", "Accept": "text/event-stream"})
    try:
        while line := response.readline():
            if not line.startswith(b"data:"):
                continue
            chunk = line[5:].strip()
            if chunk == b"[DONE]":
                return
            text = str((json.loads(chunk).get("choices") or [{}])[0].get("delta", {}).get("content") or "")
            if text:
                yield text
    finally:
        channel.clear(response)


class Chatterbox:
    def __init__(self) -> None:
        self.sock: socket.socket | None = None
        self.buf = bytearray()
        self.send_lock = threading.Lock()

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
        out = bytes(self.buf[:n]); del self.buf[:n]
        return out

    def _send(self, kind: int, epoch: int = 0, response_id: int = 0, piece_id: int = 0, text: str = "") -> None:
        raw = text.encode("utf-8")
        with self.send_lock:
            if self.sock is None:
                raise RuntimeError("TTS socket closed")
            self.sock.sendall(struct.pack("<IIIIIII", PROTOCOL_MAGIC, PROTOCOL_VERSION, kind, epoch, response_id, piece_id, len(raw)) + raw)

    def synthesize(self, epoch: int, response_id: int, piece_id: int, text: str) -> None:
        self._send(REQ_SYNTH, epoch, response_id, piece_id, text)

    def advance(self, epoch: int) -> None:
        self._send(REQ_ADVANCE, epoch)

    def request_close(self) -> None:
        self._send(REQ_CLOSE)

    def recv_frame(self) -> tuple[int, int, int, int, int, bytes] | None:
        header = self._recv(32)
        if header is None:
            return None
        magic, version, kind, epoch, response_id, piece_id, chunk_id, length = struct.unpack("<IIIIIIII", header)
        if magic != PROTOCOL_MAGIC or version != PROTOCOL_VERSION:
            raise RuntimeError("unsupported TTS response protocol")
        payload = self._recv(length) if length else b""
        return None if payload is None else (kind, epoch, response_id, piece_id, chunk_id, payload)

    def disconnect(self) -> None:
        with self.send_lock:
            sock, self.sock = self.sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
