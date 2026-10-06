import ctypes
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
if not user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
    raise ctypes.WinError(ctypes.get_last_error())

import pyautogui
import win32api
import win32clipboard
import win32con
import win32gui
from PIL import Image, ImageGrab

from store import stamp

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0


def prepare_image(image, path, origin, scale, region, magnify):
    if region is not None:
        x, y, width, height = region
        left, top = (x - origin[0]) * scale, (y - origin[1]) * scale
        right, bottom = left + width * scale, top + height * scale
        if width <= 0 or height <= 0 or left < 0 or top < 0 or right > image.width or bottom > image.height:
            raise ValueError("Crop must be a nonempty region within the supplied image")
        image = image.crop((left, top, right, bottom))
        origin = [x, y]
    if magnify != 1:
        image = image.resize((image.width * magnify, image.height * magnify), Image.Resampling.LANCZOS)
    image.save(path)
    return {"image": str(path), "origin": origin, "size": list(image.size), "scale": scale * magnify,
            "mapping": "desktop_xy = origin_xy + image_xy / scale"}


def capture(path, region, magnify):
    left, top, width, height = [win32api.GetSystemMetrics(n) for n in (76, 77, 78, 79)]
    captured = stamp()
    image = ImageGrab.grab(bbox=(left, top, left + width, top + height), all_screens=True)
    return {**prepare_image(image, path, [left, top], 1, region, magnify), "captured_at": captured,
            "desktop_bounds": [left, top, width, height],
            "pointer": list(win32api.GetCursorPos()), "window": win32gui.GetWindowText(win32gui.GetForegroundWindow())}


def image_view(view, path):
    with Image.open(view["path"]) as image:
        if image.format != "PNG":
            raise ValueError("Gemma images must be PNG")
        return {**prepare_image(image, path, view["origin"], view["scale"], view["region"], view["magnify"]),
                "source": view}


def move(x, y):
    win32api.SetCursorPos((x, y))


def click(x, y, button, count):
    move(x, y)
    down, up = {"left": (2, 4), "right": (8, 16), "middle": (32, 64)}[button]
    for index in range(count):
        win32api.mouse_event(down, 0, 0)
        win32api.mouse_event(up, 0, 0)
        if index + 1 < count:
            time.sleep(0.08)
    return {"button": button, "count": count, "pointer": list(win32api.GetCursorPos())}


def stroke(points, seconds):
    x, y = points[0]
    move(x, y)
    win32api.mouse_event(2, 0, 0)
    try:
        for x, y in points[1:]:
            move(x, y)
            time.sleep(seconds / (len(points) - 1))
    finally:
        win32api.mouse_event(4, 0, 0)
    return {"points": points, "seconds": seconds}


def type_text(text):
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()
    pyautogui.hotkey("ctrl", "v")
    return {"pasted": text}


def keys(chord):
    for name in chord:
        if name not in pyautogui.KEYBOARD_KEYS:
            raise ValueError(f"Unknown key: {name}")
    pyautogui.hotkey(*chord)
    return {"pressed": chord}


def scroll(amount):
    pyautogui.scroll(amount)
    return {"scroll": amount}
