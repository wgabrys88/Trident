import asyncio
import json
import os
import tomllib
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("TRIDENT_ROOT", Path(__file__).resolve().parent)).resolve()
CONFIG = tomllib.loads(Path(os.environ.get("TRIDENT_CONFIG", ROOT / "config.toml")).read_text(encoding="utf-8"))
RUNTIME = {"runs", "artifacts", "__pycache__", ".git"}


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def read(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(encode(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def stamp():
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def command(parts):
    return [os.path.expandvars(str(part)) for part in parts]


async def cancel(*tasks):
    for task in tasks:
        task.cancel()
    return await asyncio.gather(*tasks, return_exceptions=True)


def source_paths():
    for folder, directories, files in os.walk(ROOT):
        directories[:] = [name for name in directories
                          if name != "__pycache__" and not (Path(folder) == ROOT and name in RUNTIME)]
        for name in files:
            path = Path(folder) / name
            if path.is_file():
                yield path


def activate(state, text, source, receipt, sequence=None):
    if not text.strip():
        return
    previous = state.get("task")
    if previous and not any(item["id"] == previous["id"] for item in state["open_work"]):
        state["open_work"].append(previous)
    state["task"] = {"id": f"{state['life']}:{receipt}", "text": text,
                     "owner": {"source": source, "receipt": receipt, "text": text}}
    if sequence is not None:
        state["owner_sequence"] = sequence
    state.update(assessment=None, history=[], receipts=[], results={}, waiting=False, shutdown=False, attention=True)


def save(folder, state):
    write(Path(folder) / "session.json", state)
    retained = {item["id"]: item for item in state["open_work"]}
    if state.get("task"):
        retained[state["task"]["id"]] = state["task"]
    write(ROOT / "runs" / "memory.json", list(retained.values()))
