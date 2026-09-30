import base64
import ctypes
import io
import json
import re
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "endgame-vision-chat"))
import gemma
import winapi

PROOF = ROOT / "desk.proof.txt"
MEDIA = "<__media__>"
TURNS = 12
AREA = 2600000
MESSAGE = "Reply with the single word lighthouse."
SYSTEM = (
    "You see one full-monitor screenshot. "
    "Output one JSON object and no other text. "
    "see is the words and the app you can actually see. "
    "do is the string type, key, click, wait, or done. "
    "type has text, and may also have key set to enter. "
    "key has key set to enter, ctrl-a, ctrl-l, or backspace. "
    "click has box_2d as y_min, x_min, y_max, x_max, integers 0 to 1000, top left origin. "
    "done has reply set to the ChatGPT answer you can read."
)


def next_goal(done):
    typed = any(item.startswith("type ") for item in done)
    sent = any(item == "key enter" or item.endswith(" key enter") for item in done)
    if not typed:
        return (
            "ChatGPT is on screen. "
            "do is type. "
            "text is exactly: "
            + MESSAGE
            + " Also set key to enter so the message is sent. "
            "see names the app and quotes the visible words."
        )
    if not sent:
        return "The message is in the ChatGPT field. do must be key. key must be enter. see must quote the words in that field."
    return (
        "Describe this screenshot in two sentences. "
        "Name the foreground application and quote the readable text. "
        "If a chat answer is visible, do is done and reply is only that answer."
    )


def note(text):
    row = time.strftime("%H:%M:%S") + " " + (text or "").replace("\r\n", "\n").replace("\r", "\n")
    print(row, flush=True)
    with PROOF.open("a", encoding="utf-8") as handle:
        handle.write(row + "\n")


