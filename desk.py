import ctypes
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JOB = ROOT / "endgame.job"
AUDIO = ROOT / "audio.card"
HANGUP = threading.Event()
WAITING = {"on": False}
USER = {"lib": None}


def die(message):
    print(message, file=sys.stderr, flush=True)
    err = SystemExit(2)
    err.message = message
    raise err


def winuser():
    lib = USER["lib"]
    if lib is not None:
        return lib
    lib = ctypes.WinDLL("user32", use_last_error=True)
    lib.OpenInputDesktop.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    lib.OpenInputDesktop.restype = ctypes.c_void_p
    lib.CloseDesktop.argtypes = [ctypes.c_void_p]
    lib.CloseDesktop.restype = ctypes.c_int
    lib.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.ShowWindow.restype = ctypes.c_int
    lib.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    lib.SetForegroundWindow.restype = ctypes.c_int
    lib.GetForegroundWindow.restype = ctypes.c_void_p
    lib.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    lib.GetWindowThreadProcessId.restype = ctypes.c_uint32
    lib.AttachThreadInput.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int]
    lib.AttachThreadInput.restype = ctypes.c_int
    lib.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.GetAncestor.restype = ctypes.c_void_p
    lib.IsIconic.argtypes = [ctypes.c_void_p]
    lib.IsIconic.restype = ctypes.c_int
    lib.IsWindowVisible.argtypes = [ctypes.c_void_p]
    lib.IsWindowVisible.restype = ctypes.c_int
    lib.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    lib.GetWindowTextLengthW.restype = ctypes.c_int
    lib.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    lib.GetWindowTextW.restype = ctypes.c_int
    lib.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_ssize_t]
    lib.EnumWindows.restype = ctypes.c_int
    lib.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    lib.SetCursorPos.restype = ctypes.c_int
    lib.mouse_event.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
    lib.mouse_event.restype = None
    USER["lib"] = lib
    return lib


def interactive():
    lib = winuser()
    handle = lib.OpenInputDesktop(0, 0, 0x0001)
    if not handle:
        return False
    lib.CloseDesktop(handle)
    return True


def telegram_exe():
    roots = []
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")
    if local:
        roots.append(Path(local) / "Telegram Desktop" / "Telegram.exe")
    if roaming:
        roots.append(Path(roaming) / "Telegram Desktop" / "Telegram.exe")
    for path in roots:
        if path.is_file():
            return str(path)
    return ""


def chrome_exe():
    roots = []
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env, "")
        if base:
            roots.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for path in roots:
        if path.is_file():
            return str(path)
    return ""


def owner_config():
    path = ROOT / "telegram.owner.txt"
    found = {"owner": "", "call": "", "end": ""}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, rest = line.partition(" ")
            if sep and key in found:
                found[key] = rest.strip()
    env = {
        "owner": os.environ.get("TRIDENT_TELEGRAM_OWNER", "").strip(),
        "call": os.environ.get("TRIDENT_TELEGRAM_CALL", "").strip(),
        "end": os.environ.get("TRIDENT_TELEGRAM_END", "").strip(),
    }
    return env["owner"] or found["owner"], env["call"] or found["call"], env["end"] or found["end"]


def gui():
    import vision
    return vision.load("endgame-ai", "gui")


def desktop():
    return gui().get_desktop()


def find_window(title):
    needle = title.casefold()
    lib = winuser()
    plain = []
    iconic = []

    def callback(hwnd, _):
        if not lib.IsWindowVisible(hwnd):
            return True
        length = lib.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        lib.GetWindowTextW(hwnd, buf, length + 1)
        if needle not in buf.value.casefold():
            return True
        if lib.IsIconic(hwnd):
            iconic.append(int(hwnd))
        else:
            plain.append(int(hwnd))
        return True

    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_ssize_t)(callback)
    lib.EnumWindows(proc, 0)
    picks = plain or iconic
    if not picks:
        return None
    return picks[0]


