"""A PNG of the desktop, and the conversion from Gemma's grid to screen pixels.

Her boxes are [y0, x0, y1, x1] on a 1000 by 1000 grid, origin at the top left, at any image size.
center_px() is the center of such a box on the real screen.
point_px() is a 1000-grid point inside a crop, mapped back onto that screen.
shrink() is the longest side eyes.cloud_side, for a cloud look.
python -m organs.eyes saves state/screen.png and prints the screen size.
"""

import ctypes
import io

from PIL import Image, ImageDraw, ImageGrab

from organs import CONFIG, state_dir

CFG = CONFIG["eyes"]
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))


def screen_size() -> tuple[int, int]:
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def screenshot() -> bytes:
    """PNG of the primary monitor, longest side no greater than eyes.max_side."""
    image = ImageGrab.grab()
    image.thumbnail((CFG["max_side"], CFG["max_side"]), Image.LANCZOS)
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def shrink(png: bytes) -> bytes:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    side = CFG["cloud_side"]
    image.thumbnail((side, side), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def crop(png: bytes, box: list) -> bytes:
    """PNG of a [y0, x0, y1, x1] box on the 1000-grid."""
    image = Image.open(io.BytesIO(png)).convert("RGB")
    width, height = image.size
    y0, x0, y1, x1 = (float(v) for v in box)
    left, top = round(min(x0, x1) / 1000 * width), round(min(y0, y1) / 1000 * height)
    right, bottom = round(max(x0, x1) / 1000 * width), round(max(y0, y1) / 1000 * height)
    piece = image.crop((left, top, max(right, left + 1), max(bottom, top + 1)))
    buffer = io.BytesIO()
    piece.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def mark(png: bytes, box: list) -> bytes:
    """A copy of png with one [y0, x0, y1, x1] rectangle on the 1000-grid. png is unchanged."""
    image = Image.open(io.BytesIO(png)).convert("RGB")
    width, height = image.size
    y0, x0, y1, x1 = (float(v) for v in box)
    left, top = round(min(x0, x1) / 1000 * (width - 1)), round(min(y0, y1) / 1000 * (height - 1))
    right, bottom = round(max(x0, x1) / 1000 * (width - 1)), round(max(y0, y1) / 1000 * (height - 1))
    ImageDraw.Draw(image).rectangle((left, top, max(right, left + 1), max(bottom, top + 1)), outline=(255, 0, 0), width=3)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def center_px(box_2d: list) -> tuple[int, int]:
    """(x, y) pixels for the center of a [y0, x0, y1, x1] box on the 1000-grid."""
    width, height = screen_size()
    y0, x0, y1, x1 = (float(v) for v in box_2d)
    return round((x0 + x1) / 2000 * (width - 1)), round((y0 + y1) / 2000 * (height - 1))


def point_px(box: list, y: float, x: float) -> tuple[int, int]:
    """Screen pixel for a 1000-grid point inside a crop. box is that crop on the full 1000-grid."""
    y0, x0, y1, x1 = (float(v) for v in box)
    top, left = min(y0, y1), min(x0, x1)
    gy = top + (max(y0, y1) - top) * float(y) / 1000
    gx = left + (max(x0, x1) - left) * float(x) / 1000
    return center_px([gy, gx, gy, gx])


if __name__ == "__main__":
    path = state_dir() / "screen.png"
    path.write_bytes(screenshot())
    print(path, screen_size())
