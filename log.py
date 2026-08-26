from __future__ import annotations

import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from config import HARDWARE_PROFILE, Paths

_lock = threading.Lock()
_state = threading.local()


def set_run_log(path: Path | None) -> Path | None:
    _state.run_log = path
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def clear_run_log(path: Path | None = None) -> None:
    if path is None or getattr(_state, "run_log", None) == path:
        _state.run_log = None


def note(message: str) -> None:
    path = getattr(_state, "run_log", None)
    if path is None:
        return
    line = f"ts={datetime.now().astimezone().isoformat(timespec='milliseconds')} {message}\n"
    with _lock, path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_text(text, encoding="utf-8", newline="\n")
    os.replace(partial, path)


def start_run(command: str, models_dir=None, data_dir=None) -> Paths:
    paths = Paths(models_dir, data_dir, command)
    set_run_log(paths.log)
    note(f"component=pipeline event=start command={command} hardware={HARDWARE_PROFILE}")
    return paths


def finish(paths: Paths, outcome: str = "ok") -> None:
    note(f"component=pipeline event=finish outcome={outcome}")
    clear_run_log(paths.log)


def write_meta(paths: Paths, **rows) -> None:
    write_text(paths.meta, "".join(f"{key}={value}\n" for key, value in rows.items()))


def run(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    env = dict(env or {})
    env.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    env.setdefault("TQDM_DISABLE", "1")
    result = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        tail = "\n".join((result.stdout or b"").decode("utf-8", "replace").replace("\r\n", "\n").strip().splitlines()[-20:])
        raise RuntimeError(f"command {Path(command[0]).name} returned non-zero exit status {result.returncode}" + (f"\n{tail}" if tail else ""))