def focus_hwnd(hwnd):
    lib = winuser()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentThreadId.restype = ctypes.c_uint32
    lib.ShowWindow(hwnd, 9)
    fg = lib.GetForegroundWindow()
    cur = kernel.GetCurrentThreadId()
    slot = ctypes.c_uint32()
    target = lib.GetWindowThreadProcessId(hwnd, ctypes.byref(slot))
    fg_thread = lib.GetWindowThreadProcessId(fg, ctypes.byref(slot)) if fg else 0
    if fg_thread and fg_thread != cur:
        lib.AttachThreadInput(cur, fg_thread, 1)
    if target and target != cur:
        lib.AttachThreadInput(cur, target, 1)
    lib.SetForegroundWindow(hwnd)
    if target and target != cur:
        lib.AttachThreadInput(cur, target, 0)
    if fg_thread and fg_thread != cur:
        lib.AttachThreadInput(cur, fg_thread, 0)
    root = int(lib.GetAncestor(hwnd, 2) or 0) or int(hwnd)
    now = lib.GetForegroundWindow()
    now_root = int(lib.GetAncestor(now, 2) or 0) or int(now or 0)
    if now_root != root:
        die("focus failed")


def await_window(title, seconds, missing):
    deadline = time.time() + seconds
    while time.time() < deadline:
        hwnd = find_window(title)
        if hwnd:
            return hwnd
        time.sleep(0.4)
    die(missing)


def look():
    mod = gui()
    return mod._remember_observation(desktop().observe({"recent_windows_expanded": 1}))


def elements():
    return list(gui()._LAST_OBS["action_index"].values())


def in_window(item, window):
    return window.casefold() in str(item.get("window_title") or "").casefold()


def first_write(window):
    roles = gui().WRITE_ROLES
    for item in elements():
        if item.get("role") in roles and in_window(item, window):
            return item
    return None


def find_named(name, window):
    needle = name.casefold()
    for item in elements():
        if str(item.get("name") or "").casefold() != needle:
            continue
        if in_window(item, window):
            return item
    return None


def find_contact(owner, window):
    roles = gui().WRITE_ROLES
    needle = owner.casefold()
    for item in elements():
        if item.get("role") in roles:
            continue
        if needle in str(item.get("name") or "").casefold() and in_window(item, window):
            return item
    return None


def click(item):
    desktop().click(item["short_id"])


def click_xy(x, y):
    lib = winuser()
    if not lib.SetCursorPos(int(x), int(y)):
        die("click failed")
    lib.mouse_event(0x0002, 0, 0, 0, 0)
    lib.mouse_event(0x0004, 0, 0, 0, 0)


def poll(seconds, fn):
    deadline = time.time() + seconds
    while time.time() < deadline:
        look()
        found = fn()
        if found is not None:
            return found
        time.sleep(0.4)
    return None


def texts(window):
    seen = []
    for item in elements():
        if not in_window(item, window):
            continue
        for key in ("text_full", "value", "name"):
            body = str(item.get(key) or "").strip()
            if body and body not in seen:
                seen.append(body)
    return seen


def wait_answer(question, before, window, seconds):
    deadline = time.time() + seconds
    seen = False
    stable = None
    count = 0
    asked = question.strip()
    while time.time() < deadline:
        look()
        now = texts(window)
        if any(item == asked or asked in item for item in now):
            seen = True
        novel = tuple(item for item in now if item not in before and item != asked)
        if seen and novel:
            if novel == stable:
                count += 1
            else:
                stable = novel
                count = 1
            if count >= 3:
                return "\n".join(novel)
        time.sleep(0.8)
    if not seen:
        die("send unwitnessed")
    die("answer unwitnessed")


def read_job():
    if not JOB.is_file():
        return None
    data = {}
    for line in JOB.read_text(encoding="utf-8").splitlines():
        key, sep, rest = line.partition(" ")
        if sep and key:
            data[key] = rest.strip()
    if "pid" not in data:
        return None
    return data


def write_job(pid, lease, op, stage, verdict, reason):
    body = (
        "pid " + str(pid) + "\n"
        + "lease " + lease + "\n"
        + "op " + op + "\n"
        + "stage " + stage + "\n"
        + "verdict " + verdict + "\n"
        + "reason " + " ".join((reason or "").split()) + "\n"
    )
    tmp = JOB.with_name(JOB.name + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, JOB)


