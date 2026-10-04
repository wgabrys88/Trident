import ctypes
import io
from ctypes import wintypes

from PIL import Image, ImageDraw, ImageFont, ImageGrab

from organs import CONFIG, state_dir

CFG = CONFIG["eyes"]
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))


def screen_size() -> tuple[int, int]:
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def screenshot() -> bytes:
    image = ImageGrab.grab()
    image.thumbnail((CFG["max_side"], CFG["max_side"]), Image.LANCZOS)
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def shrink(png: bytes, side: int = 0) -> bytes:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    side = side or CFG["cloud_side"]
    image.thumbnail((side, side), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def crop(png: bytes, box: list) -> bytes:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    width, height = image.size
    y0, x0, y1, x1 = (float(v) for v in box)
    left, top = round(min(x0, x1) / 1000 * width), round(min(y0, y1) / 1000 * height)
    right, bottom = round(max(x0, x1) / 1000 * width), round(max(y0, y1) / 1000 * height)
    piece = image.crop((left, top, max(right, left + 1), max(bottom, top + 1)))
    if max(piece.size) < 512:
        piece = piece.resize((piece.width * 4, piece.height * 4), Image.NEAREST)
    buffer = io.BytesIO()
    piece.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def mark(png: bytes, areas: list) -> bytes:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    width, height = image.size
    draw = ImageDraw.Draw(image)
    for label, box in areas:
        y0, x0, y1, x1 = (float(v) for v in box)
        left, top = round(min(x0, x1) / 1000 * (width - 1)), round(min(y0, y1) / 1000 * (height - 1))
        right, bottom = round(max(x0, x1) / 1000 * (width - 1)), round(max(y0, y1) / 1000 * (height - 1))
        draw.rectangle((left, top, max(right, left + 1), max(bottom, top + 1)), outline=(255, 0, 0), width=3)
        draw.text((left + 4, top + 4), str(label), fill=(255, 255, 0), font=ImageFont.load_default(size=16))
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    sw, sh = screen_size()
    px, py = round(point.x * (width - 1) / (sw - 1)), round(point.y * (height - 1) / (sh - 1))
    draw.polygon([(px, py), (px, py + 16), (px + 4, py + 13), (px + 7, py + 19), (px + 10, py + 17), (px + 6, py + 12), (px + 12, py + 12)], fill=(255, 255, 255), outline=(0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def embed(box: list, inner: list) -> list:
    y0, x0, y1, x1 = (float(v) for v in box)
    top, left, bottom, right = min(y0, y1), min(x0, x1), max(y0, y1), max(x0, x1)
    iy0, ix0, iy1, ix1 = (float(v) for v in inner)
    return [top + (bottom - top) * iy0 / 1000, left + (right - left) * ix0 / 1000, top + (bottom - top) * iy1 / 1000, left + (right - left) * ix1 / 1000]


def center_px(box_2d: list) -> tuple[int, int]:
    width, height = screen_size()
    y0, x0, y1, x1 = (float(v) for v in box_2d)
    return round((x0 + x1) / 2000 * (width - 1)), round((y0 + y1) / 2000 * (height - 1))


def point_px(box: list, y: float, x: float) -> tuple[int, int]:
    y0, x0, y1, x1 = (float(v) for v in box)
    top, left = min(y0, y1), min(x0, x1)
    gy = top + (max(y0, y1) - top) * float(y) / 1000
    gx = left + (max(x0, x1) - left) * float(x) / 1000
    return center_px([gy, gx, gy, gx])


if __name__ == "__main__":
    path = state_dir() / "screen.png"
    path.write_bytes(screenshot())
    print(path, screen_size())
