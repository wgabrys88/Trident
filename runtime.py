from __future__ import annotations

import os
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path

from config import (
    CHATTERBOX, PORTS, RUNTIMES, TTS_MODELS, TTS_PROFILES, VULKAN_ENV, Paths, find_exe, voice_wav,
)
from journal import git_identity

PROTOCOL_MAGIC, PROTOCOL_VERSION = 0x32525454, 2
REQ_SYNTH, REQ_CLOSE = 1, 3
RESP_PCM, RESP_DONE, RESP_CANCELLED, RESP_ERROR, RESP_CLOSED = 1, 2, 3, 4, 5
_TTS_FLAGS = (
    ("--n-gpu-layers", "gpu_layers"), ("--context", "context"), ("--threads", "threads"), ("--seed", "seed"),
    ("--max-tokens", "max_tokens"), ("--top-k", "top_k"), ("--top-p", "top_p"), ("--min-p", "min_p"),
    ("--temperature", "temperature"), ("--repeat-penalty", "repeat_penalty"), ("--cfg-weight", "cfg_weight"),
    ("--exaggeration", "exaggeration"), ("--cfm-steps", "cfm_steps"), ("--fastconv", "fastconv"),
)


def _exe(folder: str, name: str) -> Path:
    if path := find_exe(RUNTIMES / folder, name):
        return path
    raise RuntimeError(f"{name} missing; run python main.py install")


def _shutdown(sock) -> None:
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _listening_ports() -> list[str]:
    out = []
    for name, port in PORTS.items():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                out.append(f"{name}:{port}")
    return out


