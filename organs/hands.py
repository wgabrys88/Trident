import ctypes
import ctypes.wintypes as W
import os
import shutil
import subprocess
import sys
import tempfile
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
user32.GetForegroundWindow.restype = W.HWND
user32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
user32.GetWindowThreadProcessId.restype = W.DWORD
user32.GetKeyboardLayout.argtypes = [W.DWORD]
user32.GetKeyboardLayout.restype = ctypes.c_void_p
user32.VkKeyScanExW.argtypes = [ctypes.c_wchar, ctypes.c_void_p]
user32.VkKeyScanExW.restype = ctypes.c_short

MOVE, ABSOLUTE, VIRTUAL = 0x0001, 0x8000, 0x4000
BUTTON = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010)}


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


def aim(x: int, y: int) -> tuple[int, int]:
    user32.SetCursorPos(x, y)
    _send([_mouse(x, y, MOVE)])
    time.sleep(0.05)
    point = W.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def strike(x: int, y: int, how: str) -> None:
    button = "right" if how == "right" else "left"
    down, up = BUTTON[button]
    items = [_mouse(x, y, down), _mouse(x, y, up)]
    if how == "double":
        items += [_mouse(x, y, down), _mouse(x, y, up)]
    _send(items)


def click(x: int, y: int, how: str = "left") -> tuple[int, int]:
    aimed = aim(x, y)
    strike(x, y, how)
    return aimed


def drag(x0: int, y0: int, x1: int, y1: int) -> None:
    down, up = BUTTON["left"]
    _send([_mouse(x0, y0, MOVE), _mouse(x0, y0, down)])
    for step in range(1, 11):
        t = step / 10
        _send([_mouse(round(x0 + (x1 - x0) * t), round(y0 + (y1 - y0) * t), MOVE)])
        time.sleep(0.02)
    _send([_mouse(x1, y1, up)])


def press(keys: str) -> None:
    for chord in keys.lower().split():
        vks = [VK[part] for part in chord.replace("+", "-").split("-") if part]
        items = [_key(vk, 0, 0x0001 if vk in EXTENDED else 0) for vk in vks]
        items += [_key(vk, 0, 0x0002 | (0x0001 if vk in EXTENDED else 0)) for vk in reversed(vks)]
        _send(items)
        time.sleep(0.05)


def type_text(text: str) -> None:
    tid = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    layout = user32.GetKeyboardLayout(tid)
    for ch in text:
        scanned = user32.VkKeyScanExW(ch, layout) if ord(ch) <= 0xFFFF else -1
        if scanned == -1:
            raw = ch.encode("utf-16-le")
            for i in range(0, len(raw), 2):
                unit = int.from_bytes(raw[i : i + 2], "little")
                _send([_key(0, unit, 0x0004), _key(0, unit, 0x0006)])
                time.sleep(0.05)
            continue
        seq = [vk for bit, vk in ((0x100, 0x10), (0x200, 0x11), (0x400, 0x12)) if scanned & bit]
        seq.append(scanned & 0xFF)
        _send([_key(vk, 0, 0) for vk in seq] + [_key(vk, 0, 0x0002) for vk in reversed(seq)])
        time.sleep(0.01)


def interactive_controls(title: str = "") -> list[tuple[str, int, int, int, int]]:
    from organs import run_dir
    script = (
        "param([string]$Title)\n"
        "Add-Type -AssemblyName UIAutomationClient\n"
        "if ($Title) {\n"
        "  $root = [System.Windows.Automation.AutomationElement]::RootElement\n"
        "  $cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, $Title)\n"
        "  $win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)\n"
        "} else {\n"
        "  Add-Type -MemberDefinition '[DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow();' -Name W -Namespace N\n"
        "  $win = [System.Windows.Automation.AutomationElement]::FromHandle([N.W]::GetForegroundWindow())\n"
        "}\n"
        "if (-not $win) { exit 0 }\n"
        "$keep = @('ControlType.Button','ControlType.MenuItem','ControlType.ListItem','ControlType.RadioButton','ControlType.CheckBox','ControlType.Edit','ControlType.ComboBox','ControlType.Hyperlink','ControlType.TabItem','ControlType.SplitButton','ControlType.Slider','ControlType.Spinner','ControlType.TreeItem','ControlType.DataItem')\n"
        "$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)\n"
        "for ($i = 0; $i -lt $all.Count; $i++) {\n"
        "  $e = $all[$i]\n"
        "  $n = $e.Current.Name\n"
        "  if (-not $n) { continue }\n"
        "  $t = $e.Current.ControlType.ProgrammaticName\n"
        "  if ($keep -notcontains $t) { continue }\n"
        "  $r = $e.Current.BoundingRectangle\n"
        "  if ($r.Width -lt 4 -or $r.Height -lt 4) { continue }\n"
        "  Write-Output (\"{0},{1},{2},{3}|{4}\" -f [int]$r.X, [int]$r.Y, [int]$r.Width, [int]$r.Height, $n)\n"
        "}\n"
    )
    path = run_dir() / "controls.ps1"
    path.write_text(script, encoding="utf-8")
    try:
        args = ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(path)]
        if title:
            args += ["-Title", title]
        done = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    finally:
        path.unlink(missing_ok=True)
    found: dict[str, tuple[int, int, int, int]] = {}
    for line in (done.stdout or "").splitlines():
        head, _, name = line.partition("|")
        parts = head.split(",")
        if len(parts) != 4 or not name.strip():
            continue
        x, y, w, h = (int(v) for v in parts)
        name = name.strip()
        old = found.get(name)
        if old is None or w * h < old[2] * old[3]:
            found[name] = (x, y, w, h)
    return [(name, *box) for name, box in found.items()]


def run(command: str, timeout: int = 25) -> str:
    text = command.strip()
    found = shutil.which(text) if text and not any(c in text for c in " \t;&|$<>") else None
    if found and os.path.getsize(found) == 0:
        command = "Start-Process -FilePath " + text
    out, err = tempfile.TemporaryFile(), tempfile.TemporaryFile()
    proc = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "Remove-Item Alias:start -Force; " + command], stdin=subprocess.DEVNULL, stdout=out, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        out.close()
        err.close()
        return "timed out"
    out.seek(0)
    err.seek(0)
    text = (out.read() + b"\n" + err.read()).decode("utf-8", errors="replace").strip()
    out.close()
    err.close()
    if command.lower().lstrip().startswith("start-process"):
        time.sleep(1.5)
    return text or (f"exit {proc.returncode}" if proc.returncode else "ok")


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
