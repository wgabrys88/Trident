import hashlib
import json
import shutil
import subprocess
import threading
import time
import traceback
import wave
from datetime import datetime
from pathlib import Path


def file_identity(path: Path) -> dict:
    path = Path(path)
    if not path.is_file(): return {"path": str(path), "missing": True}
    with path.open("rb") as handle:
        return {"path": str(path), "size": path.stat().st_size, "sha256": hashlib.file_digest(handle, "sha256").hexdigest()}


def git_identity(path: Path) -> dict:
    try:
        run = lambda *a: subprocess.check_output(["git", "-C", str(path), *a], text=True, stderr=subprocess.DEVNULL, timeout=15).strip()
        return {"sha": run("rev-parse", "HEAD"), "branch": run("branch", "--show-current"),
                "dirty": bool(run("status", "--porcelain", "--untracked-files=no"))}
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return {"sha": "", "branch": "", "dirty": None}


class Journal:
    _ENVELOPE = frozenset({"schema_version", "run_id", "sequence", "wall_timestamp", "monotonic_ns", "component", "event"})

    def __init__(self, run_dir: Path, console: bool = False) -> None:
        self.run_dir, self.run_id, self.console = Path(run_dir), Path(run_dir).name, bool(console)
        self._lock, self._sequence, self._native_sequence, self._manifest_written = threading.Lock(), 0, 0, False
        self._events = (self.run_dir / "events.jsonl").open("a", encoding="utf-8", newline="\n", buffering=1)
        self._transcripts: dict[str, object] = {}

    def sidecar(self, role: str, ext: str = "log") -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in role).strip(".-") or "sidecar"
        return self.run_dir / f"{safe}.{ext}"

    def wav(self, name: str, pcm: bytes, rate: int, width: int = 2) -> Path:
        path = self.run_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as out:
            out.setparams((1, width, rate, 0, "NONE", "not compressed"))
            out.writeframes(pcm)
        return path

    def resample(self, src: Path, dest: Path, rate: int) -> Path:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg is required to write an ASR-rate wav")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        result = subprocess.run(
            [ffmpeg, "-y", "-i", str(src), "-ar", str(rate), "-ac", "1", "-sample_fmt", "s16", str(dest)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
        if result.returncode:
            raise RuntimeError(f"ffmpeg resample failed: {result.stderr.decode('utf-8', errors='replace').strip()[-800:]}")
        return dest

    def _write(self, component: str, event: str, fields: dict, wall_timestamp: str | None = None,
               monotonic_ns: int | None = None) -> None:
        if not isinstance(component, str) or not component or not isinstance(event, str) or not event:
            raise RuntimeError("journal component and event are required")
        if overlap := self._ENVELOPE.intersection(fields):
            raise RuntimeError(f"journal fields replace schema envelope: {sorted(overlap)}")
        with self._lock:
            self._sequence += 1
            record = {"schema_version": 2, "run_id": self.run_id, "sequence": self._sequence,
                "wall_timestamp": wall_timestamp or datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "monotonic_ns": time.perf_counter_ns() if monotonic_ns is None else monotonic_ns,
                "component": component, "event": event, **fields}
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            self._events.write(line + "\n")
            if self.console: print(line, flush=True)

    def emit(self, component: str, event: str, **fields) -> None:
        self._write(component, event, fields)

    def ingest(self, raw: bytes) -> str:
        try:
            record = json.loads(raw.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"invalid native schema-v2 JSON: {error}") from error
        if type(record) is not dict or record.get("schema_version") != 2 or record.get("run_id") != self.run_id:
            raise RuntimeError("native journal schema or run identity mismatch")
        if record.get("component") != "chatterbox" or not isinstance(record.get("event"), str) or not record["event"]:
            raise RuntimeError("native journal component or event is invalid")
        if type(record.get("sequence")) is not int or type(record.get("monotonic_ns")) is not int or not isinstance(record.get("wall_timestamp"), str):
            raise RuntimeError("native journal envelope is invalid")
        if record["sequence"] != self._native_sequence + 1:
            raise RuntimeError(f"native journal sequence discontinuity: expected {self._native_sequence + 1}, got {record['sequence']}")
        self._native_sequence = record["sequence"]
        fields = {key: value for key, value in record.items() if key not in self._ENVELOPE}
        self._write("chatterbox", record["event"], fields, record["wall_timestamp"], record["monotonic_ns"])
        return record["event"]

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
            handle = self._transcripts.get(role) or self.sidecar(role, "txt").open("a", encoding="utf-8", newline="\n", buffering=1)
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
        self._failed, self._threads = threading.Event(), []

    def start(self, name: str, target, *args, daemon: bool = False, **kwargs) -> threading.Thread:
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

    def spin(self, done, deadline: float, err: str, interval: float = .1, event: threading.Event | None = None, tick=None, abort=None) -> None:
        while not done():
            (tick or self.check)()
            if abort and (msg := abort()): raise RuntimeError(msg)
            if time.monotonic() >= deadline: raise RuntimeError(err)
            (event or self._failed).wait(min(interval, max(0.0, deadline - time.monotonic())))

    def join(self, timeout: float | None = None) -> None:
        deadline = None if timeout is None else time.monotonic() + timeout
        for thread in self._threads:
            if thread.is_alive(): thread.join(None if deadline is None else max(0.0, deadline - time.monotonic()))
        if survivors := [t.name for t in self._threads if t.is_alive()]:
            raise RuntimeError(f"worker survivors after shutdown: {', '.join(survivors)}")
        self.check()


def join_or_fail(thread: threading.Thread | None, role: str, timeout: float = 5.0) -> None:
    if thread is None or not thread.is_alive(): return
    thread.join(timeout)
    if thread.is_alive(): raise RuntimeError(f"{role} worker survived shutdown")


def finish_cleanup(journal_owner, primary, actions) -> None:
    failures: list[tuple[BaseException, object]] = []
    for role, action in actions:
        try: action()
        except BaseException as error:
            failures.append((error, error.__traceback__)); journal_owner.journal.failure(f"cleanup.{role}", error)
    if primary is not None: raise primary[0].with_traceback(primary[1])
    if failures: raise failures[0][0].with_traceback(failures[0][1])
