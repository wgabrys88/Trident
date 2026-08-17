import json
import threading
import time
from pathlib import Path
FILE = Path(__file__).with_name("install.log.jsonl")
LIMIT = 10 * 1024 * 1024
LOCK = threading.Lock()
def log(level: str, component: str, msg: str, data: dict | None = None):
    entry = {"ts": time.time(), "level": level, "component": component, "msg": str(msg), "data": data or {}}
    with LOCK:
        if FILE.exists() and FILE.stat().st_size > LIMIT:
            backup = FILE.with_suffix(".jsonl.1")
            backup.unlink(missing_ok=True)
            FILE.replace(backup)
        with FILE.open("a", encoding="ascii") as output:
            output.write(json.dumps(entry, separators=(",", ":"), ensure_ascii=True) + "\n")
def info(component, msg, data=None): log("info", component, msg, data)
def warn(component, msg, data=None): log("warn", component, msg, data)
def error(component, msg, data=None): log("error", component, msg, data)
def clear():
    with LOCK: FILE.write_text("", encoding="ascii")
