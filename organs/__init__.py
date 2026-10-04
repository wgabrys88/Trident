import logging
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))


def path_of(section: str, key: str) -> Path:
    folder = {"brain": "models", "ears": "models"}.get(section, "")
    base = ROOT / CONFIG["paths"][folder] if folder else ROOT
    return base / CONFIG[section][key]


def state_dir() -> Path:
    folder = ROOT / CONFIG["paths"]["state"]
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def log(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(name)-8s %(message)s",
            datefmt="%H:%M:%S",
            handlers=[logging.StreamHandler(), logging.FileHandler(state_dir() / "trident.log", encoding="utf-8")],
        )
    return logger
