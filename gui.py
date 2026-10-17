"""Native Windows mouse, keyboard and clipboard input. Navigation belongs to image recognition.

Coordinates are physical pixels on the virtual desktop. get_desktop() returns one shared
input object; batch() calls the same public methods sequentially and releases input on failure.
"""
import ctypes
import math
import subprocess
import time
from ctypes import wintypes
from typing import Any

user32 = None


def _bind_windows():
    global user32
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetCursorPos.argtypes, user32.GetCursorPos.restype = [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL
    user32.SendInput.argtypes, user32.SendInput.restype = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int], wintypes.UINT
    user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
    user32.SetThreadDpiAwarenessContext.argtypes, user32.SetThreadDpiAwarenessContext.restype = [ctypes.c_void_p], ctypes.c_void_p
    if not user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4)):
        raise ctypes.WinError()


def screen_bounds():
    left, top, width, height = (int(user32.GetSystemMetrics(i)) for i in (76, 77, 78, 79))
    return dict(left=left, top=top, width=width, height=height)


def _move_cursor(x, y):
    screen = screen_bounds()
    nx = min(65535, round((x - screen['left'] + .5) * 65536 / screen['width']))
    ny = min(65535, round((y - screen['top'] + .5) * 65536 / screen['height']))
    _mouse_input(0xE001, nx, ny) # Absolute virtual-desktop movement, without coalescing.


def _mouse_input(flags, x=0, y=0, data=0):
    event = _INPUT(type=0, u=_INPUTUNION(mi=_MOUSEINPUT(dx=x, dy=y, mouseData=data, dwFlags=flags)))
    if user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_INPUT)) != 1:
        raise RuntimeError(f"mouse input was not delivered (Windows error {ctypes.get_last_error()})")


KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
_ULONG_PTR = ctypes.c_size_t


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


KEY_MAP: dict[str, int] = {
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B, "windows": 0x5B,
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "escape": 0x1B, "esc": 0x1B, "space": 0x20,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "delete": 0x2E, "del": 0x2E, "backspace": 0x08, "insert": 0x2D,
    **{chr(ord("a") + i): 0x41 + i for i in range(26)},
    **{str(d): 0x30 + d for d in range(10)},
    **{f"f{n}": 0x6F + n for n in range(1, 13)},
}


