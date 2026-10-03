"""Eyes: the desktop as a picture, and the geometry between Gemma's grid and the screen.

Gemma 4 localizes on a 1000 x 1000 grid, [y0, x0, y1, x1], origin top-left, whatever the image size.
So the screenshot can be any size: a box's center at (x/1000, y/1000) of the real screen is the click.
The model never converts anything; this file does.

Run alone:  python -m organs.eyes   -> writes state/screen.png and prints the open window titles.
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
    """PNG of the primary screen, longest side <= eyes.max_side."""
    image = ImageGrab.grab()
    image.thumbnail((CFG["max_side"], CFG["max_side"]), Image.LANCZOS)
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def center_px(box_2d: list) -> tuple[int, int]:
    """[y0, x0, y1, x1] on the 1000-grid -> (x, y) pixels on the real screen."""
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
