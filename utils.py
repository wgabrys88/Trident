import shutil
import subprocess
from pathlib import Path


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg is not on PATH")
    return exe


def run(args: list[str]) -> None:
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    result = subprocess.run([ffmpeg_exe(), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
    if result.returncode:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.decode('utf-8', errors='replace').strip()[-800:]}")


def resample(src: Path, dest: Path, rate: int) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(["-y", "-i", str(src), "-ar", str(rate), "-ac", "1", "-sample_fmt", "s16", str(dest)])
    return dest


def spectrogram(src: Path, dest: Path | None = None) -> Path:
    src = Path(src)
    dest = Path(dest) if dest is not None else src.with_name(src.stem + "-spec.png")
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(["-y", "-i", str(src), "-lavfi", "showspectrumpic=s=1400x512:legend=1", str(dest)])
    return dest
