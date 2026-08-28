from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import wave
from pathlib import Path

from config import TTS_RATE
from log import note


def ffmpeg_bin() -> Path:
    found = shutil.which("ffmpeg")
    if not found:
        raise RuntimeError("ffmpeg is not installed")
    return Path(found)


def _popen_kwargs() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


def _run_ffmpeg(args: list[str]) -> None:
    binary = ffmpeg_bin()
    command = [str(binary), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args]
    note("component=ffmpeg event=start operation=encode_wav")
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_popen_kwargs(),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ffmpeg failed").strip()
        raise RuntimeError(f"ffmpeg failed ({result.returncode}): {detail[-2000:]}")


def _replace(partial: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(partial, dest)
    return dest


def _canonical_wav(path: Path, rate: int, channels: int = 1) -> bool:
    if not path.is_file():
        return False
    try:
        with path.open("rb") as raw:
            header = raw.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
            return False
        with wave.open(str(path), "rb") as audio:
            return (
                audio.getsampwidth() == 2
                and audio.getcomptype() == "NONE"
                and audio.getnchannels() == channels
                and audio.getframerate() == rate
                and audio.getnframes() > 0
            )
    except (OSError, wave.Error):
        return False


def _fresh(src: Path, dest: Path) -> bool:
    return dest.is_file() and dest.stat().st_size > 0 and dest.stat().st_mtime_ns >= src.stat().st_mtime_ns


def encode_wav(src: Path, dest: Path, rate: int, *, reuse: bool = True) -> Path:
    src = src.expanduser().resolve()
    dest = dest.expanduser().resolve()
    if not src.is_file():
        raise RuntimeError(f"missing media: {src}")
    if src == dest and _canonical_wav(src, rate):
        return dest
    if reuse and _canonical_wav(dest, rate) and _fresh(src, dest):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    partial.unlink(missing_ok=True)
    _run_ffmpeg(
        [
            "-i", str(src), "-vn", "-ac", "1", "-ar", str(rate),
            "-c:a", "pcm_s16le", "-f", "wav", str(partial),
        ],
    )
    wav = _replace(partial, dest)
    if not _canonical_wav(wav, rate):
        raise RuntimeError(f"ffmpeg did not produce PCM16 {rate} Hz mono WAV: {wav}")
    note(f"component=ffmpeg event=complete rate={rate} channels=1 codec=pcm_s16le")
    return wav


def chatterbox_wav(src: Path, cache_dir: Path) -> Path:
    src = src.expanduser().resolve()
    if not src.is_file():
        raise RuntimeError(f"missing media: {src}")
    if _canonical_wav(src, TTS_RATE):
        return src
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(os.fsencode(str(src))).hexdigest()[:12]
    dest = cache_dir / f"{src.stem}-{digest}.wav"
    return encode_wav(src, dest, TTS_RATE, reuse=True)
