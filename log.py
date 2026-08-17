import json
import threading
import time
from pathlib import Path

FILE = Path(__file__).with_name("install.log.jsonl")
LIMIT = 10 * 1024 * 1024
LOCK = threading.Lock()


def log(level: str, component: str, msg: str, data: dict | None = None):
    with LOCK:
        if FILE.exists() and FILE.stat().st_size > LIMIT:
            backup = FILE.with_suffix(".jsonl.1")
            backup.unlink(missing_ok=True)
            FILE.replace(backup)
        entry = {"ts": time.time(), "level": level, "component": component, "msg": msg, "data": data or {}}
        with FILE.open("a", encoding="utf-8") as output:
            output.write(json.dumps(entry, separators=(",", ":")) + "\n")


def info(component: str, msg: str, data: dict | None = None): log("info", component, msg, data)
def warn(component: str, msg: str, data: dict | None = None): log("warn", component, msg, data)
def error(component: str, msg: str, data: dict | None = None): log("error", component, msg, data)


def clear():
    with LOCK:
        FILE.write_text("", encoding="utf-8")