class Residents:
    def __init__(self, paths: Paths) -> None:
        self.paths, self.journal, self.supervisor = paths, paths.journal, paths.supervisor
        self.procs: dict[str, subprocess.Popen] = {}
        self.readers: dict[str, threading.Thread] = {}
        self.ready: dict[str, threading.Event] = {}
        self.ready_deadlines: dict[str, float] = {}
        self.chatterbox: Chatterbox | None = None
        self.chatterbox_closed = threading.Event()

    def _forward(self, name: str, proc: subprocess.Popen, path: Path, ready: threading.Event) -> None:
        with path.open("wb", buffering=0) as out:
            assert proc.stdout is not None
            marker = b'"event":"server.ready"'
            for raw in proc.stdout:
                out.write(raw)
                if marker and marker in raw:
                    ready.set()

    def _start(self, name: str, cmd: list[str], cwd: Path, timeout: float) -> None:
        env = os.environ.copy()
        env.update(VULKAN_ENV)
        log = self.journal.sidecar(name)
        self.journal.emit("resident", "start", name=name, executable=str(cmd[0]), sidecar=log.name)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup)
        self.procs[name] = proc
        ready = self.ready[name] = threading.Event()
        deadline = self.ready_deadlines[name] = time.monotonic() + timeout
        self.readers[name] = self.supervisor.start(f"resident-{name}", self._forward, name, proc, log, ready)
        self.supervisor.spin(ready.is_set, deadline, f"{name} did not become ready", interval=.1, event=ready,
            abort=lambda: None if proc.poll() is None else f"{name} exited before ready pid={proc.pid} exit={proc.returncode}")
        self.journal.emit("resident", "ready", name=name, pid=proc.pid)

    def boot(self, family: str = "nano", language: str = "en") -> None:
        if occupied := _listening_ports():
            raise RuntimeError(f"Trident ports already occupied: {', '.join(occupied)}")
        family = family.strip().lower()
        if family != "nano":
            raise RuntimeError("TTS-only mode supports nano family only")
        if language.strip().lower() != "en":
            raise RuntimeError("TTS-only mode supports English only")
        tts = _exe("tts", "chatterbox-server.exe")
        knobs, settings = TTS_PROFILES[family], {"tts_voice": self.paths.voice}
        t3_file, codec_file = TTS_MODELS[family]
        cmd = [str(tts), "--run-id", self.journal.run_id, "--family", family,
               "--model", str(self.paths.models_dir / t3_file),
               "--s3gen-gguf", str(self.paths.models_dir / codec_file),
               "--reference", str(voice_wav(self.paths.data_dir)),
               "--language", language, "--port", str(PORTS["chatterbox"]),
               *[x for flag, key in _TTS_FLAGS for x in (flag, str(knobs[key]))]]
        self.journal.emit("runtime", "boot.start", command=self.paths.command, family=family, language=language,
                          voice=settings["tts_voice"], t3=t3_file, codec=codec_file,
                          chatterbox_sha=git_identity(CHATTERBOX).get("sha") or "", knobs=knobs)
        self._start("chatterbox", cmd, tts.parent, 300)
        self.chatterbox_client()
        self.journal.emit("runtime", "boot.ready", command=self.paths.command, family=family, language=language)

    def chatterbox_client(self) -> Chatterbox:
        if self.chatterbox is None:
            self.chatterbox = Chatterbox()
        if self.chatterbox.sock is None and not self.chatterbox_closed.is_set():
            self.chatterbox.open()
        return self.chatterbox

    def check(self) -> None:
        self.supervisor.check()
        for name, proc in self.procs.items():
            if proc.poll() is not None and not (name == "chatterbox" and self.chatterbox_closed.is_set() and proc.returncode == 0):
                raise RuntimeError(f"{name} exited pid={proc.pid} exit={proc.returncode}")

    def close_chatterbox(self) -> None:
        if self.chatterbox_closed.is_set():
            return
        client = self.chatterbox_client()
        client.request_close()
        deadline = time.monotonic() + 10
        assert client.sock is not None
        client.sock.settimeout(10)
        while time.monotonic() < deadline:
            frame = client.recv_frame()
            if frame is not None and frame[0] == RESP_CLOSED:
                self.chatterbox_closed.set()
                break
        if not self.chatterbox_closed.is_set():
            raise RuntimeError("native close handshake timed out")
        self.journal.emit("resident", "protocol.closed", name="chatterbox", protocol_version=PROTOCOL_VERSION)

    def _reap(self, name: str, proc: subprocess.Popen, failures: list[str]) -> None:
        running = proc.poll() is None
        self.journal.emit("resident", "stop", name=name, pid=proc.pid, running=running)
        if running:
            try:
                if not (name == "chatterbox" and self.chatterbox_closed.is_set()):
                    proc.wait(timeout=10)
            except (ValueError, OSError, subprocess.TimeoutExpired, RuntimeError) as error:
                failures.append(f"{name}: {error}")
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
        if (reader := self.readers.get(name)) is not None:
            reader.join(timeout=5)
            if reader.is_alive():
                failures.append(f"{name}: log reader survived")
        if not (clean := (code := proc.wait()) == 0):
            failures.append(f"{name}: exit {code}")
        self.journal.emit("resident", "stopped", name=name, pid=proc.pid, exit_code=code, clean=clean)

    def stop(self) -> None:
        failures: list[str] = []
        if (cb := self.procs.get("chatterbox")) is not None and cb.poll() is None and not self.chatterbox_closed.is_set():
            try:
                self.close_chatterbox()
            except BaseException as error:
                failures.append(f"chatterbox close: {error}")
                self.journal.failure("resident.chatterbox-close", error)
        for name, proc in reversed(tuple(self.procs.items())):
            self._reap(name, proc, failures)
        if self.chatterbox is not None:
            self.chatterbox.disconnect()
        self.procs.clear()
        self.readers.clear()
        self.ready.clear()
        self.ready_deadlines.clear()
        if listening := _listening_ports():
            failures.append(f"ports survived: {', '.join(listening)}")
        if failures:
            raise RuntimeError("resident shutdown incomplete: " + "; ".join(failures))
        self.journal.emit("resident", "drained", ports=list(PORTS.values()))


class Chatterbox:
    def __init__(self) -> None:
        self.sock: socket.socket | None = None
        self.buf = bytearray()
        self.send_lock = threading.Lock()

    def open(self) -> None:
        self.sock = socket.create_connection(("127.0.0.1", PORTS["chatterbox"]), timeout=3600)

    def _recv(self, n: int) -> bytes | None:
        while len(self.buf) < n:
            if (sock := self.sock) is None:
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

    def _send(self, kind: int, epoch: int = 0, response_id: int = 0, piece_id: int = 0, text: str = "") -> None:
        raw = text.encode("utf-8")
        with self.send_lock:
            if self.sock is None:
                raise RuntimeError("TTS socket closed")
            self.sock.sendall(struct.pack("<IIIIIII", PROTOCOL_MAGIC, PROTOCOL_VERSION, kind, epoch, response_id, piece_id, len(raw)) + raw)

    def request_close(self) -> None:
        self._send(REQ_CLOSE)

    def recv_frame(self) -> tuple[int, int, int, int, int, bytes] | None:
        if (header := self._recv(32)) is None:
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
            _shutdown(sock)
            sock.close()
