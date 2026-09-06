from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--install", action="store_true", required=True)
    p.add_argument("--from-hf", action="store_true")
    a = p.parse_args()
    for script in ("brain.py", "chunk.py", "tts_nano.py", "tts_turbo.py", "tts_v3.py", "parakeet.py"):
        flags = ["--install", *(["--from-hf"] if a.from_hf and script != "chunk.py" else [])]
        subprocess.run([sys.executable, "-u", script, *flags], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "-u", "tts_nano.py", "--load"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
