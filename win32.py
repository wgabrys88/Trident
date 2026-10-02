import ctypes
import ctypes.wintypes as W

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

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
fault = ""


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


class POINT(ctypes.Structure):
    _fields_ = [("x", W.LONG), ("y", W.LONG)]


user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext.restype = ctypes.c_void_p
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.SendInput.argtypes = [W.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = W.UINT
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = W.BOOL
user32.GetThreadDesktop.argtypes = [W.DWORD]
user32.GetThreadDesktop.restype = ctypes.c_void_p
user32.OpenInputDesktop.argtypes = [W.DWORD, W.BOOL, W.DWORD]
user32.OpenInputDesktop.restype = ctypes.c_void_p
user32.CloseDesktop.argtypes = [ctypes.c_void_p]
user32.CloseDesktop.restype = W.BOOL
user32.GetUserObjectInformationW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD)]
user32.GetUserObjectInformationW.restype = W.BOOL
kernel32.GetCurrentProcess.restype = ctypes.c_void_p
kernel32.GetCurrentProcessId.restype = W.DWORD
kernel32.GetCurrentThreadId.restype = W.DWORD
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.CloseHandle.restype = W.BOOL
kernel32.ProcessIdToSessionId.argtypes = [W.DWORD, ctypes.POINTER(W.DWORD)]
kernel32.ProcessIdToSessionId.restype = W.BOOL
kernel32.WTSGetActiveConsoleSessionId.restype = W.DWORD
advapi32.OpenProcessToken.argtypes = [ctypes.c_void_p, W.DWORD, ctypes.POINTER(ctypes.c_void_p)]
advapi32.OpenProcessToken.restype = W.BOOL
advapi32.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD)]
advapi32.GetTokenInformation.restype = W.BOOL
advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, W.DWORD]
advapi32.GetSidSubAuthority.restype = ctypes.POINTER(W.DWORD)


def set_dpi_aware():
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))


def _fail(reason):
    global fault
    fault = reason
    return False


def _object_name(handle):
    if not handle:
        return ""
    buf = ctypes.create_unicode_buffer(128)
    need = W.DWORD()
    if not user32.GetUserObjectInformationW(handle, 2, buf, ctypes.sizeof(buf), ctypes.byref(need)):
        return ""
    return buf.value


def seat_fault():
    token = ctypes.c_void_p()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        return "integrity unreadable"
    try:
        need = W.DWORD()
        advapi32.GetTokenInformation(token, 25, None, 0, ctypes.byref(need))
        if need.value < 8:
            return "integrity unreadable"
        buf = ctypes.create_string_buffer(need.value)
        if not advapi32.GetTokenInformation(token, 25, buf, need.value, ctypes.byref(need)):
            return "integrity unreadable"
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p)).contents.value
        count = advapi32.GetSidSubAuthorityCount(sid).contents.value
        if count < 1:
            return "integrity unreadable"
        rid = advapi32.GetSidSubAuthority(sid, count - 1).contents.value
    finally:
        kernel32.CloseHandle(token)
    if rid < 0x3000:
        return "integrity %d" % rid
    sess = W.DWORD()
    if not kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(sess)):
        return "session unreadable"
    console = kernel32.WTSGetActiveConsoleSessionId()
    if sess.value != console:
        return "session %d console %d" % (sess.value, console)
    thread_name = _object_name(user32.GetThreadDesktop(kernel32.GetCurrentThreadId()))
    if thread_name != "Default":
        return "thread desktop " + (thread_name or "absent")
    incoming = user32.OpenInputDesktop(0, 0, 1)
    if not incoming:
        return "input desktop absent"
    try:
        input_name = _object_name(incoming)
    finally:
        user32.CloseDesktop(incoming)
    if input_name != "Default":
        return "input desktop " + (input_name or "absent")
    return ""


def _ready():
    global fault
    reason = seat_fault()
    if reason:
        fault = reason
        return False
    fault = ""
    return True


