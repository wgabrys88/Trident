from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

from config import ROOT
from log import note, run as run_logged


PARAKEET_RATE = 16000
CHATTERBOX_RATE = 24000
PLAYBACK_RATE = 44100
PLAYBACK_SIZE = "640x360"
PLAYBACK_FPS = 30

_ffmpeg: Path | None = None


def _winget_ffmpeg() -> Path | None:
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    matches = sorted(root.glob("Gyan.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe"), reverse=True)
    return next((path for path in matches if path.is_file()), None)


def ffmpeg_bin() -> Path:
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    packed = _winget_ffmpeg()
    if packed:
        return packed
    raise RuntimeError("ffmpeg is not installed")


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
    extra: dict = {}
    if os.name == "nt":
        extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return extra


def ensure_ffmpeg() -> Path:
    global _ffmpeg
    if _ffmpeg is not None:
        return _ffmpeg
    try:
        path = ffmpeg_bin()
    except RuntimeError:
        path = None
    if path is None:
        winget = shutil.which("winget")
        if not winget:
            raise RuntimeError(
                "ffmpeg is missing and winget is unavailable; install Gyan.FFmpeg globally"
            )
        note("component=ffmpeg event=install id=Gyan.FFmpeg source=winget")
        run_logged(
            [
                winget, "install", "-e", "--id", "Gyan.FFmpeg", "--source", "winget",
                "--accept-package-agreements", "--accept-source-agreements",
                "--disable-interactivity",
            ],
            ROOT,
            os.environ.copy(),
        )
        path = ffmpeg_bin()
    _ffmpeg = path
    note(f"component=ffmpeg event=ready path={path} version={_ffmpeg_version(path)}")
    return path


def _run_ffmpeg(args: list[str]) -> None:
    binary = ensure_ffmpeg()
    command = [str(binary), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args]
    note("component=ffmpeg event=start command=" + " ".join(command))
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
    note(f"component=ffmpeg event=wav path={wav} rate={rate} channels=1 codec=pcm_s16le")
    return wav


def parakeet_wav(src: Path, dest: Path) -> Path:
    src = src.expanduser().resolve()
    dest = dest.expanduser().resolve()
    if _canonical_wav(src, PARAKEET_RATE):
        if src != dest:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        return dest
    return encode_wav(src, dest, PARAKEET_RATE, reuse=False)


def chatterbox_wav(src: Path, cache_dir: Path) -> Path:
    src = src.expanduser().resolve()
    if not src.is_file():
        raise RuntimeError(f"missing media: {src}")
    if _canonical_wav(src, CHATTERBOX_RATE):
        return src
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(os.fsencode(str(src))).hexdigest()[:12]
    dest = cache_dir / f"{src.stem}-{digest}.wav"
    return encode_wav(src, dest, CHATTERBOX_RATE, reuse=True)


def compatible_mp4(src: Path, dest: Path) -> Path:
    src = src.expanduser().resolve()
    dest = dest.expanduser().resolve()
    if not src.is_file():
        raise RuntimeError(f"missing media: {src}")
    if _fresh(src, dest) and dest.stat().st_size > 32:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    partial.unlink(missing_ok=True)
    # H.264 Baseline + AAC-LC + yuv420p + faststart is the widest MP4 profile:
    # phones, browsers, TVs, and game consoles all play it. A still frame is
    # required because many of those players reject audio-only MP4.
    _run_ffmpeg(
        [
            "-f", "lavfi", "-i", f"color=c=black:s={PLAYBACK_SIZE}:r={PLAYBACK_FPS}",
            "-i", str(src),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0",
            "-pix_fmt", "yuv420p", "-preset", "veryfast", "-tune", "stillimage",
            "-crf", "28", "-g", str(PLAYBACK_FPS), "-keyint_min", str(PLAYBACK_FPS),
            "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "128k",
            "-ac", "2", "-ar", str(PLAYBACK_RATE),
            "-shortest", "-movflags", "+faststart",
            "-brand", "mp42", "-f", "mp4", str(partial),
        ],
    )
    mp4 = _replace(partial, dest)
    note(f"component=ffmpeg event=mp4 path={mp4} video=h264-baseline audio=aac-lc faststart=1")
    return mp4


def publish_outputs(wav: Path, mp4: Path, dest: Path | None) -> None:
    if dest is None:
        return
    dest = dest.expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.suffix.lower() == ".mp4":
        shutil.copy2(mp4, dest)
        shutil.copy2(wav, dest.with_suffix(".wav"))
        return
    shutil.copy2(wav, dest)
    shutil.copy2(mp4, dest.with_suffix(".mp4"))


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "usage: python media.py {parakeet|chatterbox|mp4} INPUT [OUTPUT]",
            file=sys.stderr,
        )
        return 2
    kind = sys.argv[1].lower()
    src = Path(sys.argv[2])
    dest = Path(sys.argv[3]).expanduser().resolve() if len(sys.argv) > 3 else None
    try:
        if kind == "parakeet":
            out = parakeet_wav(src, dest or src.with_name(src.stem + ".parakeet.wav"))
        elif kind == "chatterbox":
            out = chatterbox_wav(src, dest.parent if dest else src.parent)
            if dest and out != dest:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(out, dest)
                out = dest
        elif kind == "mp4":
            out = compatible_mp4(src, dest or src.with_suffix(".mp4"))
        else:
            raise RuntimeError(f"unknown media action: {kind}")
        print(out)
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
