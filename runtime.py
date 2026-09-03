from __future__ import annotations

import os
import socket
import struct
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from config import (
    CHATTERBOX, PORTS, RUNTIMES, TTS_MODELS, TTS_PROFILES, VULKAN_ENV, Paths, find_exe, voice_wav,
)
from journal import git_identity

PROTOCOL_MAGIC, PROTOCOL_VERSION = 0x32525454, 2
REQ_SYNTH, REQ_CLOSE = 1, 3
RESP_PCM, RESP_DONE, RESP_CANCELLED, RESP_ERROR, RESP_CLOSED = 1, 2, 3, 4, 5
_REQUEST_HEADER = struct.Struct("<IIIIIII")
_RESPONSE_HEADER = struct.Struct("<IIIIIIII")
_FLAG_MAP = (
    ("--n-gpu-layers", "gpu_layers"), ("--context", "context"), ("--threads", "threads"), ("--seed", "seed"),
    ("--max-tokens", "max_tokens"), ("--top-k", "top_k"), ("--top-p", "top_p"), ("--min-p", "min_p"),
    ("--temperature", "temperature"), ("--repeat-penalty", "repeat_penalty"), ("--cfg-weight", "cfg_weight"),
    ("--exaggeration", "exaggeration"), ("--cfm-steps", "cfm_steps"), ("--fastconv", "fastconv"),
)


@dataclass(frozen=True)
class TTSProfile:
    gpu_layers: int = 99
    context: int = 2048
    threads: int = 4
    fastconv: int = 1
    seed: int = 42
    max_tokens: int = 1000
    top_k: int = 1000
    top_p: float = 0.95
    min_p: float = 0.05
    temperature: float = 0.8
    repeat_penalty: float = 1.2
    cfm_steps: int = 2
    cfg_weight: float = 0.5
    exaggeration: float = 0.5

    @classmethod
    def from_dict(cls, d: dict) -> "TTSProfile":
        return cls(**{k: d[k] for k in d if k in cls.__dataclass_fields__})

    def cmd_args(self) -> list[str]:
        return [x for flag, key in _FLAG_MAP for x in (flag, str(getattr(self, key)))]


class WireProtocol:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.send_lock = threading.Lock()
        self.buf = bytearray()

    def send(self, kind: int, epoch: int = 0, response_id: int = 0, piece_id: int = 0, text: str = "") -> None:
        raw = text.encode("utf-8")
        with self.send_lock:
            self.sock.sendall(_REQUEST_HEADER.pack(PROTOCOL_MAGIC, PROTOCOL_VERSION, kind, epoch, response_id, piece_id, len(raw)) + raw)

    def recv_exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(min(n - len(self.buf), 1 << 20))
            if not chunk:
                raise RuntimeError("unexpected TTS socket EOF")
            self.buf.extend(chunk)
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def recv_frame(self) -> tuple[int, int, int, int, int, bytes]:
        header = self.recv_exact(_RESPONSE_HEADER.size)
        magic, version, kind, epoch, response_id, piece_id, chunk_id, length = _RESPONSE_HEADER.unpack(header)
        if magic != PROTOCOL_MAGIC or version != PROTOCOL_VERSION:
            raise RuntimeError("unsupported TTS response protocol")
        payload = self.recv_exact(length) if length else b""
        return kind, epoch, response_id, piece_id, chunk_id, payload


class Chatterbox:
    def __init__(self) -> None:
        self.proto: WireProtocol | None = None

    def open(self) -> None:
        sock = socket.create_connection(("127.0.0.1", PORTS["chatterbox"]), timeout=3600)
        self.proto = WireProtocol(sock)

    def request_close(self) -> None:
        assert self.proto is not None
        self.proto.send(REQ_CLOSE)

    def disconnect(self) -> None:
        proto, self.proto = self.proto, None
        if proto is not None and proto.sock is not None:
            try:
                proto.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            proto.sock.close()


def _port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _build_resident_cmd(paths: Paths, family: str, language: str) -> list[str]:
    if family != "nano":
        raise RuntimeError("TTS-only mode supports nano family only")
    if language.strip().lower() != "en":
        raise RuntimeError("TTS-only mode supports English only")
    tts = find_exe(RUNTIMES / "tts", "chatterbox-server.exe")
    if not tts:
        raise RuntimeError("chatterbox-server.exe missing; run python main.py install")
    profile = TTSProfile.from_dict(TTS_PROFILES[family])
    t3_file, codec_file = TTS_MODELS[family]
    return [
        str(tts), "--run-id", paths.journal.run_id, "--family", family,
        "--model", str(paths.models_dir / t3_file),
        "--s3gen-gguf", str(paths.models_dir / codec_file),
        "--reference", str(voice_wav(paths.data_dir)),
        "--language", language, "--port", str(PORTS["chatterbox"]),
        *profile.cmd_args(),
    ]


