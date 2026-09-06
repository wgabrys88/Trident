from __future__ import annotations
import hashlib
from pathlib import Path

try:
    from huggingface_hub import hf_hub_download
    _HF_AVAILABLE = True
except ImportError:
    _HF_AVAILABLE = False

REPO = "wgabrys88/trident-gguf"


def pull(folder: str, filename: str, dst: Path, sha256: str = "") -> None:
    if dst.is_file():
        if sha256:
            with dst.open("rb") as f:
                if hashlib.file_digest(f, "sha256").hexdigest() == sha256:
                    return
        else:
            return
    if not _HF_AVAILABLE:
        raise RuntimeError("huggingface-hub not installed; run: pip install huggingface-hub")
    dst.parent.mkdir(parents=True, exist_ok=True)
    path_in_repo = f"{folder}/{filename}"
    local_path = hf_hub_download(
        repo_id=REPO,
        filename=path_in_repo,
        local_dir=str(dst.parent),
        local_dir_use_symlinks=False,
    )
    Path(local_path).replace(dst)
