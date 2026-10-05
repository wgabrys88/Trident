import ctypes
import io
from ctypes import wintypes

from PIL import Image, ImageDraw, ImageFont, ImageGrab

from organs import CONFIG

CFG = CONFIG["eyes"]
VIDEO = (960, 540)
SMALL = ImageFont.load_default(size=16)
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
boxes: list[tuple[str, int, int, int, int]] = []
shown: Image.Image | None = None


def screen_size() -> tuple[int, int]:
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def refresh() -> None:
    global boxes
    from organs.hands import interactive_controls

    boxes = interactive_controls()


def _pointer(image: Image.Image, px: int, py: int) -> None:
    width, height = image.size
    px = min(max(px, 0), width - 1)
    py = min(max(py, 0), height - 1)
    draw = ImageDraw.Draw(image)
    draw.line([(0, py), (width - 1, py)], fill=(255, 0, 0), width=2)
    draw.line([(px, 0), (px, height - 1)], fill=(255, 0, 0), width=2)
    draw.polygon([(px, py), (px, py + 16), (px + 4, py + 13), (px + 7, py + 19), (px + 10, py + 17), (px + 6, py + 12), (px + 12, py + 12)], fill=(255, 255, 255), outline=(0, 0, 0))


def imprint(image: Image.Image) -> Image.Image:
    image = image.copy()
    width, height = image.size
    sw, sh = screen_size()
    draw = ImageDraw.Draw(image)
    for name, x, y, w, h in boxes:
        x0, y0 = x * width / sw, y * height / sh
        x1, y1 = (x + w) * width / sw, (y + h) * height / sh
        draw.rectangle((x0, y0, x1, y1), outline=(0, 255, 255), width=2)
        draw.text((x0 + 2, max(0, y0 - 16)), name[:40], fill=(0, 255, 255), font=SMALL)
    point = wintypes.POINT()
    if user32.GetCursorPos(ctypes.byref(point)):
        _pointer(image, round(point.x * (width - 1) / max(1, sw - 1)), round(point.y * (height - 1) / max(1, sh - 1)))
    return image


def picture() -> bytes:
    global shown
    refresh()
    image = ImageGrab.grab().convert("RGB")
    image.thumbnail((CFG["max_side"], CFG["max_side"]), Image.LANCZOS)
    shown = imprint(image)
    return _png(shown)


def live() -> None:
    global shown
    shown = None


def video_rgb() -> Image.Image:
    frame = shown
    if frame is not None:
        return frame.resize(VIDEO, Image.BILINEAR)
    return imprint(ImageGrab.grab().convert("RGB").resize(VIDEO, Image.BILINEAR))


def screen_px(y: int, x: int) -> tuple[int, int]:
    sw, sh = screen_size()
    return round(x / 1000 * (sw - 1)), round(y / 1000 * (sh - 1))


def around(png: bytes, x: int, y: int) -> bytes:
    global shown
    image = Image.open(io.BytesIO(png)).convert("RGB")
    sw, sh = screen_size()
    width, height = image.size
    cx = x * (width - 1) / max(1, sw - 1)
    cy = y * (height - 1) / max(1, sh - 1)
    half = 240
    left, top = max(0, round(cx - half)), max(0, round(cy - half))
    right, bottom = min(width, round(cx + half)), min(height, round(cy + half))
    shown = image.crop((left, top, max(right, left + 1), max(bottom, top + 1)))
    return _png(shown)
