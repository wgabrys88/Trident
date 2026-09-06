from __future__ import annotations
import sys, time
from pathlib import Path

LOG = Path(__file__).resolve().parent / ".runtime-logs/main.log"
LOG.parent.mkdir(exist_ok=True)


def now() -> str:
    return time.strftime("%H:%M:%S")


def emit(msg: str) -> None:
    line = f"[{now()}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


class span:
    def __init__(self, name: str) -> None:
        self.name = name
        self.t0 = 0.0

    def __enter__(self) -> span:
        self.t0 = time.perf_counter()
        emit(f"{self.name} start")
        return self

    def __exit__(self, *_) -> None:
        d = time.perf_counter() - self.t0
        emit(f"{self.name} end in {d:.2f}s")
        emit(f"script {self.name} took {d:.2f} seconds")
