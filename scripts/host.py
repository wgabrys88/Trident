# Always-on loop. Ear, Gemma, and chatterbox stay resident.
#   .venv\Scripts\python.exe scripts\host.py --live
#   .venv\Scripts\python.exe scripts\host.py --text "Add 17 and 4."
#   .venv\Scripts\python.exe scripts\host.py --serve

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from trident_gemma import converse
from trident_runtime import (
    closed_text,
    ear_transcribe,
    mouth_speak,
    require_gemma,
    set_key,
    start_resident,
    stop_resident,
    unload_all,
)

USER_PROMPT = ROOT / "user.prompt.txt"
EAR_RESPONSE = ROOT / "ear.response.txt"


def log(tag, text):
    line = tag + " " + text
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def turn(gemma, mouth, spec, history, user):
    log("USER", user)
    reply = converse(gemma, user, spec, history)
    log("REPLY", reply)
    mouth_speak(mouth, reply)


def residents():
    return start_resident("gemma-brain.exe", "gemma"), start_resident("chatterbox.exe", "chatterbox")


def serve(spec):
    unload_all()
    USER_PROMPT.write_text("", encoding="utf-8")
    gemma, mouth = residents()
    history = []
    seen = 0.0
    try:
        log("SERVE", str(USER_PROMPT))
        while True:
            if USER_PROMPT.exists():
                stamp = USER_PROMPT.stat().st_mtime
                if stamp != seen:
                    seen = stamp
                    user = USER_PROMPT.read_text(encoding="utf-8").strip()
                    if user:
                        turn(gemma, mouth, spec, history, user)
            time.sleep(0.2)
    finally:
        stop_resident("chatterbox", mouth)
        stop_resident("gemma", gemma)


def live(spec):
    unload_all()
    set_key("ear.live", "on")
    EAR_RESPONSE.write_text("", encoding="utf-8")
    ear = start_resident("ear.exe", "ear")
    gemma, mouth = residents()
    history = []
    seen = ""
    try:
        log("LIVE", str(EAR_RESPONSE))
        while True:
            text = closed_text(EAR_RESPONSE)
            heard = text.strip() if text else ""
            if heard and heard != seen:
                seen = heard
                turn(gemma, mouth, spec, history, heard)
            time.sleep(0.2)
    finally:
        stop_resident("ear", ear)
        stop_resident("chatterbox", mouth)
        stop_resident("gemma", gemma)
        set_key("ear.live", "off")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="Microphone via resident ear.exe")
    ap.add_argument("--serve", action="store_true", help="Watch user.prompt.txt")
    ap.add_argument("--text", help="One utterance, then exit")
    ap.add_argument("--audio", type=Path, help="One audio file via ear.exe, then exit")
    args = ap.parse_args()
    spec = require_gemma().get("gemma.tools", "")
    if args.live:
        live(spec)
        return
    if args.serve:
        serve(spec)
        return
    if bool(args.text) == bool(args.audio):
        raise SystemExit("pass --live, --serve, or exactly one of --text or --audio")
    unload_all()
    user = args.text.strip() if args.text else ear_transcribe(args.audio.resolve())
    gemma, mouth = residents()
    try:
        turn(gemma, mouth, spec, [], user)
    finally:
        stop_resident("chatterbox", mouth)
        stop_resident("gemma", gemma)


if __name__ == "__main__":
    main()