def shot():
    winapi.init_dpi()
    sw, sh = winapi.get_screen_size()
    png, _rw, _rh = winapi.capture_screenshot_png(sw, sh)
    image = Image.open(io.BytesIO(png)).convert("RGB")
    scale = min(1.0, (AREA / float(image.width * image.height)) ** 0.5)
    wide = max(1, int(round(image.width * scale)))
    high = max(1, int(round(image.height * scale)))
    sent = image.resize((wide, high), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    sent.save(buf, format="PNG")
    data = buf.getvalue()
    return sw, sh, image.width, image.height, len(png), wide, high, data


def prompt_for(user):
    return (
        "<bos><|turn>system\n"
        + SYSTEM
        + "<turn|>\n<|turn>user\n"
        + MEDIA
        + "\n"
        + user
        + "<turn|>\n<|turn>model\n"
    )


def ask(user):
    sw, sh, cw, ch, full_n, wide, high, data = shot()
    text = prompt_for(user)
    note("prompt <<\n" + text + "<<")
    note(
        "image screen %d %d captured %d %d bytes %d sent %d %d bytes %d"
        % (sw, sh, cw, ch, full_n, wide, high, len(data))
    )
    reply = gemma.resident_generate(text, base64.b64encode(data).decode("ascii"), False)
    note("gemma <<\n" + reply + "<<")
    return reply


def flatten(obj):
    do = obj.get("do")
    if do is None and isinstance(obj.get("done"), str):
        obj["reply"] = obj["done"]
        obj["do"] = "done"
        return obj
    if isinstance(obj.get("readable_text"), str):
        obj["reply"] = obj["readable_text"]
        obj["do"] = "done"
        return obj
    if do is None and obj.get("text"):
        do = "type"
        obj["do"] = do
    if isinstance(do, str):
        obj["do"] = do.strip().lower()
        return obj
    if isinstance(do, list):
        for item in do:
            if isinstance(item, dict):
                kind = str(item.get("action") or item.get("do") or "click").strip().lower()
                merged = {"do": kind, "see": obj.get("see", "")}
                for key in ("box_2d", "text", "key", "reply"):
                    if key in item:
                        merged[key] = item[key]
                return merged
            if isinstance(item, str):
                obj["do"] = item.strip().lower()
                return obj
    if "box_2d" in obj:
        obj["do"] = "click"
        return obj
    return None


def object_from(reply):
    raw = reply or ""
    if "<channel|>" in raw:
        raw = raw.split("<channel|>")[-1]
    start = raw.find("{")
    obj = None
    if start >= 0:
        try:
            found, _end = json.JSONDecoder().raw_decode(raw[start:])
            if isinstance(found, dict):
                obj = found
        except json.JSONDecodeError:
            obj = None
    if obj is None:
        match = re.search(
            r"box_2d\s*\"?\s*[:=]\s*\[\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\]",
            raw,
        )
        if match is None:
            return None
        obj = {"do": "click", "box_2d": [float(match.group(i)) for i in range(1, 5)], "see": ""}
    return flatten(obj)


def key_input(vk, flags):
    item = winapi.INPUT()
    item.type = winapi.INPUT_KEYBOARD
    item.ii.ki = winapi.KEYBDINPUT(vk, 0, flags, 0, 0)
    return item


def press(name):
    if name == "ctrl-l":
        seq = (key_input(0x11, 0), key_input(0x4C, 0), key_input(0x4C, 2), key_input(0x11, 2))
    elif name == "ctrl-a":
        seq = (key_input(0x11, 0), key_input(0x41, 0), key_input(0x41, 2), key_input(0x11, 2))
    else:
        vk = {"enter": 0x0D, "tab": 0x09, "escape": 0x1B, "backspace": 0x08}[name]
        seq = (key_input(vk, 0), key_input(vk, 2))
    batch = (winapi.INPUT * len(seq))(*seq)
    winapi.user32.SendInput(len(seq), batch, ctypes.sizeof(winapi.INPUT))


def click_box(box):
    y0, x0, y1, x1 = [float(v) for v in box]
    winapi.move_mouse_norm((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    time.sleep(0.05)
    winapi.click_mouse()
    time.sleep(1.0)
    return "click %.1f %.1f %.1f %.1f" % (y0, x0, y1, x1)


def act(obj):
    kind = str(obj.get("do", "")).strip().lower()
    if kind == "" and "box_2d" in obj:
        kind = "click"
    if kind == "click":
        return click_box(obj["box_2d"])
    if kind == "type":
        winapi.type_text(str(obj.get("text", "")))
        time.sleep(0.3)
        key = str(obj.get("key", "")).strip().lower()
        if key:
            press(key)
            time.sleep(2.0)
            return "type " + str(obj.get("text", "")) + " key " + key
        return "type " + str(obj.get("text", ""))
    if kind == "key":
        press(str(obj.get("key", "")).strip().lower())
        time.sleep(2.0)
        return "key " + str(obj.get("key", "")).strip().lower()
    if kind == "wait":
        time.sleep(4.0)
        return "wait"
    if kind == "done":
        return "done"
    return "idle"


def readable(text):
    flat = " ".join((text or "").split())
    low = flat.lower()
    return "lighthouse" in low and "single word" not in low and len(flat) < 240


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    winapi.init_dpi()
    winapi.user32.GetDpiForSystem.restype = ctypes.c_uint
    note("revision 7")
    note("dpi %s" % int(winapi.user32.GetDpiForSystem()))
    pairs = gemma.config_pairs(gemma.config_body())
    for key in (
        "gemma.ctx",
        "gemma.batch",
        "gemma.ubatch",
        "gemma.image-min-tokens",
        "gemma.image-max-tokens",
        "gemma.mtmd-batch",
        "gemma.temp",
        "gemma.top-k",
        "gemma.top-p",
        "gemma.seed",
        "gemma.n-predict",
    ):
        note(key + " " + pairs[key])
    done = ["type lighthouse key enter"]
    turn = 1
    while turn <= TURNS:
        note("turn %d" % turn)
        user = next_goal(done)
        if done and not any(item.endswith(" key enter") for item in done):
            user += "\nDone so far:\n" + "\n".join(done)
        reply = ask(user)
        obj = object_from(reply)
        if obj is None:
            done.append("unparsed")
            note("unparsed")
            turn += 1
            continue
        note("see " + " ".join(str(obj.get("see", "")).split()))
        taken = act(obj)
        note("act " + taken)
        if taken != "done":
            done.append(taken)
            turn += 1
            continue
        if not any(item.startswith("type ") for item in done):
            done.append("done before type")
            note("done before type")
            turn += 1
            continue
        heard = ask("Transcribe only the newest ChatGPT assistant message that is visible. No other words.")
        if readable(heard):
            note("success <<\n" + heard + "<<")
            return
        done.append("transcribe missed")
        note("transcribe missed")
        turn += 1
    note("unfinished")


if __name__ == "__main__":
    main()
