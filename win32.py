import ctypes
import ctypes.wintypes as W

user32 = ctypes.WinDLL("user32", use_last_error=True)

VK_MAP = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "escape": 0x1B, "esc": 0x1B,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "ins": 0x2D,
    "home": 0x24, "end": 0x23,
    "pageup": 0x21, "page_up": 0x21, "pgup": 0x21,
    "pagedown": 0x22, "page_down": 0x22, "pgdn": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10,
    "win": 0x5B, "windows": 0x5B, "meta": 0x5B, "super": 0x5B, "space": 0x20,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B,
    "`": 0xC0, "~": 0xC0,
    "-": 0xBD, "_": 0xBD,
    "=": 0xBB, "+": 0xBB,
    "[": 0xDB, "{": 0xDB,
    "]": 0xDD, "}": 0xDD,
    "\\": 0xDC, "|": 0xDC,
    ";": 0xBA, ":": 0xBA,
    "'": 0xDE, '"': 0xDE,
    ",": 0xBC, "<": 0xBC,
    ".": 0xBE, ">": 0xBE,
    "/": 0xBF, "?": 0xBF,
} | {chr(ord("a") + i): ord("A") + i for i in range(26)} | {chr(ord("0") + i): ord("0") + i for i in range(10)}

EXTENDED_VKS = frozenset({0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E})


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", W.WORD),
        ("wScan", W.WORD),
        ("dwFlags", W.DWORD),
        ("time", W.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", W.LONG),
        ("dy", W.LONG),
        ("mouseData", W.DWORD),
        ("dwFlags", W.DWORD),
        ("time", W.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

    _fields_ = [("type", W.DWORD), ("u", _U)]


user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext.restype = ctypes.c_void_p
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.SendInput.argtypes = [W.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = W.UINT


def set_dpi_aware():
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))


def _send(items):
    batch = (INPUT * len(items))(*items)
    return user32.SendInput(len(items), batch, ctypes.sizeof(INPUT)) == len(items)


def _screen():
    return (
        user32.GetSystemMetrics(0),
        user32.GetSystemMetrics(1),
        user32.GetSystemMetrics(76),
        user32.GetSystemMetrics(77),
        user32.GetSystemMetrics(78),
        user32.GetSystemMetrics(79),
    )


def _abs(x, y):
    sw, sh, vx, vy, vw, vh = _screen()
    px = min(100.0, max(0.0, float(x))) / 100.0 * max(1, sw - 1)
    py = min(100.0, max(0.0, float(y))) / 100.0 * max(1, sh - 1)
    ax = int(round((px - vx) * 65535 / max(1, vw - 1)))
    ay = int(round((py - vy) * 65535 / max(1, vh - 1)))
    return max(0, min(65535, ax)), max(0, min(65535, ay))


def _mouse(dx, dy, flags):
    item = INPUT()
    item.type = 0
    item.u.mi = MOUSEINPUT(dx, dy, 0, flags, 0, 0)
    return item


def move_pct(x, y):
    ax, ay = _abs(x, y)
    return _send([_mouse(ax, ay, 0x8001 | 0x4000)])


def click_pct(x, y):
    ax, ay = _abs(x, y)
    base = 0x8000 | 0x4000
    return _send([
        _mouse(ax, ay, base | 0x0001),
        _mouse(ax, ay, base | 0x0002),
        _mouse(ax, ay, base | 0x0004),
    ])


def drag_pct(x0, y0, x1, y1):
    ax, ay = _abs(x0, y0)
    bx, by = _abs(x1, y1)
    base = 0x8000 | 0x4000
    return _send([
        _mouse(ax, ay, base | 0x0001),
        _mouse(ax, ay, base | 0x0002),
        _mouse(bx, by, base | 0x0001),
        _mouse(bx, by, base | 0x0004),
    ])


def _key(vk, scan, flags):
    item = INPUT()
    item.type = 1
    item.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return item


def press(name):
    raw = str(name).lower()
    parts = [raw] if raw in VK_MAP else [part for part in raw.replace("+", "-").split("-") if part]
    if not parts or any(part not in VK_MAP for part in parts):
        return False
    vks = [VK_MAP[part] for part in parts]
    items = []
    for vk in vks:
        items.append(_key(vk, 0, 0x0001 if vk in EXTENDED_VKS else 0))
    for vk in reversed(vks):
        items.append(_key(vk, 0, 0x0002 | (0x0001 if vk in EXTENDED_VKS else 0)))
    return _send(items)


def type_text(text):
    items = []
    for ch in text:
        code = ord(ch)
        units = (code,) if code < 0x10000 else (0xD800 + ((code - 0x10000) >> 10), 0xDC00 + ((code - 0x10000) & 0x3FF))
        for unit in units:
            items.append(_key(0, unit, 0x0004))
            items.append(_key(0, unit, 0x0006))
    return bool(items) and _send(items)


set_dpi_aware()
