from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

from config import ROOT

PATH = ROOT / "trident.log"


def note(message: str) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"ts={datetime.now().astimezone().isoformat(timespec='milliseconds')} {message}\n"
    with PATH.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)


def fail(message: str) -> None:
    note(message)
    print(message, file=sys.stderr, flush=True)


def size() -> int:
    return PATH.stat().st_size if PATH.is_file() else 0


def read_from(offset: int = 0) -> str:
    if not PATH.is_file():
        return ""
    with PATH.open("rb") as handle:
        handle.seek(max(offset, 0))
        return handle.read().decode("utf-8", "replace")


def sink():
    PATH.parent.mkdir(parents=True, exist_ok=True)
    return PATH.open("ab", buffering=0)


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
