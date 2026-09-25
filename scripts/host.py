# Always-on loop. Ear, Gemma, and chatterbox stay resident.
#   .venv\Scripts\python.exe scripts\host.py
# One shot: .venv\Scripts\python.exe scripts\host.py --text "Add 17 and 4."

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
    if not reply:
        log("QUIET", "thought only")
        return
    log("REPLY", reply)
    mouth_speak(mouth, reply)


def residents():
    return start_resident("gemma-brain.exe", "gemma"), start_resident("chatterbox.exe", "chatterbox")


def always(spec):
    unload_all()
    USER_PROMPT.write_text("", encoding="utf-8")
    EAR_RESPONSE.write_text("", encoding="utf-8")
    ear = start_resident("ear.exe", "ear")
    gemma, mouth = residents()
    history = []
    heard_seen = ""
    typed_seen = 0.0
    try:
        log("ON", "ear.response.txt + user.prompt.txt")
        while True:
            text = closed_text(EAR_RESPONSE)
            heard = text.strip() if text else ""
            if heard and heard != heard_seen:
                heard_seen = heard
                turn(gemma, mouth, spec, history, heard)
            if USER_PROMPT.exists():
                stamp = USER_PROMPT.stat().st_mtime
                if stamp != typed_seen:
                    typed_seen = stamp
                    user = USER_PROMPT.read_text(encoding="utf-8").strip()
                    if user:
                        turn(gemma, mouth, spec, history, user)
            time.sleep(0.2)
    finally:
        stop_resident("ear", ear)
        stop_resident("chatterbox", mouth)
        stop_resident("gemma", gemma)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", help="One utterance, then exit")
    ap.add_argument("--audio", type=Path, help="One audio file via ear.exe, then exit")
    args = ap.parse_args()
    spec = require_gemma().get("gemma.tools", "")
    if not args.text and not args.audio:
        always(spec)
        return
    if bool(args.text) == bool(args.audio):
        raise SystemExit("pass only one of --text or --audio")
    unload_all()
    if args.audio:
        set_key("ear.live", "off")
    try:
        user = args.text.strip() if args.text else ear_transcribe(args.audio.resolve())
    finally:
        if args.audio:
            set_key("ear.live", "on")
    gemma, mouth = residents()
    try:
        turn(gemma, mouth, spec, [], user)
    finally:
        stop_resident("chatterbox", mouth)
        stop_resident("gemma", gemma)


if __name__ == "__main__":
    main()
