import logging
import os
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
_RUN: Path | None = None


def path_of(section: str, key: str) -> Path:
    folder = {"brain": "models", "ears": "models", "vision": "models"}.get(section, "")
    base = ROOT / CONFIG["paths"][folder] if folder else ROOT
    return base / CONFIG[section][key]


def state_dir() -> Path:
    folder = ROOT / CONFIG["paths"]["state"]
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def run_dir() -> Path:
    """Pictures, logs, and overlays for this process. A later process never opens an older folder."""
    global _RUN
    if _RUN is not None:
        return _RUN
    inherited = os.environ.get("TRIDENT_RUN", "").strip()
    if inherited:
        folder = Path(inherited)
        folder.mkdir(parents=True, exist_ok=True)
        _RUN = folder
        return folder
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
    os.environ["TRIDENT_RUN"] = str(folder)
    _RUN = folder
    return folder


def log(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(name)-8s %(message)s",
            datefmt="%H:%M:%S",
            handlers=[logging.StreamHandler(), logging.FileHandler(run_dir() / "trident.log", encoding="utf-8")],
        )
    return logger