def _send(items):
    if not items:
        return _fail("sendinput 0")
    batch = (INPUT * len(items))(*items)
    sent = user32.SendInput(len(items), batch, ctypes.sizeof(INPUT))
    if sent == len(items):
        return True
    return _fail("sendinput " + str(ctypes.get_last_error() or sent))


def _screen():
    return (
        user32.GetSystemMetrics(0),
        user32.GetSystemMetrics(1),
        user32.GetSystemMetrics(76),
        user32.GetSystemMetrics(77),
        user32.GetSystemMetrics(78),
        user32.GetSystemMetrics(79),
    )


def _pixel(x, y):
    sw, sh, _vx, _vy, _vw, _vh = _screen()
    px = int(round(min(100.0, max(0.0, float(x))) / 100.0 * max(1, sw - 1)))
    py = int(round(min(100.0, max(0.0, float(y))) / 100.0 * max(1, sh - 1)))
    return px, py


def _abs(x, y):
    _sw, _sh, vx, vy, vw, vh = _screen()
    px, py = _pixel(x, y)
    ax = int(round((px - vx) * 65535 / max(1, vw - 1)))
    ay = int(round((py - vy) * 65535 / max(1, vh - 1)))
    return max(0, min(65535, ax)), max(0, min(65535, ay))


def _at(x, y):
    want = _pixel(x, y)
    pt = POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return _fail("cursor unreadable")
    if abs(pt.x - want[0]) <= 1 and abs(pt.y - want[1]) <= 1:
        return True
    return _fail("cursor %d %d wanted %d %d" % (pt.x, pt.y, want[0], want[1]))


def pointer_pct():
    sw, sh, *_rest = _screen()
    pt = POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return None
    return pt.x / max(1, sw - 1) * 100.0, pt.y / max(1, sh - 1) * 100.0


def _mouse(dx, dy, flags, data=0):
    item = INPUT()
    item.type = 0
    item.u.mi = MOUSEINPUT(int(dx), int(dy), int(data) & 0xFFFFFFFF, flags, 0, 0)
    return item


def _go(x, y):
    ax, ay = _abs(x, y)
    base = 0x8000 | 0x4000
    if not _send([_mouse(ax, ay, base | 0x0001)]):
        return None
    if not _at(x, y):
        return None
    return ax, ay, base


def move_pct(x, y):
    if not _ready():
        return False
    return _go(x, y) is not None


def hold_pct(x, y, right=False):
    if not _ready():
        return False
    got = _go(x, y)
    if not got:
        return False
    ax, ay, base = got
    flag = 0x0008 if right else 0x0002
    return _send([_mouse(ax, ay, base | flag)])


def loosen_pct(x, y, right=False):
    if not _ready():
        return False
    got = _go(x, y)
    if not got:
        return False
    ax, ay, base = got
    flag = 0x0010 if right else 0x0004
    return _send([_mouse(ax, ay, base | flag)])


def _key(vk, scan, flags):
    item = INPUT()
    item.type = 1
    item.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return item


def press(name):
    if not _ready():
        return False
    raw = str(name).lower()
    parts = [raw] if raw in VK_MAP else [part for part in raw.replace("+", "-").split("-") if part]
    if not parts or any(part not in VK_MAP for part in parts):
        return _fail("key " + raw)
    vks = [VK_MAP[part] for part in parts]
    items = []
    for vk in vks:
        items.append(_key(vk, 0, 0x0001 if vk in EXTENDED_VKS else 0))
    for vk in reversed(vks):
        items.append(_key(vk, 0, 0x0002 | (0x0001 if vk in EXTENDED_VKS else 0)))
    return _send(items)


def type_text(text):
    if not _ready():
        return False
    items = []
    for ch in text:
        code = ord(ch)
        units = (code,) if code < 0x10000 else (0xD800 + ((code - 0x10000) >> 10), 0xDC00 + ((code - 0x10000) & 0x3FF))
        for unit in units:
            items.append(_key(0, unit, 0x0004))
            items.append(_key(0, unit, 0x0006))
    return bool(items) and _send(items)


set_dpi_aware()
