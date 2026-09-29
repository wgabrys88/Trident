"""Text seat between Iris and the brain PC.

Signals are status, work, and say. The brain PC does not send audio.
Iris claims the file, acts, and writes a signal back only when the act fails.
"""

import os
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KINDS = ("status", "work", "say")
Signal = namedtuple("Signal", "kind text")
STATUS_PATH = ROOT / "iris_status.txt"
TEXT_CHARS = 2000


def die(message):
    import sys

    print(message, file=sys.stderr)
    raise SystemExit(2)


def physical_lines(raw):
    return (raw or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")


def clip(text):
    flat = " ".join((text or "").split())
    if len(flat) > TEXT_CHARS:
        flat = flat[:TEXT_CHARS].rstrip()
    return flat


def parse_signals(raw):
    text = raw or ""
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = physical_lines(text)
    signals = []
    saw = False
    index = 0
    while index < len(lines):
        stripped = lines[index].rstrip(" \t")
        if stripped.endswith("<<") and stripped[:-2].strip() in KINDS:
            saw = True
            kind = stripped[:-2].strip()
            index += 1
            buf = []
            while index < len(lines) and lines[index] != "<<":
                buf.append(lines[index])
                index += 1
            if index < len(lines) and lines[index] == "<<":
                index += 1
            body = clip("\n".join(buf))
            if body:
                signals.append(Signal(kind, body))
            continue
        index += 1
    if saw:
        return signals
    body = clip(text)
    if body:
        return [Signal("say", body)]
    return []


def render(signals):
    parts = []
    for sig in signals:
        parts.append(sig.kind + " <<\n" + sig.text.strip() + "\n<<\n")
    return "".join(parts)


def seat_path(override=None):
    if override:
        return Path(override)
    env = os.environ.get("TRIDENT_IRIS_SEAT", "").strip()
    if env:
        return Path(env)
    return ROOT / "iris_seat.txt"


def outbox_path(override=None):
    if override:
        return Path(override)
    env = os.environ.get("TRIDENT_IRIS_OUTBOX", "").strip()
    if env:
        return Path(env)
    return ROOT / "iris_outbox.txt"


def store(path, signals):
    path = Path(path)
    if not signals:
        try:
            if path.is_file():
                path.unlink()
        except OSError as exc:
            die("cannot clear " + path.name + ": " + str(exc))
        return
    body = render(signals)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(body.encode("utf-8"))
        tmp.replace(path)
    except OSError as exc:
        die("cannot write " + path.name + ": " + str(exc))


def claim(path):
    path = Path(path)
    if not path.is_file():
        return []
    held = path.with_name(path.name + ".claim")
    try:
        if held.is_file():
            held.unlink()
    except OSError:
        return []
    try:
        path.replace(held)
    except OSError:
        return []
    try:
        raw = held.read_text(encoding="utf-8-sig")
    except OSError:
        raw = ""
    try:
        held.unlink()
    except OSError:
        pass
    return parse_signals(raw)


def give_back(path, signals):
    if not signals:
        return
    arrived = claim(path)
    store(path, list(signals) + list(arrived))


def record_status(text):
    line = clip(text)
    if not line:
        return
    prev = ""
    if STATUS_PATH.is_file():
        try:
            prev = STATUS_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            die("cannot read " + STATUS_PATH.name + ": " + str(exc))
    if prev and not prev.endswith("\n"):
        prev += "\n"
    tmp = STATUS_PATH.with_name(STATUS_PATH.name + ".tmp")
    try:
        tmp.write_bytes((prev + line + "\n").encode("utf-8"))
        tmp.replace(STATUS_PATH)
    except OSError as exc:
        die("cannot write " + STATUS_PATH.name + ": " + str(exc))
