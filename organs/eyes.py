"""A PNG of the desktop, and the conversion from Gemma's grid to screen pixels.

Her boxes are [y0, x0, y1, x1] on a 1000 by 1000 grid, origin at the top left, at any image size.
center_px() is the center of such a box on the real screen.
python -m organs.eyes saves state/screen.png and prints the window titles.
"""

import ctypes
import io

from PIL import Image, ImageGrab

from organs import CONFIG, state_dir

CFG = CONFIG["eyes"]
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]


def screen_size() -> tuple[int, int]:
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def screenshot() -> bytes:
    """PNG of the primary monitor, longest side no greater than eyes.max_side."""
    image = ImageGrab.grab()
    image.thumbnail((CFG["max_side"], CFG["max_side"]), Image.LANCZOS)
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def corner(png: bytes) -> bytes:
    """PNG of the bottom-right corner, scaled up, where the clock is."""
    image = Image.open(io.BytesIO(png)).convert("RGB")
    width, height = image.size
    crop = image.crop((max(0, width - 420), max(0, height - 90), width, height))
    crop = crop.resize((max(1, crop.width * 3), max(1, crop.height * 3)), Image.Resampling.NEAREST)
    buffer = io.BytesIO()
    crop.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def crop(png: bytes, box: list) -> bytes:
    """PNG of a [y0, x0, y1, x1] box on the 1000-grid, with each side halved."""
    image = Image.open(io.BytesIO(png)).convert("RGB")
    width, height = image.size
    y0, x0, y1, x1 = (float(v) for v in box)
    left, top = round(min(x0, x1) / 1000 * width), round(min(y0, y1) / 1000 * height)
    right, bottom = round(max(x0, x1) / 1000 * width), round(max(y0, y1) / 1000 * height)
    piece = image.crop((left, top, max(right, left + 1), max(bottom, top + 1)))
    piece = piece.resize((max(1, piece.width // 2), max(1, piece.height // 2)), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    piece.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def center_px(box_2d: list) -> tuple[int, int]:
    """(x, y) pixels for the center of a [y0, x0, y1, x1] box on the 1000-grid."""
    width, height = screen_size()
    y0, x0, y1, x1 = (float(v) for v in box_2d)
    return round((x0 + x1) / 2000 * (width - 1)), round((y0 + y1) / 2000 * (height - 1))


def window_titles(limit: int = 8) -> list[str]:
    titles: list[str] = []

    def add(hwnd):
        buf = ctypes.create_unicode_buffer(256)
        if hwnd and user32.GetWindowTextW(hwnd, buf, 256) > 0:
            title = " ".join(buf.value.split())
            if title and title not in titles and len(titles) < limit:
                titles.append(title)

    add(user32.GetForegroundWindow())

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            add(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return titles


if __name__ == "__main__":
    path = state_dir() / "screen.png"
    path.write_bytes(screenshot())
    print(path, screen_size())
    print("\n".join(window_titles()))
