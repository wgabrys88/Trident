import hashlib
import json
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
    def __init__(self, run_dir: Path, console: bool = False) -> None:
        self.run_dir, self.run_id, self.console = Path(run_dir), Path(run_dir).name, bool(console)
        self._lock, self._sequence, self._manifest_written = threading.Lock(), 0, False
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

    def emit(self, component: str, event: str, **fields) -> None:
        if not isinstance(component, str) or not component or not isinstance(event, str) or not event:
            raise RuntimeError("journal component and event are required")
        with self._lock:
            self._sequence += 1
            ts = datetime.now().astimezone().strftime("%H:%M:%S") + f".{datetime.now().microsecond // 1000:03d}"
            extras = " ".join(f"{k}={fields[k]}" for k in fields) if fields else ""
            line = f"[{ts}] {component}.{event} {extras}".rstrip()
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