def pid_alive(pid):
    if pid <= 0:
        return False
    lib = ctypes.WinDLL("kernel32", use_last_error=True)
    lib.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    lib.OpenProcess.restype = ctypes.c_void_p
    lib.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    lib.GetExitCodeProcess.restype = ctypes.c_int
    lib.CloseHandle.argtypes = [ctypes.c_void_p]
    lib.CloseHandle.restype = ctypes.c_int
    handle = lib.OpenProcess(0x1000, 0, int(pid))
    if not handle:
        return False
    code = ctypes.c_uint32(0)
    ok = lib.GetExitCodeProcess(handle, ctypes.byref(code))
    lib.CloseHandle(handle)
    return bool(ok) and code.value == 259


def job_running(data):
    if data.get("stage") not in ("execute", "witness"):
        return False
    try:
        pid = int(data.get("pid") or "0")
    except ValueError:
        return False
    if pid == os.getpid():
        return True
    return pid_alive(pid)


def begin_job(lease, op):
    current = read_job()
    if current and job_running(current):
        die("endgame job live")
    write_job(os.getpid(), lease, op, "execute", "", "")


def mark_job(stage):
    current = read_job()
    if not current or int(current.get("pid") or "0") != os.getpid():
        die("endgame job lost")
    write_job(os.getpid(), current.get("lease", ""), current.get("op", ""), stage, current.get("verdict", ""), current.get("reason", ""))


def end_job(verdict, reason):
    current = read_job()
    if not current or int(current.get("pid") or "0") != os.getpid():
        return
    write_job(os.getpid(), current.get("lease", ""), current.get("op", ""), "halt", verdict, reason)


def fail_job(reason):
    current = read_job()
    if not current or current.get("stage") == "halt":
        return
    try:
        pid = int(current.get("pid") or "0")
    except ValueError:
        return
    if pid != os.getpid():
        return
    write_job(pid, current.get("lease", ""), current.get("op", ""), "halt", "denied", reason)


def audio_peer():
    import node
    if node.cap_has("mic", "yes"):
        return None
    other = node.peer_with("mic", "yes")
    if not other or not other.get("addr"):
        die("audio peer absent")
    return other


