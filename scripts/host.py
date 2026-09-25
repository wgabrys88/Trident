# Always-on loop. The small CPU model decides. Gemma hears the original line or nothing.
# .venv\Scripts\python.exe scripts\host.py

import sys
import threading
import time
import winsound
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sense import Gate
from trident_gemma import converse
from trident_runtime import closed_text, mouth_speak, read_cfg, start_resident, stop_resident, unload_all
from vad import listen

EAR_RESPONSE = ROOT / "ear.response.txt"


def log(tag, text):
    line = tag + " " + text
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def main():
    cfg = read_cfg()
    spec = cfg.get("gemma.tools", "")
    limit = int(cfg.get("gemma.history-max", "4000"))
    history_path = ROOT / cfg.get("gemma.history-file", "gemma.history.txt")
    unload_all()
    EAR_RESPONSE.write_text("", encoding="utf-8")
    ear = start_resident("ear.exe", "ear")
    gemma = start_resident("gemma-brain.exe", "gemma")
    mouth = start_resident("chatterbox.exe", "chatterbox")
    gate = Gate(
        ROOT / cfg.get("sense.model", "sense.gguf"),
        int(cfg.get("sense.threads", "4")),
        int(cfg.get("sense.ctx", "512")),
        int(cfg.get("sense.n-predict", "2")),
    )
    mouth_busy = threading.Event()
    vad_stop = threading.Event()
    vad = threading.Thread(target=listen, args=(ROOT / "ear.prompt.txt", vad_stop, mouth_busy), daemon=True)
    vad.start()
    raw = history_path.read_text(encoding="utf-8") if history_path.exists() else ""
    history = ["<|turn>" + part for part in raw.split("<|turn>") if part.strip()]
    heard_stamp = 0.0
    waiting = []

    def act(name, args):
        if name == "see":
            path = cfg.get("gemma.image", "").strip()
            log("SEE", path or "none")
            return {"image": path or "none"}
        if name != "speak":
            return {"error": "unknown"}
        text = str(args.get("text", "")).strip()
        if not text:
            return {"spoken": 0}
        log("SPEAK", text)
        mouth_busy.set()
        try:
            wav = mouth_speak(mouth, text)
            winsound.PlaySound(str(wav), winsound.SND_FILENAME)
        finally:
            mouth_busy.clear()
        return {"spoken": 1}

    try:
        log("ON", "vad -> ear -> sense -> gemma")
        while True:
            if EAR_RESPONSE.exists():
                stamp = EAR_RESPONSE.stat().st_mtime
                if stamp != heard_stamp:
                    heard_stamp = stamp
                    text = closed_text(EAR_RESPONSE)
                    heard = text.strip() if text else ""
                    if heard:
                        waiting.append(heard)
            if waiting:
                heard = waiting.pop(0)
                log("HEARD", heard)
                original = gate.pass_original(heard)
                if not original:
                    log("HOLD", heard)
                else:
                    log("FORWARD", original)
                    converse(gemma, original, spec, history, limit, history_path, act)
            time.sleep(0.05 if waiting else 0.2)
    finally:
        vad_stop.set()
        vad.join(timeout=2)
        stop_resident("ear", ear)
        stop_resident("chatterbox", mouth)
        stop_resident("gemma", gemma)


if __name__ == "__main__":
    main()
