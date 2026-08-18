from __future__ import annotations
import contextlib, contextvars, datetime as dt, itertools, json, math, os, threading, time, uuid
from pathlib import Path
from typing import Any, Iterator

FILE = Path(__file__).with_name("trident.log.jsonl")
RUN_ID = "run-" + uuid.uuid4().hex
LOCK = threading.RLock()
SEQ = itertools.count(1)
CTX: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("trident_log_context", default={})
IDS = {"trace_id", "turn_id", "http_id", "job_id", "config_id", "session_id", "request_id", "lane", "client_id"}
SECRETS = {"authorization", "cookie", "password", "secret", "api_key", "apikey", "access_token", "refresh_token"}

def run_id() -> str: return RUN_ID
def new_id(kind: str) -> str: return f"{''.join(c if c.isalnum() else '-' for c in kind.lower()).strip('-') or 'id'}-{uuid.uuid4().hex}"

def _clean(value: Any, key: str = "", depth: int = 0) -> Any:
    if key.lower() in SECRETS: return "[redacted]"
    if depth > 5: return "[depth-limit]"
    if value is None or isinstance(value, (bool, int)): return value
    if isinstance(value, float): return value if math.isfinite(value) else str(value)
    if isinstance(value, Path): return str(value)
    if isinstance(value, bytes): return {"bytes": len(value)}
    if isinstance(value, str): return value if len(value) <= 8192 else value[:8192] + "...[truncated]"
    if isinstance(value, dict): return {str(k)[:128]: _clean(v, str(k), depth + 1) for k, v in list(value.items())[:256]}
    if isinstance(value, (list, tuple, set)): return [_clean(v, key, depth + 1) for v in list(value)[:256]]
    return _clean(str(value), key, depth + 1)

@contextlib.contextmanager
def scope(**ids: Any) -> Iterator[dict[str, str]]:
    current = {**CTX.get(), **{k: str(v) for k, v in ids.items() if k in IDS and v}}
    token = CTX.set(current)
    try: yield current
    finally: CTX.reset(token)

def record(level: str, component: str, event: str, data: dict[str, Any] | None = None, *, message: str = "", source: str = "controller", **ids: Any) -> dict[str, Any]:
    now, seq = time.time(), next(SEQ)
    identity = {**CTX.get(), **{k: str(v) for k, v in ids.items() if k in IDS and v}}
    entry = {
        "schema": "trident.event", "version": 1, "event_id": f"{RUN_ID}:{seq}", "run_id": RUN_ID, "seq": seq,
        "ts": now, "time": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(timespec="milliseconds"),
        "level": level if level in {"debug", "info", "warn", "error"} else "info", "source": source,
        "component": component, "event": event, "message": _clean(message), "pid": os.getpid(), "thread": threading.current_thread().name,
        **identity, "data": _clean(data or {}),
    }
    with LOCK:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        if FILE.exists() and FILE.stat().st_size > 20 * 1024 * 1024:
            backup = FILE.with_suffix(FILE.suffix + ".1"); backup.unlink(missing_ok=True); FILE.replace(backup)
        with FILE.open("a", encoding="ascii") as out: out.write(json.dumps(entry, separators=(",", ":"), ensure_ascii=True) + "\n")
    return entry

def ingest(component: str, payload: dict[str, Any], **fallback: Any) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    ids = {k: payload.get(k) or fallback.get(k) for k in IDS if payload.get(k) or fallback.get(k)}
    return record(str(payload.get("level") or fallback.get("level") or "info"), str(payload.get("component") or component), str(payload.get("event") or "native.event"), data, message=str(payload.get("message") or ""), source=str(payload.get("source") or fallback.get("source") or "native"), **ids)

def debug(component: str, event: str, data: dict[str, Any] | None = None, **ids: Any): return record("debug", component, event, data, **ids)
def info(component: str, event: str, data: dict[str, Any] | None = None, **ids: Any): return record("info", component, event, data, **ids)
def warn(component: str, event: str, data: dict[str, Any] | None = None, **ids: Any): return record("warn", component, event, data, **ids)
def error(component: str, event: str, data: dict[str, Any] | None = None, **ids: Any): return record("error", component, event, data, **ids)
