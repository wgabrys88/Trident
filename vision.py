import importlib.util
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = {}
BOX = re.compile(
    r"\[\s*\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]\s*\]"
)


def load(folder, name):
    key = folder + ":" + name
    if key in CACHE:
        return CACHE[key]
    path = ROOT / folder / (name + ".py")
    if not path.is_file():
        raise RuntimeError("missing " + str(path))
    parent = str(path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    mod_name = name + "_" + folder.replace("-", "_")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    CACHE[key] = mod
    return mod


def jpeg(png):
    from PIL import Image

    img = Image.open(io.BytesIO(png)).convert("RGB")
    if img.width > 1280:
        img = img.resize((1280, int(img.height * 1280 / img.width)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def parse_box(text):
    match = BOX.search(text or "")
    if not match:
        raise RuntimeError("vision found nothing")
    return [float(match.group(i)) for i in range(1, 5)]


def locate(query, crop_title):
    frame_mod = load("endgame-vision-chat", "vision_frame")
    coords = load("endgame-vision-chat", "coords")
    ask = load("screenshot-vision", "ask")
    frame = frame_mod.grab(looking_for=query, crop_title=crop_title, window_title=crop_title)
    if crop_title and not frame.crop_png:
        raise RuntimeError("vision crop absent")
    png = frame.crop_png or frame.full_png
    if not ask.is_server_ready():
        if ask.is_server_up():
            raise RuntimeError("vision server absent")
        ask.start_server()
    prompt = ask.PROMPTS["find"] + "\n\nElement to find: " + query
    text = ask.ask(jpeg(png), prompt, ask.DEFAULT_MODEL)
    box = parse_box(text)
    win = None
    if frame.crop_png:
        for item in frame.windows:
            if crop_title.lower() in (item.get("title") or "").lower():
                win = item
                break
        if win is None:
            raise RuntimeError("vision crop absent")
        rect = win["rect"]
        width = rect["right"] - rect["left"]
        height = rect["bottom"] - rect["top"]
        x0, y0, x1, y1 = coords.box_to_px(box, width, height)
        return rect["left"] + (x0 + x1) // 2, rect["top"] + (y0 + y1) // 2
    x0, y0, x1, y1 = coords.box_to_px(box, frame.screen_w, frame.screen_h)
    return (x0 + x1) // 2, (y0 + y1) // 2
