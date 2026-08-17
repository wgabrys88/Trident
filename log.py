from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import itertools
import json
import math
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator


SCHEMA = "trident.event"
VERSION = 1
FILE = Path(__file__).with_name("trident.log.jsonl")
LEGACY_FILE = Path(__file__).with_name("install.log.jsonl")
LIMIT = 20 * 1024 * 1024
RUN_ID = "run-" + uuid.uuid4().hex
LOCK = threading.RLock()
SEQUENCE = itertools.count(1)
CONTEXT: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("trident_log_context", default={})
LISTENER: Callable[[dict[str, Any]], None] | None = None

IDENTIFIERS = (
    "trace_id",
    "turn_id",
    "http_id",
    "job_id",
    "config_id",
    "session_id",
    "request_id",
    "lane",
    "client_id",
)
SECRET_KEYS = ("authorization", "cookie", "password", "secret", "api_key", "apikey", "access_token", "refresh_token")
EVENT_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,95}$")


def run_id() -> str:
    return RUN_ID


def new_id(kind: str) -> str:
    prefix = re.sub(r"[^a-z0-9]+", "-", kind.lower()).strip("-") or "id"
    return f"{prefix}-{uuid.uuid4().hex}"


def _clean(value: Any, key: str = "", depth: int = 0) -> Any:
    if key.lower() in SECRET_KEYS:
        return "[redacted]"
    if depth > 6:
        return "[depth-limit]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return {"bytes": len(value)}
    if isinstance(value, str):
        return value if len(value) <= 8192 else value[:8192] + "...[truncated]"
    if isinstance(value, dict):
        items = list(value.items())[:256]
        result = {str(name)[:128]: _clean(item, str(name), depth + 1) for name, item in items}
        if len(value) > len(items):
            result["_truncated_keys"] = len(value) - len(items)
        return result
    if isinstance(value, (list, tuple, set)):
        values = list(value)
        result = [_clean(item, key, depth + 1) for item in values[:256]]
        if len(values) > len(result):
            result.append({"_truncated_items": len(values) - len(result)})
        return result
    return _clean(str(value), key, depth + 1)


def _event_name(component: str, value: str) -> tuple[str, str]:
    raw = str(value or "event").strip()
    lowered = raw.lower()
    if EVENT_RE.fullmatch(lowered):
        return (lowered if "." in lowered else f"{component}.{lowered}"), ""
    slug = re.sub(r"[^a-z0-9]+", ".", lowered).strip(".")[:48] or "output"
    return f"{component}.{slug}", raw


def set_listener(listener: Callable[[dict[str, Any]], None] | None) -> None:
    global LISTENER
    with LOCK:
        LISTENER = listener


@contextlib.contextmanager
def scope(**identifiers: Any) -> Iterator[dict[str, str]]:
    current = dict(CONTEXT.get())
    current.update({name: str(value) for name, value in identifiers.items() if name in IDENTIFIERS and value})
    token = CONTEXT.set(current)
    try:
        yield current
    finally:
        CONTEXT.reset(token)


def record(
    level: str,
    component: str,
    event: str,
    data: dict[str, Any] | None = None,
    *,
    message: str = "",
    source: str = "controller",
    **identifiers: Any,
) -> dict[str, Any]:
    component = re.sub(r"[^a-z0-9_-]+", "-", str(component).lower()).strip("-")[:48] or "system"
    event, derived_message = _event_name(component, event)
    now = time.time()
    seq = next(SEQUENCE)
    identity = dict(CONTEXT.get())
    identity.update({name: str(value) for name, value in identifiers.items() if name in IDENTIFIERS and value})
    entry: dict[str, Any] = {
        "schema": SCHEMA,
        "version": VERSION,
        "event_id": f"{RUN_ID}:{seq}",
        "run_id": RUN_ID,
        "seq": seq,
        "ts": now,
        "time": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(timespec="milliseconds"),
        "level": level if level in ("debug", "info", "warn", "error") else "info",
        "source": re.sub(r"[^a-z0-9_-]+", "-", str(source).lower()).strip("-")[:48] or "unknown",
        "component": component,
        "event": event,
        "message": _clean(message or derived_message),
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        **identity,
        "data": _clean(data or {}),
    }
    listener: Callable[[dict[str, Any]], None] | None
    with LOCK:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        if FILE.exists() and FILE.stat().st_size > LIMIT:
            backup = FILE.with_suffix(FILE.suffix + ".1")
            backup.unlink(missing_ok=True)
            FILE.replace(backup)
        with FILE.open("a", encoding="ascii") as output:
            output.write(json.dumps(entry, separators=(",", ":"), ensure_ascii=True) + "\n")
        listener = LISTENER
    if listener:
        try:
            listener(entry)
        except Exception:
            pass
    return entry


def ingest(component: str, payload: dict[str, Any], **fallback: Any) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    native = {key: value for key, value in payload.items() if key not in {
        "schema", "version", "level", "source", "component", "event", "message", "data", *IDENTIFIERS
    }}
    if native:
        data = {**native, **data}
    identifiers = {
        name: payload.get(name) or fallback.get(name)
        for name in IDENTIFIERS
        if payload.get(name) or fallback.get(name)
    }
    return record(
        str(payload.get("level") or fallback.get("level") or "info"),
        str(payload.get("component") or component),
        str(payload.get("event") or "native.event"),
        data,
        message=str(payload.get("message") or ""),
        source=str(payload.get("source") or fallback.get("source") or "native"),
        **identifiers,
    )


def read(limit: int = 200, **filters: Any) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 5000))
    if not FILE.is_file():
        return []
    allowed = {"run_id", "trace_id", "turn_id", "http_id", "job_id", "config_id", "session_id", "request_id", "lane", "client_id", "source", "component", "level", "event"}
    wanted = {key: str(value) for key, value in filters.items() if key in allowed and value not in (None, "")}
    since_seq = max(0, int(filters.get("since_seq") or 0))
    matches: list[dict[str, Any]] = []
    for raw in FILE.read_text(encoding="ascii", errors="replace").splitlines():
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if since_seq and int(entry.get("seq") or 0) <= since_seq:
            continue
        if all(str(entry.get(key, "")) == value for key, value in wanted.items()):
            matches.append(entry)
    return matches[-limit:]


def log(level: str, component: str, event: str, data: dict[str, Any] | None = None, **identifiers: Any) -> dict[str, Any]:
    return record(level, component, event, data, **identifiers)


def debug(component: str, event: str, data: dict[str, Any] | None = None, **identifiers: Any) -> dict[str, Any]:
    return record("debug", component, event, data, **identifiers)


def info(component: str, event: str, data: dict[str, Any] | None = None, **identifiers: Any) -> dict[str, Any]:
    return record("info", component, event, data, **identifiers)


def warn(component: str, event: str, data: dict[str, Any] | None = None, **identifiers: Any) -> dict[str, Any]:
    return record("warn", component, event, data, **identifiers)


def error(component: str, event: str, data: dict[str, Any] | None = None, **identifiers: Any) -> dict[str, Any]:
    return record("error", component, event, data, **identifiers)


def clear() -> None:
    with LOCK:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text("", encoding="ascii")
