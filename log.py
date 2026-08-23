from __future__ import annotations

import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from config import ROOT

LIMIT_BYTES = 6_000_000
PATH = ROOT / "trident.log"
_PATTERN = re.compile(r"trident(\d*)\.log")

_lock = threading.Lock()
_run_log: Path | None = None
_run_mark: tuple[str, int] | None = None


def _number(path: Path) -> int:
    match = _PATTERN.fullmatch(path.name)
    return int(match.group(1) or 1) if match else 0


def chunks() -> list[Path]:
    found = [p for p in ROOT.glob("trident*.log") if _PATTERN.fullmatch(p.name)]
    return sorted(found, key=_number)


def active() -> Path:
    found = chunks()
    if not found:
        return PATH
    last = found[-1]
    try:
        if last.stat().st_size < LIMIT_BYTES:
            return last
    except OSError:
        return last
    return ROOT / f"trident{_number(last) + 1}.log"


def set_run_log(path: Path | None) -> tuple[str, int] | None:
    global _run_log, _run_mark
    with _lock:
        _run_log = path
        _run_mark = None
        if path is None:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        current = active()
        offset = current.stat().st_size if current.exists() else 0
        _run_mark = (current.name, offset)
        return _run_mark


def _write(line: str) -> None:
    target = active()
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
    if _run_log is not None:
        with _run_log.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)


def note(message: str) -> None:
    line = f"ts={datetime.now().astimezone().isoformat(timespec='milliseconds')} {message}\n"
    with _lock:
        _write(line)


def fail(message: str) -> None:
    note(message)
    print(message, file=sys.stderr, flush=True)


def open_sink() -> tuple[str, int, object]:
    with _lock:
        target = active()
        offset = target.stat().st_size if target.exists() else 0
        return target.name, offset, target.open("ab", buffering=0)


def sink():
    return open_sink()[2]


def _chain(offset: int, start: str | None):
    begun = start is None
    for path in chunks():
        if not begun:
            if path.name != start:
                continue
            begun = True
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if path.name == start and offset:
            data = data[offset:] if len(data) > offset else b""
        yield data


def end_run(dest: Path) -> bool:
    global _run_mark
    with _lock:
        mark = _run_mark
        _run_mark = None
    if mark is None:
        return False
    name, offset = mark
    payload = b"".join(_chain(offset, name))
    dest.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# server-side slice from {name}@{offset}; client events above are also in this run\n"
    ).encode("ascii")
    dest.write_bytes(header + payload)
    note(f"component=pipeline event=log_slice run_dir={dest.parent.name} source={name}:{offset} bytes={len(payload)}")
    return True


def run(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    env = dict(env or {})
    env.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    env.setdefault("TQDM_DISABLE", "1")
    result = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    output = result.stdout or b""
    with sink() as out:
        out.write(output)
    if result.returncode != 0:
        tail = "\n".join(output.decode("utf-8", "replace").replace("\r\n", "\n").strip().splitlines()[-20:])
        raise RuntimeError(
            f"command {command} returned non-zero exit status {result.returncode}"
            + (f"\n{tail}" if tail else "")
        )
