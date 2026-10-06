import ctypes
import io
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


def prepare_image(image, origin, scale, region, max_edge):
    if region is not None:
        x, y, width, height = region
        left, top = round((x - origin[0]) * scale[0]), round((y - origin[1]) * scale[1])
        right, bottom = round((x + width - origin[0]) * scale[0]), round((y + height - origin[1]) * scale[1])
        if right <= left or bottom <= top or left < 0 or top < 0 or right > image.width or bottom > image.height:
            raise ValueError("Crop must be a nonempty region within the supplied image")
        image = image.crop((left, top, right, bottom))
        origin = [origin[0] + left / scale[0], origin[1] + top / scale[1]]
    original = image.size
    ratio = min(1, max_edge / max(original))
    size = [max(48, round(side * ratio / 48) * 48) for side in original]
    image = image.resize(size, Image.Resampling.BICUBIC)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue(), {"origin": origin, "size": size,
                              "scale": [scale[i] * size[i] / original[i] for i in range(2)],
                              "mapping": "desktop_xy = origin_xy + image_xy / scale_xy"}


def capture(region, max_edge):
    left, top, width, height = [win32api.GetSystemMetrics(n) for n in (76, 77, 78, 79)]
    captured = stamp()
    image = ImageGrab.grab(bbox=(left, top, left + width, top + height), all_screens=True)
    payload, metadata = prepare_image(image, [left, top], [1, 1], region, max_edge)
    return payload, {**metadata, "captured_at": captured, "desktop_bounds": [left, top, width, height],
                     "pointer": list(win32api.GetCursorPos()), "window": win32gui.GetWindowText(win32gui.GetForegroundWindow())}


def image_view(view):
    with Image.open(view["path"]) as image:
        if image.format != "PNG":
            raise ValueError("Gemma images must be PNG")
        payload, metadata = prepare_image(image, view["origin"], view["scale"], view["region"], view["max_edge"])
        return payload, {**metadata, "source": view}


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
