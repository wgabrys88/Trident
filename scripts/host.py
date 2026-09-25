# Always-on loop. Ear, Gemma, and chatterbox stay resident.
# .venv\Scripts\python.exe scripts\host.py

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from trident_gemma import converse
from trident_runtime import closed_text, mouth_speak, read_cfg, start_resident, stop_resident, unload_all

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


def main():
    spec = read_cfg().get("gemma.tools", "")
    unload_all()
    USER_PROMPT.write_text("", encoding="utf-8")
    EAR_RESPONSE.write_text("", encoding="utf-8")
    ear = start_resident("ear.exe", "ear")
    gemma = start_resident("gemma-brain.exe", "gemma")
    mouth = start_resident("chatterbox.exe", "chatterbox")
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


if __name__ == "__main__":
    main()