def record_call(op, body):
    if op == "hangup":
        if not WAITING["on"]:
            die("no live call")
        HANGUP.set()
    text = "op " + op + "\n" + (body or "")
    if not text.endswith("\n"):
        text += "\n"
    tmp = AUDIO.with_name("audio.card.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, AUDIO)
    return op


def handoff(op, audio):
    if audio is None:
        return record_call(op, "")
    import node
    card = node.make_card("call", op, "", to=audio["id"])
    reply = node.transact(audio["addr"], card, 30)
    return reply.body


def end_call(end_name):
    mark_job("witness")
    button = poll(10, lambda: find_named(end_name, "Telegram"))
    if button is None:
        die("end control absent")
    click(button)
    deadline = time.time() + 15
    while time.time() < deadline:
        look()
        if find_named(end_name, "Telegram") is None:
            return
        time.sleep(0.4)
    die("end unwitnessed")


def call_body(owner, call_name, end_name, audio):
    desktop()
    hwnd = find_window("Telegram")
    if hwnd is None:
        subprocess.Popen([telegram_exe()], shell=False)
        hwnd = await_window("Telegram", 25, "telegram desktop absent")
    focus_hwnd(hwnd)
    edit = poll(15, lambda: first_write("Telegram"))
    if edit is None:
        die("call control absent")
    click(edit)
    desktop().paste_clipboard(owner)
    contact = poll(15, lambda: find_contact(owner, "Telegram"))
    if contact is None:
        die("owner contact absent")
    click(contact)
    button = poll(15, lambda: find_named(call_name, "Telegram"))
    if button is None:
        die("call control absent")
    click(button)
    mark_job("witness")
    witnessed = poll(20, lambda: find_named(end_name, "Telegram"))
    if witnessed is None:
        die("end control absent")
    HANGUP.clear()
    WAITING["on"] = True
    try:
        handoff("live", audio)
    except BaseException:
        WAITING["on"] = False
        end_call(end_name)
        handoff("restore", audio)
        raise
    HANGUP.wait()
    WAITING["on"] = False
    end_call(end_name)
    handoff("restore", audio)
    return "hangup"


def place_call(lease):
    if not interactive():
        die("desktop lease absent")
    if not telegram_exe():
        die("telegram desktop absent")
    owner, call_name, end_name = owner_config()
    if not owner or not call_name or not end_name:
        die("owner contact absent")
    audio = audio_peer()
    begin_job(lease, "telegram")
    try:
        result = call_body(owner, call_name, end_name, audio)
    except SystemExit as exc:
        fail_job(getattr(exc, "message", "") or "failed")
        raise
    except Exception as exc:
        fail_job(str(exc))
        raise
    end_job("deed_confirmed", "hangup")
    return result


def parse(body):
    fields = {"window": "", "url": "", "text": "", "browser": ""}
    for line in (body or "").splitlines():
        key, sep, rest = line.partition(" ")
        if sep and key in fields:
            fields[key] = rest.strip()
    return fields


def ask_body(window, url, text, browser):
    hand = desktop()
    if url:
        hand.open_url(browser or chrome_exe(), url)
    hwnd = await_window(window, 25, "window absent")
    focus_hwnd(hwnd)
    edit = poll(45, lambda: first_write(window))
    if edit is None:
        import vision
        try:
            x, y = vision.locate("the text input where a message is typed", window)
        except RuntimeError:
            die("input unwitnessed")
        except FileNotFoundError as exc:
            die(str(exc))
        click_xy(x, y)
        edit = poll(20, lambda: first_write(window))
        if edit is None:
            die("input unwitnessed")
    before = set(texts(window))
    click(edit)
    hand.paste_clipboard(text)
    hand.press_key("enter")
    mark_job("witness")
    return wait_answer(text, before, window, 120)


def place_ask(fields, lease):
    if not interactive():
        die("desktop lease absent")
    window = fields.get("window", "").strip()
    url = fields.get("url", "").strip()
    text = fields.get("text", "").strip()
    browser = fields.get("browser", "").strip()
    if not window:
        die("window absent")
    if not text:
        die("text absent")
    if url and not (browser or chrome_exe()):
        die("browser absent")
    begin_job(lease, "ask")
    try:
        result = ask_body(window, url, text, browser)
    except SystemExit as exc:
        fail_job(getattr(exc, "message", "") or "failed")
        raise
    except Exception as exc:
        fail_job(str(exc))
        raise
    end_job("deed_confirmed", "answer")
    return result


def take(argv, name):
    if name not in argv:
        return ""
    index = argv.index(name)
    if index + 1 >= len(argv):
        die("missing " + name)
    return argv[index + 1]


def main():
    argv = sys.argv[1:]
    if not argv:
        die("usage: desk.py caps|call|hangup|ask")
    cmd = argv[0]
    if cmd == "caps":
        owner, call_name, end_name = owner_config()
        lines = [
            "desktop " + ("yes" if interactive() else "no"),
            "telegram-desktop " + ("yes" if telegram_exe() else "no"),
            "chrome " + ("yes" if chrome_exe() else "no"),
            "owner " + ("yes" if owner else "no"),
            "call " + ("yes" if call_name else "no"),
            "end " + ("yes" if end_name else "no"),
        ]
        sys.stdout.write("\n".join(lines) + "\n")
        return
    if cmd == "call":
        sys.stdout.write(place_call("cli") + "\n")
        return
    if cmd == "hangup":
        import node
        if node.reachable("127.0.0.1:8765"):
            reply = node.transact("127.0.0.1:8765", node.make_card("call", "hangup", ""), 30)
            sys.stdout.write(reply.body + "\n")
            return
        sys.stdout.write(record_call("hangup", "") + "\n")
        return
    if cmd == "ask":
        fields = {
            "window": take(argv, "--window"),
            "url": take(argv, "--url"),
            "text": take(argv, "--text"),
            "browser": take(argv, "--browser"),
        }
        sys.stdout.write(place_ask(fields, "cli") + "\n")
        return
    die("usage: desk.py caps|call|hangup|ask")


if __name__ == "__main__":
    main()
