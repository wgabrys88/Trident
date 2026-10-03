"""Hands: mouse, keyboard and the shell. Pixels in, Windows input out.

Run alone:  python -m organs.hands press win-d
            python -m organs.hands type "hello"
            python -m organs.hands click 500 300
            python -m organs.hands run "Get-Date"
"""

import ctypes
import ctypes.wintypes as W
import subprocess
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

VK = {
    "enter": 0x0D, "tab": 0x09, "escape": 0x1B, "esc": 0x1B, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B, "space": 0x20, "printscreen": 0x2C,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
    **{chr(ord("a") + i): ord("A") + i for i in range(26)},
    **{chr(ord("0") + i): ord("0") + i for i in range(10)},
    "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF, "`": 0xC0,
}
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD), ("dwFlags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

    _fields_ = [("type", W.DWORD), ("u", _U)]


user32.SendInput.argtypes = [W.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = W.UINT

MOVE, ABSOLUTE, VIRTUAL = 0x0001, 0x8000, 0x4000
BUTTON = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}


def _send(items: list[INPUT]) -> None:
    batch = (INPUT * len(items))(*items)
    sent = user32.SendInput(len(items), batch, ctypes.sizeof(INPUT))
    if sent != len(items):
        raise OSError(f"SendInput sent {sent} of {len(items)}")


def _mouse(x: int, y: int, flags: int) -> INPUT:
    vx, vy, vw, vh = (user32.GetSystemMetrics(i) for i in (76, 77, 78, 79))
    ax = int((x - vx) * 65535 / max(1, vw - 1))
    ay = int((y - vy) * 65535 / max(1, vh - 1))
    item = INPUT(type=0)
    item.u.mi = MOUSEINPUT(ax, ay, 0, flags | ABSOLUTE | VIRTUAL, 0, 0)
    return item


def _key(vk: int, scan: int, flags: int) -> INPUT:
    item = INPUT(type=1)
    item.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return item


def move(x: int, y: int) -> None:
    _send([_mouse(x, y, MOVE)])


def click(x: int, y: int, how: str = "left") -> None:
    """how: left, right, double."""
    button = "right" if how == "right" else "left"
    down, up = BUTTON[button]
    items = [_mouse(x, y, MOVE), _mouse(x, y, down), _mouse(x, y, up)]
    if how == "double":
        items += [_mouse(x, y, down), _mouse(x, y, up)]
    _send(items)


def drag(x0: int, y0: int, x1: int, y1: int) -> None:
    down, up = BUTTON["left"]
    _send([_mouse(x0, y0, MOVE), _mouse(x0, y0, down)])
    for step in range(1, 11):
        t = step / 10
        _send([_mouse(round(x0 + (x1 - x0) * t), round(y0 + (y1 - y0) * t), MOVE)])
        time.sleep(0.02)
    _send([_mouse(x1, y1, up)])


def press(keys: str) -> None:
    """'enter', 'ctrl-a', 'win-r', 'alt-f4'. Several chords separated by spaces are pressed in turn."""
    for chord in keys.lower().split():
        vks = [VK[part] for part in chord.replace("+", "-").split("-") if part]
        items = [_key(vk, 0, 0x0001 if vk in EXTENDED else 0) for vk in vks]
        items += [_key(vk, 0, 0x0002 | (0x0001 if vk in EXTENDED else 0)) for vk in reversed(vks)]
        _send(items)
        time.sleep(0.05)


def type_text(text: str) -> None:
    encoded = text.encode("utf-16-le")
    items = []
    for i in range(0, len(encoded), 2):
        unit = int.from_bytes(encoded[i : i + 2], "little")
        items.append(_key(0, unit, 0x0004))
        items.append(_key(0, unit, 0x0006))
    if items:
        _send(items)


def run(command: str, timeout: int = 25) -> str:
    """One PowerShell command. Returns stdout+stderr clipped to 600 characters."""
    try:
        done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return "timed out"
    out = (done.stdout + b"\n" + done.stderr).decode("utf-8", errors="replace")
    text = " ".join(out.split())[:600]
    if command.lower().lstrip().startswith(("start-process", "start ")):
        time.sleep(1.5)
    return text or (f"exit {done.returncode}" if done.returncode else "ok")


if __name__ == "__main__":
    verb, rest = sys.argv[1], sys.argv[2:]
    if verb == "press":
        press(rest[0])
    elif verb == "type":
        type_text(" ".join(rest))
    elif verb == "click":
        click(int(rest[0]), int(rest[1]), rest[2] if len(rest) > 2 else "left")
    elif verb == "run":
        print(run(" ".join(rest)))
