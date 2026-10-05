import ctypes
import io
from ctypes import wintypes

import numpy as np
from PIL import Image, ImageDraw, ImageGrab

from organs import CONFIG

CFG = CONFIG["eyes"]
VIDEO = (960, 540)
PAGE = ("canvas", "page", "paper", "drawing surface", "white area")
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
boxes: list[tuple[str, int, int, int, int]] = []


def screen_size() -> tuple[int, int]:
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def refresh() -> None:
    global boxes
    from organs.hands import interactive_controls

    boxes = interactive_controls()


def imprint(image: Image.Image) -> Image.Image:
    image = image.copy()
    width, height = image.size
    sw, sh = screen_size()
    draw = ImageDraw.Draw(image)
    for name, x, y, w, h in boxes:
        x0, y0 = x * width / sw, y * height / sh
        x1, y1 = (x + w) * width / sw, (y + h) * height / sh
        draw.rectangle((x0, y0, x1, y1), outline=(0, 255, 255), width=2)
        if y1 - y0 >= 18 and x1 - x0 >= 28:
            draw.text((x0 + 2, y0 + 2), name[:20], fill=(0, 255, 255))
    point = wintypes.POINT()
    if user32.GetCursorPos(ctypes.byref(point)):
        px = min(max(round(point.x * (width - 1) / max(1, sw - 1)), 0), width - 1)
        py = min(max(round(point.y * (height - 1) / max(1, sh - 1)), 0), height - 1)
        draw.line([(0, py), (width - 1, py)], fill=(255, 0, 0), width=2)
        draw.line([(px, 0), (px, height - 1)], fill=(255, 0, 0), width=2)
        draw.polygon([(px, py), (px, py + 16), (px + 4, py + 13), (px + 7, py + 19), (px + 10, py + 17), (px + 6, py + 12), (px + 12, py + 12)], fill=(255, 255, 255), outline=(0, 0, 0))
    return image


def picture() -> bytes:
    refresh()
    image = ImageGrab.grab().convert("RGB")
    image.thumbnail((CFG["max_side"], CFG["max_side"]), Image.LANCZOS)
    return png(imprint(image))


def video_rgb() -> Image.Image:
    return imprint(ImageGrab.grab().convert("RGB").resize(VIDEO, Image.BILINEAR))


def screen_px(y: int, x: int) -> tuple[int, int]:
    sw, sh = screen_size()
    return round(x / 1000 * (sw - 1)), round(y / 1000 * (sh - 1))


def page() -> tuple[int, int, int, int] | None:
    white = np.all(np.asarray(ImageGrab.grab().convert("RGB")) > 240, axis=2)
    ys = np.where(white.mean(axis=1) > 0.55)[0]
    if ys.size < 40:
        return None
    runs = np.split(ys, np.where(np.diff(ys) > 1)[0] + 1)
    run = max(runs, key=len)
    if run.size < 40:
        return None
    top, bottom = int(run[0]), int(run[-1])
    xs = np.where(white[top : bottom + 1].mean(axis=0) > 0.75)[0]
    if xs.size < 40:
        return None
    return int(xs[0]), top, int(xs[-1]), bottom


def place(what: str, y: int, x: int) -> tuple[int, int]:
    if any(word in what.lower() for word in PAGE):
        rect = page()
        if rect:
            left, top, right, bottom = rect
            return round(left + x / 1000 * (right - left)), round(top + y / 1000 * (bottom - top))
    return screen_px(y, x)


def around(blob: bytes, x: int, y: int) -> bytes:
    image = Image.open(io.BytesIO(blob)).convert("RGB")
    sw, sh = screen_size()
    width, height = image.size
    cx = x * (width - 1) / max(1, sw - 1)
    cy = y * (height - 1) / max(1, sh - 1)
    half = 240
    left, top = max(0, round(cx - half)), max(0, round(cy - half))
    right, bottom = min(width, round(cx + half)), min(height, round(cy + half))
    return png(image.crop((left, top, max(right, left + 1), max(bottom, top + 1))))
