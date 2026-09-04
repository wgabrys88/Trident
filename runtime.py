from __future__ import annotations

import http.client
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from config import PORTS, RUNTIMES, Paths, find_exe

_READY = {"parakeet": "parakeet-server: listening on "}
_CTRL_C_HELPER = "import ctypes,sys,time\nk=ctypes.WinDLL('kernel32',use_last_error=True)\nk.FreeConsole()\nif not k.AttachConsole(int(sys.argv[1])): raise ctypes.WinError(ctypes.get_last_error())\nif not k.SetConsoleCtrlHandler(None,True): raise ctypes.WinError(ctypes.get_last_error())\nif not k.GenerateConsoleCtrlEvent(0,0): raise ctypes.WinError(ctypes.get_last_error())\ntime.sleep(0.5)\nk.FreeConsole()\n"


def _exe(folder: str, name: str) -> Path:
    if path := find_exe(RUNTIMES / folder, name): return path
    raise RuntimeError(f"{name} missing; run python main.py install")


def _shutdown(sock) -> None:
    if sock is None: return
    try: sock.shutdown(socket.SHUT_RDWR)
    except OSError: pass


def _listening_ports() -> list[str]:
    out = []
    for name, port in PORTS.items():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0: out.append(f"{name}:{port}")
    return out


class Residents:
    def __init__(self, paths: Paths) -> None:
        self.paths, self.journal, self.supervisor = paths, paths.journal, paths.supervisor
        self.procs: dict[str, subprocess.Popen] = {}
        self.readers: dict[str, threading.Thread] = {}
        self.ready: dict[str, threading.Event] = {}
        self.ready_deadlines: dict[str, float] = {}

    def _forward(self, name: str, proc: subprocess.Popen, path: Path, ready: threading.Event) -> None:
        with path.open("wb", buffering=0) as out:
            assert proc.stdout is not None
            marker = _READY.get(name, "").encode()
            for raw in proc.stdout:
                out.write(raw)
                if marker and marker in raw:
                    ready.set()

    def _start(self, name: str, cmd: list[str], cwd: Path, timeout: float) -> None:
        log = self.journal.sidecar(name)
        self.journal.emit("resident", "start", name=name, executable=str(cmd[0]), sidecar=log.name)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup)
        self.procs[name] = proc
        ready = self.ready[name] = threading.Event()
        deadline = self.ready_deadlines[name] = time.monotonic() + timeout
        self.readers[name] = self.supervisor.start(f"resident-{name}", self._forward, name, proc, log, ready)
        self.supervisor.spin(ready.is_set, deadline, f"{name} did not become ready", interval=.1, event=ready,
            abort=lambda: None if proc.poll() is None else f"{name} exited before ready pid={proc.pid} exit={proc.returncode}")
        self.journal.emit("resident", "ready", name=name, pid=proc.pid)

    def boot(self) -> None:
        if occupied := _listening_ports():
            raise RuntimeError(f"Trident ports already occupied: {', '.join(occupied)}")
        commands: list[tuple[str, Path, list[str], float]] = []
        parakeet = _exe("parakeet", "parakeet-server.exe")
        commands.append(("parakeet", parakeet.parent, [str(parakeet), "--model", str(self.paths.models_dir / "tdt-0.6b-v3-q4_k.gguf"), "--port", str(PORTS["parakeet"])], 180))
        for name, cwd, command, timeout in commands:
            self._start(name, command, cwd, timeout)
        self.journal.emit("runtime", "boot.ready", command=self.paths.command)

    def require_alive(self, name: str) -> str:
        proc = self.procs.get(name)
        if proc is None or proc.poll() is not None: raise RuntimeError(f"{name} is not running")
        return f"http://127.0.0.1:{PORTS[name]}"

    def check(self) -> None:
        self.supervisor.check()
        for name, proc in self.procs.items():
            if proc.poll() is not None:
                raise RuntimeError(f"{name} exited pid={proc.pid} exit={proc.returncode}")

    def _wait_ready(self, name: str, proc: subprocess.Popen) -> None:
        ready = self.ready.get(name)
        if ready is None or ready.is_set() or proc.poll() is not None: return
        deadline = self.ready_deadlines.get(name, time.monotonic() + 10)
        self.supervisor.spin(lambda: ready.is_set() or proc.poll() is not None, deadline,
            f"{name} did not become ready for graceful shutdown", event=ready)

    def _ctrl_c(self, name: str, proc: subprocess.Popen) -> None:
        result = subprocess.run([sys.executable, "-c", _CTRL_C_HELPER, str(proc.pid)], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
            text=True, encoding="utf-8", errors="replace", timeout=5)
        if result.returncode:
            raise RuntimeError(f"{name} CTRL_C helper failed exit={result.returncode}: {result.stdout.strip()}")

    def _reap(self, name: str, proc: subprocess.Popen, failures: list[str]) -> None:
        running = proc.poll() is None
        self.journal.emit("resident", "stop", name=name, pid=proc.pid, running=running)
        if running:
            try:
                self._wait_ready(name, proc)
                self._ctrl_c(name, proc)
                proc.wait(timeout=10)
            except (ValueError, OSError, subprocess.TimeoutExpired, RuntimeError) as error:
                failures.append(f"{name}: {error}")
                if proc.poll() is None: proc.kill(); proc.wait(timeout=5)
        if (reader := self.readers.get(name)) is not None:
            reader.join(timeout=5)
            if reader.is_alive(): failures.append(f"{name}: log reader survived")
        if not (clean := (code := proc.wait()) == 0): failures.append(f"{name}: exit {code}")
        self.journal.emit("resident", "stopped", name=name, pid=proc.pid, exit_code=code, clean=clean)

    def stop(self) -> None:
        failures: list[str] = []
        for name, proc in reversed(tuple(self.procs.items())):
            self._reap(name, proc, failures)
        self.procs.clear(); self.readers.clear(); self.ready.clear(); self.ready_deadlines.clear()
        if listening := _listening_ports(): failures.append(f"ports survived: {', '.join(listening)}")
        if failures: raise RuntimeError("resident shutdown incomplete: " + "; ".join(failures))
        self.journal.emit("resident", "drained", ports=list(PORTS.values()))


class CancelableHTTP:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: tuple[http.client.HTTPConnection, http.client.HTTPResponse | None] | None = None
        self._generation = 0

    @staticmethod
    def _interrupt(conn: http.client.HTTPConnection) -> None:
        _shutdown(conn.sock)

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
            conn.request("POST", (target.path or "/") + (f"?{target.query}" if target.query else ""), body=body, headers=headers)
            response = conn.getresponse()
            with self._lock:
                cancelled = generation != self._generation or self._active is None or self._active[0] is not conn
                if not cancelled: self._active = (conn, response)
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
