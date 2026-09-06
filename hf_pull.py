from __future__ import annotations
import hashlib
from pathlib import Path

from _runtime import _download

REPO = "wgabrys88/trident-gguf"


def pull(folder: str, filename: str, dst: Path, sha256: str = "") -> None:
    if dst.is_file():
        if sha256:
            with dst.open("rb") as f:
                if hashlib.file_digest(f, "sha256").hexdigest() == sha256:
                    return
        else:
            return
    _download(f"https://huggingface.co/{REPO}/resolve/main/{folder}/{filename}", dst, sha256)
