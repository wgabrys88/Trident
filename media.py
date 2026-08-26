from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import wave
from pathlib import Path

import imageio_ffmpeg
import numpy as np

from config import ASR_RATE, TTS_RATE
from log import note


_ffmpeg: Path | None = None


def ffmpeg_bin() -> Path:
    return Path(imageio_ffmpeg.get_ffmpeg_exe())


def _ffmpeg_version(path: Path) -> str:
    result = subprocess.run(
        [str(path), "-version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_popen_kwargs(),
    )
    line = (result.stdout or result.stderr).splitlines()[0] if result.returncode == 0 else ""
    return line.replace("ffmpeg version ", "", 1).split(" ", 1)[0] if line else "unknown"


def _popen_kwargs() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def ensure_ffmpeg() -> Path:
    global _ffmpeg
    if _ffmpeg is None:
        _ffmpeg = ffmpeg_bin()
        note(f"component=ffmpeg event=ready version={_ffmpeg_version(_ffmpeg)}")
    return _ffmpeg


def _run_ffmpeg(args: list[str]) -> None:
    binary = ensure_ffmpeg()
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


def parakeet_wav(src: Path, dest: Path) -> Path:
    src = src.expanduser().resolve()
    dest = dest.expanduser().resolve()
    if _canonical_wav(src, ASR_RATE):
        if src != dest:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        return dest
    return encode_wav(src, dest, ASR_RATE, reuse=False)



def parakeet_chunks(wav: Path, directory: Path, seconds: int, overlap_seconds: int):
    window = int(seconds * ASR_RATE)
    overlap = int(overlap_seconds * ASR_RATE)
    if window <= overlap or overlap < 0:
        raise RuntimeError("invalid ASR chunk policy")
    with wave.open(str(wav), "rb") as source:
        total = source.getnframes()
        if total <= window:
            yield wav, 0.0, total / ASR_RATE, True
            return
        directory.mkdir(parents=True, exist_ok=True)
        start = index = 0
        try:
            while start < total:
                end = min(start + window, total)
                chunk = directory / f"chunk-{index:04d}.wav"
                source.setpos(start)
                with wave.open(str(chunk), "wb") as out:
                    out.setnchannels(1); out.setsampwidth(2); out.setframerate(ASR_RATE)
                    out.writeframes(source.readframes(end - start))
                try:
                    yield chunk, start / ASR_RATE, (end - start) / ASR_RATE, end == total
                finally:
                    chunk.unlink(missing_ok=True)
                if end == total:
                    break
                start = end - overlap
                index += 1
        finally:
            if directory.exists():
                directory.rmdir()

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


def wav_pcm(path: Path, rate: int) -> bytes:
    with wave.open(str(path), "rb") as audio:
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2:
            raise RuntimeError(f"expected mono PCM16 WAV: {path}")
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        source_rate = audio.getframerate()
    if rate != source_rate and samples.size:
        count = max(1, round(samples.size * rate / source_rate))
        samples = np.interp(np.linspace(0, samples.size - 1, count), np.arange(samples.size), samples)
    return samples.astype(np.float32, copy=False).tobytes()
