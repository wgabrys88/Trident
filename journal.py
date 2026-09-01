from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable


def file_identity(path: Path) -> dict:
    path = Path(path)
    if not path.is_file(): return {"path": str(path), "missing": True}
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 << 20), b""): digest.update(block)
    return {"path": str(path), "size": path.stat().st_size, "sha256": digest.hexdigest()}


def git_identity(path: Path) -> dict:
    path = Path(path)
    try:
        run = lambda *a: subprocess.check_output(["git", "-C", str(path), *a], text=True, stderr=subprocess.DEVNULL, timeout=15).strip()
        sha = run("rev-parse", "HEAD")
        dirty = bool(run("status", "--porcelain", "--untracked-files=no"))
        return {"sha": sha, "branch": run("branch", "--show-current"), "dirty": dirty}
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return {"sha": "", "branch": "", "dirty": None}


class Journal:
    def __init__(self, run_dir: Path, console: bool = False) -> None:
        self.run_dir = Path(run_dir)
        self.run_id = self.run_dir.name
        self.console = bool(console)
        self._lock = threading.Lock()
        self._sequence = 0
        self._events = (self.run_dir / "events.jsonl").open("a", encoding="utf-8", newline="\n", buffering=1)
        self._transcripts: dict[str, object] = {}
        self._manifest_written = False

    def sidecar(self, role: str, ext: str = "log") -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in role).strip(".-") or "sidecar"
        return self.run_dir / f"{safe}.{ext}"

    def emit(self, component: str, event: str, **fields) -> None:
        now = datetime.now().astimezone().isoformat(timespec="milliseconds")
        with self._lock:
            self._sequence += 1
            line = json.dumps({"schema_version": 2, "run_id": self.run_id, "sequence": self._sequence,
                "wall_timestamp": now, "monotonic_ns": time.perf_counter_ns(), "component": component, "event": event, **fields},
                ensure_ascii=False, separators=(",", ":"), default=str)
            self._events.write(line + "\n")
            if self.console: print(line, flush=True)

    def write_manifest(self, manifest: dict) -> None:
        with self._lock:
            if self._manifest_written or (self.run_dir / "run.json").exists():
                raise RuntimeError("run manifest is immutable")
            (self.run_dir / "run.json").write_text(json.dumps({"schema_version": 2, "run_id": self.run_id, **manifest},
                ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
            self._manifest_written = True

    def transcript(self, role: str, text: str) -> None:
        if not text: return
        with self._lock:
            handle = self._transcripts.get(role)
            if handle is None:
                handle = self.sidecar(role, "txt").open("a", encoding="utf-8", newline="\n", buffering=1)
                self._transcripts[role] = handle
            handle.write(text.rstrip() + "\n")

    def failure(self, component: str, error: BaseException) -> None:
        self.emit(component, "failed", type=type(error).__name__, error=str(error))
        with self.sidecar("failure", "txt").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("".join(traceback.format_exception(type(error), error, error.__traceback__)))

    def close(self) -> None:
        with self._lock:
            for handle in self._transcripts.values(): handle.close()
            self._transcripts.clear()
            if not self._events.closed:
                self._events.flush(); self._events.close()


class WorkerSupervisor:
    def __init__(self, journal: Journal) -> None:
        self.journal = journal
        self._failure_lock = threading.Lock()
        self._failure: tuple[BaseException, object] | None = None
        self._failed = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self, name: str, target: Callable, *args, daemon: bool = False, **kwargs) -> threading.Thread:
        def guarded() -> None:
            try: target(*args, **kwargs)
            except BaseException as error:
                with self._failure_lock:
                    if self._failure is None:
                        self._failure = (error, error.__traceback__); self._failed.set()
                self.journal.failure(name, error)
        thread = threading.Thread(target=guarded, name=name, daemon=daemon)
        self._threads.append(thread); thread.start(); return thread

    def check(self) -> None:
        if self._failed.is_set():
            with self._failure_lock: failure = self._failure
            if failure is not None: raise failure[0].with_traceback(failure[1])

    def wait(self, seconds: float) -> None:
        self._failed.wait(seconds); self.check()

    def join(self, timeout: float | None = None) -> None:
        deadline = None if timeout is None else time.monotonic() + timeout
        for thread in self._threads:
            if thread.is_alive():
                thread.join(None if deadline is None else max(0.0, deadline - time.monotonic()))
        survivors = [t.name for t in self._threads if t.is_alive()]
        if survivors: raise RuntimeError(f"worker survivors after shutdown: {', '.join(survivors)}")
        self.check()
