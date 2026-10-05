import ctypes
import io
from ctypes import wintypes

from PIL import Image, ImageDraw, ImageGrab

from organs import CONFIG

CFG = CONFIG["eyes"]
VIDEO = (960, 540)
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL


def screen_size() -> tuple[int, int]:
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def imprint(image: Image.Image) -> Image.Image:
    image = image.copy()
    width, height = image.size
    sw, sh = screen_size()
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return image
    px = min(max(round(point.x * (width - 1) / max(1, sw - 1)), 0), width - 1)
    py = min(max(round(point.y * (height - 1) / max(1, sh - 1)), 0), height - 1)
    draw = ImageDraw.Draw(image)
    draw.polygon([(px, py), (px, py + 16), (px + 4, py + 13), (px + 7, py + 19), (px + 10, py + 17), (px + 6, py + 12), (px + 12, py + 12)], fill=(255, 255, 255), outline=(0, 0, 0))
    return image


def picture() -> bytes:
    image = ImageGrab.grab().convert("RGB")
    image.thumbnail((CFG["max_side"], CFG["max_side"]), Image.LANCZOS)
    return png(imprint(image))


def video_rgb() -> Image.Image:
    return imprint(ImageGrab.grab().convert("RGB").resize(VIDEO, Image.BILINEAR))


def screen_px(y: int, x: int) -> tuple[int, int]:
    sw, sh = screen_size()
    return round(x / 1000 * (sw - 1)), round(y / 1000 * (sh - 1))


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