class Residents:
    def __init__(self, paths: Paths) -> None:
        self.paths, self.journal = paths, paths.journal
        self.procs: dict[str, subprocess.Popen] = {}
        self.readers: dict[str, threading.Thread] = {}
        self.chatterbox = Chatterbox()
        self.chatterbox_closed = threading.Event()

    def _start_resident(self, name: str, cmd: list[str], cwd: Path, timeout: float) -> None:
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
        ready, deadline = threading.Event(), time.monotonic() + timeout
        log.parent.mkdir(parents=True, exist_ok=True)
        reader = self.readers[name] = threading.Thread(target=self._forward_stdout,
            args=(proc, log, ready), daemon=True, name=f"resident-{name}")
        reader.start()
        while not ready.is_set():
            if time.monotonic() >= deadline:
                raise RuntimeError(f"{name} did not become ready")
            ready.wait(0.1)
            if proc.poll() is not None:
                raise RuntimeError(f"{name} exited before ready pid={proc.pid} exit={proc.returncode}")
        self.journal.emit("resident", "ready", name=name, pid=proc.pid)

    @staticmethod
    def _forward_stdout(proc: subprocess.Popen, log: Path, ready: threading.Event) -> None:
        marker = b"server.ready"
        assert proc.stdout is not None
        with log.open("wb", buffering=0) as out, proc.stdout:
            for raw in proc.stdout:
                out.write(raw)
                if marker and marker in raw:
                    ready.set()

    def boot(self, family: str = "nano", language: str = "en") -> None:
        if occupied := [f"{n}:{p}" for n, p in PORTS.items() if _port_listening(p)]:
            raise RuntimeError(f"Trident ports already occupied: {', '.join(occupied)}")
        cmd = _build_resident_cmd(self.paths, family, language)
        knobs = TTS_PROFILES[family]
        t3_file, codec_file = TTS_MODELS[family]
        self.journal.emit("runtime", "boot.start", command=self.paths.command, family=family, language=language,
                          voice=self.paths.voice, t3=t3_file, codec=codec_file,
                          chatterbox_sha=git_identity(CHATTERBOX).get("sha") or "", knobs=knobs)
        self._start_resident("chatterbox", cmd, Path(cmd[0]).parent, 300)
        self.chatterbox_client()
        self.journal.emit("runtime", "boot.ready", command=self.paths.command, family=family, language=language)

    def chatterbox_client(self) -> Chatterbox:
        if self.chatterbox.proto is None and not self.chatterbox_closed.is_set():
            self.chatterbox.open()
        return self.chatterbox

    def close_chatterbox(self) -> None:
        if self.chatterbox_closed.is_set():
            return
        client = self.chatterbox_client()
        client.request_close()
        assert client.proto is not None and client.proto.sock is not None
        client.proto.sock.settimeout(10)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            kind, *_ = client.proto.recv_frame()
            if kind == RESP_CLOSED:
                self.chatterbox_closed.set()
                break
        if not self.chatterbox_closed.is_set():
            raise RuntimeError("native close handshake timed out")
        self.journal.emit("resident", "protocol.closed", name="chatterbox", protocol_version=PROTOCOL_VERSION)

    def _reap(self, name: str, proc: subprocess.Popen, failures: list[str]) -> None:
        self.journal.emit("resident", "stop", name=name, pid=proc.pid, running=proc.poll() is None)
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
        code = proc.wait()
        self.journal.emit("resident", "stopped", name=name, pid=proc.pid, exit_code=code, clean=code == 0)
        if code != 0:
            failures.append(f"{name}: exit {code}")

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
        self.chatterbox.disconnect()
        self.procs.clear()
        self.readers.clear()
        if listening := [n for n, p in PORTS.items() if _port_listening(p)]:
            failures.append(f"ports survived: {', '.join(listening)}")
        if failures:
            raise RuntimeError("resident shutdown incomplete: " + "; ".join(failures))
        self.journal.emit("resident", "drained", ports=list(PORTS.values()))