class Desktop:
    def __init__(self):
        self._buttons = set()
        self._keys = set()
        self.PAUSE = .02

    screen_bounds = staticmethod(screen_bounds)

    def position(self):
        point = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            raise ctypes.WinError()
        return point.x, point.y

    def _target(self, x, y):
        if y is None and isinstance(x, (tuple, list)):
            x, y = x
        current = self.position()
        values = [current[i] if v is None else float(v) for i, v in enumerate((x, y))]
        if not all(math.isfinite(v) for v in values):
            raise ValueError("mouse coordinates must be finite physical pixels")
        x, y = map(round, values)
        screen = screen_bounds()
        if not (screen['left'] <= x < screen['left'] + screen['width'] and screen['top'] <= y < screen['top'] + screen['height']):
            raise ValueError(f"physical mouse point {(x, y)} is outside the virtual desktop")
        return x, y

    def moveTo(self, x=None, y=None, duration=0, tween=None, _pause=True, **kw):
        target = self._target(x, y)
        start = self.position()
        seconds = float(duration)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("movement duration must be finite and nonnegative")
        steps = max(1, math.ceil(math.dist(start, target) / 4), math.ceil(seconds / .016)) if self._buttons or seconds else 1
        for i in range(1, steps + 1):
            fraction = tween(i / steps) if callable(tween) else i / steps
            _move_cursor(*(round(a + (b - a) * fraction) for a, b in zip(start, target)))
            if steps > 1:
                time.sleep(max(.004, seconds / steps))
        if _pause:
            time.sleep(self.PAUSE)
        return {"ok": True, "action": "move", "x": target[0], "y": target[1]}

    def moveRel(self, xOffset=0, yOffset=0, duration=0, **kw):
        x, y = self.position()
        return self.moveTo(x + xOffset, y + yOffset, duration, **kw)

    def mouseDown(self, x=None, y=None, button="left", _pause=True):
        flag = {"left": 0x0002, "right": 0x0008, "middle": 0x0020}[button]
        if x is not None or y is not None:
            self.moveTo(x, y, _pause=False)
        _mouse_input(flag)
        self._buttons.add(button)
        time.sleep(.035)

    def mouseUp(self, x=None, y=None, button="left", _pause=True):
        try:
            if x is not None or y is not None:
                self.moveTo(x, y, _pause=False)
        finally:
            _mouse_input({"left": 0x0004, "right": 0x0010, "middle": 0x0040}[button])
            self._buttons.discard(button)
        if _pause:
            time.sleep(self.PAUSE)

    def click_at(self, x=None, y=None, button="left", clicks=1, interval=.08):
        if not isinstance(clicks, int) or clicks < 1:
            raise ValueError("clicks must be a positive integer")
        self.moveTo(x, y)
        for i in range(clicks):
            try:
                self.mouseDown(button=button)
            finally:
                self.mouseUp(button=button)
            if i + 1 < clicks:
                time.sleep(interval)
        return {"ok": True, "action": "click", "button": button, "clicks": clicks}

    def rightClick(self, x=None, y=None, **kw):
        return self.click_at(x, y, button="right", **kw)

    def doubleClick(self, x=None, y=None, **kw):
        return self.click_at(x, y, clicks=2, **kw)

    def drag(self, points, duration=.2, button="left"):
        points = [self._target(*point) for point in points]
        if len(points) < 2:
            raise ValueError("drag requires at least two physical point pairs")
        self.moveTo(*points[0])
        try:
            self.mouseDown(button=button)
            for point in points[1:]:
                self.moveTo(*point, duration=duration, _pause=False)
        finally:
            self.mouseUp(button=button)
        return {"ok": True, "action": "drag", "points": len(points)}

    def dragTo(self, x=None, y=None, duration=.2, button="left", **kw):
        return self.drag([self.position(), self._target(x, y)], duration, button)

    def dragRel(self, xOffset=0, yOffset=0, duration=.2, **kw):
        x, y = self.position()
        return self.dragTo(x + xOffset, y + yOffset, duration, **kw)

    def keyDown(self, key, _pause=True):
        key = str(key).lower()
        vk = KEY_MAP[key]
        user32.keybd_event(vk, 0, 1 if vk in (0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B) else 0, 0)
        self._keys.add(key)

    def keyUp(self, key, _pause=True):
        key = str(key).lower()
        vk = KEY_MAP[key]
        user32.keybd_event(vk, 0, 3 if vk in (0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B) else 2, 0)
        self._keys.discard(key)

    def release_input(self):
        for button in tuple(self._buttons):
            self.mouseUp(button=button, _pause=False)
        for key in self._keys | {"ctrl", "alt", "shift", "win"}:
            self.keyUp(key, _pause=False)

    def batch(self, actions):
        results = []
        try:
            for index, action in enumerate(actions):
                try:
                    name = action["action"]
                    if name.startswith('_') or name == 'batch':
                        raise ValueError("batch requires a public desktop action")
                    fn = getattr(self, name)
                    results.append(fn(*action.get("args", []), **action.get("kwargs", {})))
                except Exception as error:
                    raise RuntimeError(f"batch action {index} failed: {error}") from error
        finally:
            self.release_input()
        return results

    def set_clipboard(self, text: str) -> dict[str, Any]:
        command = ["powershell.exe", "-NoProfile", "-Command", "[Console]::InputEncoding=[Text.UTF8Encoding]::new(); $in=[Console]::In.ReadToEnd(); Set-Clipboard -Value $in"]
        completed = subprocess.run(command, input=str(text).encode("utf-8"), capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if completed.returncode != 0:
            raise RuntimeError(f"clipboard write failed: {(completed.stderr or completed.stdout).decode('utf-8', 'replace').strip()}")
        return {"ok": True, "action": "set_clipboard", "chars": len(str(text))}

    def get_clipboard(self):
        command = ["powershell.exe", "-NoProfile", "-Command", "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); [Console]::Write((Get-Clipboard -Raw))"]
        result = subprocess.run(command, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        return result.stdout.decode("utf-8-sig")

    def type_text(self, text: str) -> dict[str, Any]:
        s = str(text)
        code_units = list(s.encode("utf-16-le"))
        events = []
        for i in range(0, len(code_units), 2):
            unit = code_units[i] | (code_units[i + 1] << 8)
            for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                events.append(_INPUT(type=1, u=_INPUTUNION(ki=_KEYBDINPUT(wVk=0, wScan=unit, dwFlags=flags, time=0, dwExtraInfo=0))))
        if not events:
            return {"ok": True, "action": "type_text", "chars": 0}
        arr = (_INPUT * len(events))(*events)
        sent = user32.SendInput(len(events), arr, ctypes.sizeof(_INPUT))
        if sent != len(events):
            raise RuntimeError(f"Unicode input delivered {sent} of {len(events)} events (Windows error {ctypes.get_last_error()})")
        return {"ok": True, "action": "type_text", "chars": len(s)}

    def paste_clipboard(self, text: str) -> dict[str, Any]:
        self.set_clipboard(text)
        pasted = self.hotkey("ctrl", "v")
        if pasted.get("ok") is not True:
            raise RuntimeError(f"paste failed: {pasted}")
        return {"ok": True, "action": "paste_clipboard", "chars": len(str(text))}

    def press_key(self, key: str) -> dict[str, Any]:
        vk = KEY_MAP.get(str(key).strip().lower())
        if vk is None:
            raise RuntimeError(f"unknown key: {key}; known: {', '.join(sorted(KEY_MAP))}")
        try:
            self.keyDown(key)
        finally:
            self.keyUp(key)
        return {"ok": True, "action": "press_key", "key": key}

    def hotkey(self, *keys: Any) -> dict[str, Any]:
        if len(keys) == 1 and isinstance(keys[0], (list, tuple)):
            raw_parts = list(keys[0])
        elif len(keys) == 1:
            raw_parts = str(keys[0]).split("+")
        else:
            raw_parts = list(keys)
        parts = [str(k).strip().lower() for k in raw_parts if str(k).strip()]
        if not parts:
            raise RuntimeError("hotkey requires at least one key")
        for k in parts:
            vk = KEY_MAP.get(k)
            if vk is None:
                raise RuntimeError(f"unknown key in combination: {k}; known: {', '.join(sorted(KEY_MAP))}")
        try:
            for key in parts:
                self.keyDown(key)
        finally:
            for key in reversed(parts):
                self.keyUp(key)
        return {"ok": True, "action": "hotkey", "keys": parts}

    def scroll(self, clicks, x=None, y=None):
        if x is not None or y is not None:
            self.moveTo(x, y)
        _mouse_input(0x0800, data=int(clicks) * 120)
        return dict(ok=True, action='scroll', clicks=int(clicks))

    click = click_at
    move = moveRel
    press = press_key
    write = typewrite = type_text
    copy = set_clipboard
    paste = get_clipboard


_desktop_instance = None


def get_desktop():
    global _desktop_instance
    if user32 is None:
        _bind_windows()
    if _desktop_instance is None:
        _desktop_instance = Desktop()
    return _desktop_instance
