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
from PIL import ImageGrab

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0


def capture(path):
    left, top, width, height = [win32api.GetSystemMetrics(n) for n in (76, 77, 78, 79)]
    ImageGrab.grab(bbox=(left, top, left + width, top + height), all_screens=True).save(path)
    return {"image": str(path), "origin": [left, top], "size": [width, height],
            "pointer": list(win32api.GetCursorPos()), "window": win32gui.GetWindowText(win32gui.GetForegroundWindow())}


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
