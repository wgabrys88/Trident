import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
_RUN: Path | None = None


def path_of(section: str, key: str) -> Path:
    folder = {"brain": "models", "ears": "models"}.get(section, "")
    base = ROOT / CONFIG["paths"][folder] if folder else ROOT
    return base / CONFIG[section][key]


def state_dir() -> Path:
    folder = ROOT / CONFIG["paths"]["state"]
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def run_dir() -> Path:
    global _RUN
    if _RUN is not None:
        return _RUN
    root = state_dir()
    stamp = time.strftime("run_%Y-%m-%d_%H%M")
    folder = root / stamp
    n = 2
    while True:
        try:
            folder.mkdir()
        except FileExistsError:
            folder = root / f"{stamp}-{n}"
            n += 1
            continue
        break
    _RUN = folder
    return folder
