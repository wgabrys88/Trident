"""A PNG of the desktop, and the conversion from Gemma's grid to screen pixels.

Her boxes are [y0, x0, y1, x1] on a 1000 by 1000 grid, origin at the top left, at any image size.
center_px() is the center of such a box on the real screen.
python -m organs.eyes saves state/screen.png and prints the window titles.
"""

import ctypes
import ctypes.wintypes
import io

from PIL import Image, ImageGrab

from organs import CONFIG, state_dir

CFG = CONFIG["eyes"]
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.wintypes.RECT)]
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


def strips(png: bytes) -> list[tuple[str, bytes]]:
    """Each line in the front window, scaled up. who is user on the right, assistant on the left, or empty when the line sits in the middle."""
    image = Image.open(io.BytesIO(png)).convert("RGB")
    box = ctypes.wintypes.RECT()
    user32.GetWindowRect(user32.GetForegroundWindow(), ctypes.byref(box))
    image = image.crop((max(0, box.left), max(0, box.top), min(image.width, box.right), min(image.height, box.bottom)))
    width, height = image.size
    pix = image.load()
    dark = [sum(sum(pix[x, y]) > 90 for y in range(height // 5, 4 * height // 5)) < 3 for x in range(width)]
    best, run = 0, 0
    at = 0
    for x, flag in enumerate(dark):
        run = run + 1 if flag else 0
        if run > best:
            best, at = run, x
    left = at + 3
    rows = []
    for y in range(int(height * 0.16), height - 8):
        xs = [x for x in range(left, width - 8) if sum(pix[x, y]) > 180]
        if len(xs) <= 8:
            continue
        x1 = xs[0]
        for x in xs[1:]:
            if x - x1 > 36:
                break
            x1 = x
        rows.append((y, xs[0], x1))
    bands: list[list[int]] = []
    for y, x0, x1 in rows:
        if bands and y - bands[-1][1] <= 16:
            bands[-1][1], bands[-1][2], bands[-1][3] = y, min(bands[-1][2], x0), max(bands[-1][3], x1)
        else:
            bands.append([y, y, x0, x1])
    kept = []
    for y0, y1, x0, x1 in bands:
        if kept and y0 - kept[-1][1] > 160:
            break
        area = (y1 - y0 + 1) * (x1 - x0 + 1)
        bright = sum(sum(pix[x, y]) > 180 for y in range(y0, y1 + 1) for x in range(x0, x1 + 1))
        if y1 - y0 < 24 and x1 - x0 > 120 and bright / area < 0.18:
            continue
        kept.append((y0, y1, x0, x1))
    if not kept:
        return []
    margin = min(item[2] for item in kept)
    right = max(item[3] for item in kept)
    out = []
    for y0, y1, x0, x1 in kept:
        crop = image.crop((max(0, x0 - 6), max(0, y0 - 4), min(width, x1 + 6), min(height, y1 + 4)))
        crop = crop.resize((crop.width * 3, crop.height * 3), Image.Resampling.NEAREST)
        buf = io.BytesIO()
        crop.save(buf, format="PNG", compress_level=1)
        who = "assistant" if x0 <= margin + 80 else "user" if x1 >= right - 40 else ""
        out.append((who, buf.getvalue()))
    return out


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
