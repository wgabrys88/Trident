import io, subprocess, time
import ctypes as C
import win32api, win32gui
from ctypes import wintypes as W
from PIL import Image, ImageDraw, ImageFont, ImageGrab
from core import CONFIG

USER = C.WinDLL("user32", use_last_error=True)
USER.SetProcessDpiAwarenessContext(C.c_void_p(-4))

class Mouse(C.Structure):
    _fields_ = [("x", W.LONG), ("y", W.LONG), ("data", W.DWORD), ("flags", W.DWORD),
                ("time", W.DWORD), ("extra", C.c_size_t)]

class Keyboard(C.Structure):
    _fields_ = [("vk", W.WORD), ("scan", W.WORD), ("flags", W.DWORD), ("time", W.DWORD), ("extra", C.c_size_t)]

class Payload(C.Union):
    _fields_ = [("mouse", Mouse), ("key", Keyboard)]

class Input(C.Structure):
    _fields_ = [("kind", W.DWORD), ("payload", Payload)]

USER.SendInput.argtypes = [W.UINT, C.POINTER(Input), C.c_int]
VK = {"enter": 13, "tab": 9, "escape": 27, "esc": 27, "backspace": 8, "delete": 46, "insert": 45,
      "home": 36, "end": 35, "pageup": 33, "pagedown": 34, "up": 38, "down": 40, "left": 37, "right": 39,
      "ctrl": 17, "alt": 18, "shift": 16, "win": 91, "space": 32, "printscreen": 44,
      **{f"f{i}": 111 + i for i in range(1, 13)}, **{chr(i).lower(): i for i in range(65, 91)},
      **{str(i): 48 + i for i in range(10)}, **dict(zip("-=[]\\;',./`", (189, 187, 219, 221, 220, 186, 222, 188, 190, 191, 192)))}

def bounds():
    return tuple(win32api.GetSystemMetrics(i) for i in (76, 77, 78, 79))

def image_size():
    _, _, width, height = bounds()
    scale = min(1, CONFIG["eyes"]["max_side"] / max(width, height))
    return round(width * scale) // 2 * 2, round(height * scale) // 2 * 2

def position():
    x, y = win32api.GetCursorPos()
    left, top, width, height = bounds()
    return round((y - top) * 1000 / (height - 1)), round((x - left) * 1000 / (width - 1))

def png(image):
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()

def annotate(data, marks):
    if not marks:
        return data
    image = Image.open(io.BytesIO(data)).convert("RGB")
    draw, font = ImageDraw.Draw(image), ImageFont.load_default(size=14)
    for mark in marks:
        x0, x1 = (round(mark[key] * (image.width - 1) / 1000) for key in ("x0", "x1"))
        y0, y1 = (round(mark[key] * (image.height - 1) / 1000) for key in ("y0", "y1"))
        draw.rectangle((x0, y0, x1, y1), outline="yellow", width=3)
        draw.rectangle(draw.textbbox((x0, y0), mark["label"], font=font), fill="black")
        draw.text((x0, y0), mark["label"], font=font, fill="yellow")
    return png(image)

def picture():
    left, top, width, height = bounds()
    image = ImageGrab.grab((left, top, left + width, top + height), all_screens=True).convert("RGB").resize(image_size())
    draw, font = ImageDraw.Draw(image), ImageFont.load_default(size=14)
    for value in range(0, 1001, 100):
        x, y = round(value * (image.width - 1) / 1000), round(value * (image.height - 1) / 1000)
        for text, place, anchor in ((f"x={value}", (32 if value == 0 else x, 1), "rt" if value == 1000 else "lt"),
                                    (f"y={value}", (1, y), "lb" if value == 1000 else "lt")):
            draw.rectangle(draw.textbbox(place, text, font=font, anchor=anchor), fill="black")
            draw.text(place, text, font=font, anchor=anchor, fill="white")
    gy, gx = position()
    x, y = round(gx * (image.width - 1) / 1000), round(gy * (image.height - 1) / 1000)
    draw.polygon([(x, y), (x, y + 16), (x + 4, y + 13), (x + 7, y + 19), (x + 10, y + 17),
                  (x + 6, y + 12), (x + 12, y + 12)], fill="white", outline="black")
    return f"Whole screen: {image.width} x {image.height} pixels; y down, x right, both 0–1000.", png(image)

def send(*inputs):
    if USER.SendInput(len(inputs), (Input * len(inputs))(*inputs), C.sizeof(Input)) != len(inputs):
        raise C.WinError(C.get_last_error())

def key(vk, scan=0, flags=0):
    return Input(1, Payload(key=Keyboard(vk, scan, flags, 0, 0)))

def button(flags):
    return Input(0, Payload(mouse=Mouse(0, 0, 0, flags, 0, 0)))

def point(y: int, x: int):
    left, top, width, height = bounds()
    win32api.SetCursorPos((left + round(x * (width - 1) / 1000), top + round(y * (height - 1) / 1000)))
    return "Pointer at y {} x {}.".format(*position())

def click(how: str):
    down, up = {"left": (2, 4), "right": (8, 16), "double": (2, 4)}[how]
    y, x = position()
    for _ in range(2 if how == "double" else 1):
        send(button(down), button(up))
    return f"{how} click at y {y} x {x}."

def stroke(points: str):
    path = [tuple(map(int, pair.split())) for pair in points.split(";")]
    point(*path[0])
    send(button(2))
    try:
        for y, x in path[1:]:
            point(y, x)
            time.sleep(0.02)
    finally:
        send(button(4))
    return "Stroke through {}; pointer y {} x {}.".format(points, *position())

def title():
    return win32gui.GetWindowText(win32gui.GetForegroundWindow())

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
    return f"Typed {text!r} into {window!r}."

def press(keys: str):
    window, extended = title(), {33, 34, 35, 36, 37, 38, 39, 40, 45, 46, 91}
    for chord in keys.lower().split():
        codes = [VK[part] for part in chord.split("+")]
        send(*[key(vk, flags=int(vk in extended)) for vk in codes],
             *[key(vk, flags=2 | int(vk in extended)) for vk in reversed(codes)])
        time.sleep(0.05)
    return f"Pressed {keys!r} into {window!r}."

def run(command: str):
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return f"Exit {result.returncode}\n" + (result.stdout + result.stderr).decode("utf-8", "replace")
