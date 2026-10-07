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

from store import CONFIG, stamp

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0


def prepare_image(image, origin, scale, region):
    if region is not None:
        x, y, width, height = region
        left, top = round((x - origin[0]) * scale[0]), round((y - origin[1]) * scale[1])
        right, bottom = round((x + width - origin[0]) * scale[0]), round((y + height - origin[1]) * scale[1])
        if right <= left or bottom <= top or left < 0 or top < 0 or right > image.width or bottom > image.height:
            raise ValueError("Crop must be a nonempty region within the supplied image")
        image = image.crop((left, top, right, bottom))
        origin = [origin[0] + left / scale[0], origin[1] + top / scale[1]]
    crop = list(image.size)
    ratio = min(1, 1024 / max(crop))
    prepared = [max(1, round(side * ratio)) for side in crop]
    if prepared != crop:
        image = image.resize(prepared, Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue(), {
        "origin": origin, "crop": crop, "prepared": prepared,
        "scale": [scale[i] * prepared[i] / crop[i] for i in range(2)],
        "frame": "prepared PNG",
    }


def place(box, receipt):
    width, height = receipt["prepared"]
    origin_x, origin_y = receipt["origin"]
    scale_x, scale_y = receipt["scale"]
    xmin, ymin, xmax, ymax = box
    desktop = [
        round(origin_x + (xmin / 1000) * width / scale_x),
        round(origin_y + (ymin / 1000) * height / scale_y),
        round(origin_x + (xmax / 1000) * width / scale_x),
        round(origin_y + (ymax / 1000) * height / scale_y),
    ]
    return {"box": desktop, "center": [round((desktop[0] + desktop[2]) / 2), round((desktop[1] + desktop[3]) / 2)]}


def capture(region):
    left, top, width, height = [win32api.GetSystemMetrics(n) for n in (76, 77, 78, 79)]
    captured = stamp()
    image = ImageGrab.grab(bbox=(left, top, left + width, top + height), all_screens=True)
    payload, metadata = prepare_image(image, [left, top], [1, 1], region)
    return payload, {**metadata, "captured_at": captured, "desktop_bounds": [left, top, width, height],
                     "pointer": list(win32api.GetCursorPos()), "window": win32gui.GetWindowText(win32gui.GetForegroundWindow())}


def image_view(view):
    with Image.open(view["path"]) as image:
        if image.format != "PNG":
            raise ValueError("Vision images must be PNG")
        payload, metadata = prepare_image(image, view["origin"], view["scale"], view["region"])
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


def sample():
    left, top, width, height = [win32api.GetSystemMetrics(n) for n in (76, 77, 78, 79)]
    image = ImageGrab.grab(bbox=(left, top, left + width, top + height), all_screens=True)
    return image.convert("RGB").resize((64, 36), Image.Resampling.BOX).tobytes()


def difference(before, after):
    if before is None or len(before) != len(after):
        return CONFIG["screen"]["difference"] + 1
    return sum(abs(a - b) for a, b in zip(before, after)) / len(before)
