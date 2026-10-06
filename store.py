import json
import os
import tomllib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))


def encode(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    Path(path).write_text(encode(value) + "\n", encoding="utf-8")


def stamp():
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def command(parts):
    return [os.path.expandvars(str(part)) for part in parts]


class Record:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def append(self, kind, value):
        with (self.folder / "trace.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"time": stamp(), "kind": kind, "value": value}, ensure_ascii=False) + "\n")

    def trace(self, start):
        return (self.folder / "trace.jsonl").read_text(encoding="utf-8")[start:]

    def offset(self):
        return len((self.folder / "trace.jsonl").read_text(encoding="utf-8"))
