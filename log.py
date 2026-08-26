from __future__ import annotations

import subprocess
import threading
from datetime import datetime
from pathlib import Path

_lock = threading.Lock()
_state = threading.local()


def set_run_log(path: Path | None) -> Path | None:
    _state.run_log = path
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def clear_run_log(path: Path | None = None) -> None:
    current = getattr(_state, "run_log", None)
    if path is None or current == path:
        _state.run_log = None


def note(message: str) -> None:
    path = getattr(_state, "run_log", None)
    if path is None:
        return
    line = f"ts={datetime.now().astimezone().isoformat(timespec='milliseconds')} {message}\n"
    with _lock, path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)


def run(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    env = dict(env or {})
    env.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    env.setdefault("TQDM_DISABLE", "1")
    result = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    output = result.stdout or b""
    if result.returncode != 0:
        tail = "\n".join(output.decode("utf-8", "replace").replace("\r\n", "\n").strip().splitlines()[-20:])
        raise RuntimeError(
            f"command {Path(command[0]).name} returned non-zero exit status {result.returncode}"
            + (f"\n{tail}" if tail else "")
        )
