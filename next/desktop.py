import ctypes
import io
import subprocess
import time
from ctypes import wintypes as W
from PIL import Image, ImageDraw, ImageGrab
from next import CONFIG

USER = ctypes.WinDLL("user32", use_last_error=True)
USER.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
USER.GetCursorPos.argtypes = [ctypes.POINTER(W.POINT)]
USER.GetForegroundWindow.restype = W.HWND
USER.GetWindowTextLengthW.argtypes = [W.HWND]
USER.GetWindowTextW.argtypes = [W.HWND, ctypes.c_wchar_p, ctypes.c_int]


class Mouse(ctypes.Structure):
    _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("data", W.DWORD), ("flags", W.DWORD),
                ("time", W.DWORD), ("extra", ctypes.c_size_t)]


class Keyboard(ctypes.Structure):
    _fields_ = [("vk", W.WORD), ("scan", W.WORD), ("flags", W.DWORD),
                ("time", W.DWORD), ("extra", ctypes.c_size_t)]


class Payload(ctypes.Union):
    _fields_ = [("mouse", Mouse), ("keyboard", Keyboard)]


class Input(ctypes.Structure):
    _fields_ = [("type", W.DWORD), ("payload", Payload)]


USER.SendInput.argtypes = [W.UINT, ctypes.POINTER(Input), ctypes.c_int]
VK = {"enter": 13, "tab": 9, "escape": 27, "esc": 27, "backspace": 8, "delete": 46,
      "insert": 45, "home": 36, "end": 35, "pageup": 33, "pagedown": 34, "up": 38,
      "down": 40, "left": 37, "right": 39, "ctrl": 17, "alt": 18, "shift": 16, "win": 91,
      "space": 32, "printscreen": 44, **{f"f{i}": 111 + i for i in range(1, 13)},
      **{chr(i).lower(): i for i in range(65, 91)}, **{str(i): 48 + i for i in range(10)},
      "-": 189, "=": 187, "[": 219, "]": 221, "\\": 220, ";": 186, "'": 222,
      ",": 188, ".": 190, "/": 191, "`": 192}


def bounds():
    return tuple(USER.GetSystemMetrics(i) for i in (76, 77, 78, 79))


def position():
    point = W.POINT()
    if not USER.GetCursorPos(ctypes.byref(point)):
        raise ctypes.WinError(ctypes.get_last_error())
    return point.x, point.y


def grid():
    left, top, width, height = bounds()
    x, y = position()
    return f"y {round((y - top) * 1000 / (height - 1))} x {round((x - left) * 1000 / (width - 1))}"


def pixels(y, x):
    left, top, width, height = bounds()
    return left + round(x * (width - 1) / 1000), top + round(y * (height - 1) / 1000)


def picture():
    left, top, width, height = bounds()
    image = ImageGrab.grab(bbox=(left, top, left + width, top + height), all_screens=True).convert("RGB")
    image.thumbnail((CONFIG["eyes"]["max_side"],) * 2, Image.Resampling.LANCZOS)
    image = image.resize((image.width // 2 * 2, image.height // 2 * 2))
    x, y = position()
    px, py = round((x - left) * (image.width - 1) / (width - 1)), round((y - top) * (image.height - 1) / (height - 1))
    ImageDraw.Draw(image).polygon([(px, py), (px, py + 16), (px + 4, py + 13), (px + 7, py + 19),
                                 (px + 10, py + 17), (px + 6, py + 12), (px + 12, py + 12)],
                                fill="white", outline="black")
    buffer = io.BytesIO()
    image.save(buffer, "PNG", compress_level=1)
    return buffer.getvalue()


def send(*items):
    batch = (Input * len(items))(*items)
    if USER.SendInput(len(items), batch, ctypes.sizeof(Input)) != len(items):
        raise ctypes.WinError(ctypes.get_last_error())


def key(vk, scan=0, flags=0):
    return Input(1, Payload(keyboard=Keyboard(vk, scan, flags, 0, 0)))


def button(flags):
    return Input(0, Payload(mouse=Mouse(0, 0, 0, flags, 0, 0)))


def move(y: int, x: int):
    if not USER.SetCursorPos(*pixels(y, x)):
        raise ctypes.WinError(ctypes.get_last_error())
    return "Pointer at " + grid() + "."


def click(how: str):
    down, up = {"left": (2, 4), "right": (8, 16), "double": (2, 4)}[how]
    place = grid()
    send(button(down), button(up))
    if how == "double":
        send(button(down), button(up))
    return f"{how} click at {place}."


def stroke(points: str):
    places = [tuple(map(int, part.split())) for part in points.split(";")]
    if not 1 <= len(places) <= 32 or any(len(p) != 2 for p in places):
        raise ValueError(points)
    y, x = places[0]
    move(y, x)
    send(button(2))
    try:
        for ny, nx in places[1:]:
            for step in range(1, 11):
                move(y + (ny - y) * step / 10, x + (nx - x) * step / 10)
                time.sleep(0.02)
            y, x = ny, nx
    finally:
        send(button(4))
    return f"Stroke through {points}; pointer at {grid()}."


def title():
    window = USER.GetForegroundWindow()
    buffer = ctypes.create_unicode_buffer(USER.GetWindowTextLengthW(window) + 1)
    USER.GetWindowTextW(window, buffer, len(buffer))
    return buffer.value


def type_text(text: str):
    window = title()
    for char in text.replace("\r\n", "\n"):
        if char in "\r\n\t":
            vk = 9 if char == "\t" else 13
            send(key(vk), key(vk, flags=2))
        else:
            raw = char.encode("utf-16-le")
            for offset in range(0, len(raw), 2):
                scan = int.from_bytes(raw[offset:offset + 2], "little")
                send(key(0, scan, 4), key(0, scan, 6))
    return f'Typed {text!r} into window "{window}".'


def press(keys: str):
    window = title()
    for chord in keys.lower().split():
        codes = [VK[part] for part in chord.split("+")]
        extended = {33, 34, 35, 36, 37, 38, 39, 40, 45, 46, 91}
        send(*[key(vk, flags=int(vk in extended)) for vk in codes],
             *[key(vk, flags=2 | int(vk in extended)) for vk in reversed(codes)])
        time.sleep(0.05)
    return f'Pressed {keys!r} into window "{window}".'


def run(command: str):
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return f"Exit {result.returncode}\n" + (result.stdout + result.stderr).decode("utf-8", "replace")
