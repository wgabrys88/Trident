from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from tts_client import TTSClient, load_variant
from tts_common import wav_duration_s

PLAYERS = ("wmplayer.exe", "Microsoft.Media.Player.exe", "Video.UI.exe", "Music.UI.exe")
VARIANTS = ("nano", "turbo", "v3")


def not_ready(name: str) -> str:
    extra = ' "<text>" <language>' if name == "v3" else ' "<text>"'
    return (
        f"{name} server is not running. From Trident run:\n"
        f'  python tts_{name}.py --analysis none --determinism-repeats 0{extra}'
    )


def player_pids() -> set[int]:
    found: set[int] = set()
    for name in PLAYERS:
        proc = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {name}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line or line.upper().startswith("INFO:"):
                continue
            if not line.startswith('"'):
                continue
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2 and parts[1].isdigit():
                found.add(int(parts[1]))
    return found


def close_new_players(before: set[int]) -> None:
    for pid in sorted(player_pids() - before):
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True)


def numbered_txt(folder: Path) -> list[Path]:
    files: list[Path] = []
    n = 1
    while True:
        path = folder / f"{n}.txt"
        if not path.is_file():
            break
        files.append(path)
        n += 1
    return files


def synthesize(client: TTSClient, text_path: Path, wav_path: Path):
    text = text_path.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"empty chunk: {text_path}")
    result = client.synthesize(text, wav_path)
    duration = wav_duration_s(wav_path)
    rtf = result.wall_s / duration if duration else float("inf")
    print(
        f"{client.variant.name} {text_path.name} -> {wav_path.name}  wall={result.wall_s:.3f}s  "
        f"audio={duration:.3f}s  rtf={rtf:.3f}",
        flush=True,
    )
    return result, duration


def play_wav(path: Path) -> set[int]:
    before = player_pids()
    os.startfile(path)
    time.sleep(0.4)
    return before


def speak_folder(name: str, folder: Path) -> int:
    chunks = numbered_txt(folder)
    if not chunks:
        raise SystemExit(f"no numbered .txt files in {folder}")
    client = TTSClient(load_variant(name))
    if not client.ready:
        raise SystemExit(not_ready(name))
    first_wav = folder / "1.wav"
    _, duration = synthesize(client, chunks[0], first_wav)
    pending = (first_wav, duration)
    for i, _text_path in enumerate(chunks):
        wav_path, duration = pending
        before = play_wav(wav_path)
        deadline = time.monotonic() + duration
        if i + 1 < len(chunks):
            next_wav = folder / f"{i + 2}.wav"
            _, next_duration = synthesize(client, chunks[i + 1], next_wav)
            pending = (next_wav, next_duration)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        close_new_players(before)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in VARIANTS:
        raise SystemExit("usage: python tts_speak.py nano|turbo|v3 CHUNKDIR")
    return speak_folder(argv[1], Path(argv[2]).expanduser().resolve())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
