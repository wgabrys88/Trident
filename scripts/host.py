# Trident resident loop: ear text or --text -> Gemma (tools) -> chatterbox.
# Run from repo root: .venv\Scripts\python.exe scripts\host.py --text "Add 17 and 4."

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from trident_gemma import converse
from trident_runtime import ear_transcribe, mouth_speak, start_resident, stop_resident, unload_all


def log(tag, text):
    line = tag + " " + text
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", help="User utterance (skips ear)")
    ap.add_argument("--audio", type=Path, help="Audio file for ear.exe")
    args = ap.parse_args()
    if bool(args.text) == bool(args.audio):
        raise SystemExit("pass exactly one of --text or --audio")
    unload_all()
    user = args.text.strip() if args.text else ear_transcribe(args.audio.resolve())
    log("USER", user)
    gemma = start_resident("gemma-brain.exe", "gemma")
    mouth = start_resident("chatterbox.exe", "chatterbox")
    try:
        reply = converse(gemma, user)
        log("REPLY", reply)
        wav = mouth_speak(mouth, reply)
        log("WAV", str(wav))
    finally:
        stop_resident("chatterbox", mouth)
        stop_resident("gemma", gemma)


if __name__ == "__main__":
    main()
