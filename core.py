import json, tomllib
from datetime import datetime
from pathlib import Path
from hardware import select

ROOT = Path(__file__).resolve().parent
CONFIG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
ACCELERATOR, HARDWARE = select(CONFIG["hardware"])
STATE, MODELS, BIN = (ROOT / CONFIG["paths"][key] for key in ("state", "models", "bin"))

def timestamp():
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S_%f%z")

def encode(value):
    return json.dumps(value, ensure_ascii=False, indent=2)

class Interrupted(Exception):
    pass
