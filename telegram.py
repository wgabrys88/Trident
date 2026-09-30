import argparse
import ctypes
import io
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import wave
from ctypes import wintypes
from pathlib import Path

import hear
import mouth
import run

ROOT = Path(__file__).resolve().parent
ROLE_COMMUNICATIONS = 2
FLOW_CAPTURE = 1
DEVICE_ACTIVE = 1
CLSCTX_ALL = 23
VT_LPWSTR = 31
MIN_WINDOW = 2500


class CallError(Exception):
    pass


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", ctypes.c_ulong)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("r1", ctypes.c_ushort),
        ("r2", ctypes.c_ushort),
        ("r3", ctypes.c_ushort),
        ("data", ctypes.c_ubyte * 16),
    ]


def guid(text):
    raw = text.replace("-", "").replace("{", "").replace("}", "")
    if len(raw) != 32:
        raise CallError("bad guid")
    value = GUID()
    value.Data1 = int(raw[0:8], 16)
    value.Data2 = int(raw[8:12], 16)
    value.Data3 = int(raw[12:16], 16)
    for index, byte in enumerate(bytes.fromhex(raw[16:])):
        value.Data4[index] = byte
    return value


CLSID_ENUM = guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_ENUM = guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
CLSID_POLICY = guid("870af99c-171d-4f9e-af0d-e63df40c2bc9")
IID_POLICY = guid("f8679f50-850a-41cf-9c72-430f290290c8")
PKEY_NAME = PROPERTYKEY()
PKEY_NAME.fmtid = guid("a45c254e-df1c-4efd-8020-67d146a850e0")
PKEY_NAME.pid = 14

ole32 = ctypes.WinDLL("ole32", use_last_error=True)
ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
ole32.CoInitializeEx.restype = ctypes.c_long
ole32.CoCreateInstance.argtypes = [
    ctypes.POINTER(GUID),
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.POINTER(GUID),
    ctypes.POINTER(ctypes.c_void_p),
]
ole32.CoCreateInstance.restype = ctypes.c_long
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
ole32.CoTaskMemFree.restype = None
ole32.PropVariantClear.argtypes = [ctypes.POINTER(PROPVARIANT)]
ole32.PropVariantClear.restype = ctypes.c_long

user32 = ctypes.WinDLL("user32", use_last_error=True)
ENUM_PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
user32.EnumWindows.argtypes = [ENUM_PROC, ctypes.c_void_p]
user32.EnumWindows.restype = ctypes.c_int
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.IsWindowVisible.restype = ctypes.c_int
user32.IsIconic.argtypes = [ctypes.c_void_p]
user32.IsIconic.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = ctypes.c_int
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = ctypes.c_uint32

PLAYER = "\n".join(
    (
        "import sys, time",
        "from pathlib import Path",
        "import mouth",
        "go1, go2, done1, done2, wav, device = sys.argv[1:7]",
        "while not Path(go1).is_file():",
        "    time.sleep(0.05)",
        "mouth.play_wav(wav, device)",
        "Path(done1).write_bytes(b'')",
        "while not Path(go2).is_file():",
        "    time.sleep(0.05)",
        "mouth.play_wav(wav, device)",
        "Path(done2).write_bytes(b'')",
    )
)


def die(message):
    print(message, file=sys.stderr)
    raise SystemExit(2)


def com_hr(hr, message):
    if hr < 0:
        raise CallError(message)


def com_init():
    hr = ole32.CoInitializeEx(None, 0)
    if hr < 0 and (hr & 0xFFFFFFFF) != 0x80010106:
        raise CallError("audio device absent: communications mic")


def com_call(ptr, slot, proto, *args):
    table = ctypes.cast(ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    return ctypes.cast(table[slot], proto)(ptr, *args)


def release(ptr):
    if not ptr:
        return
    com_call(ptr, 2, ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p))


def create(clsid, iid, message):
    out = ctypes.c_void_p()
    com_hr(
        ole32.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_ALL, ctypes.byref(iid), ctypes.byref(out)),
        message,
    )
    if not out.value:
        raise CallError(message)
    return out


def prop_wstr(value):
    if value.vt != VT_LPWSTR:
        return ""
    ptr = ctypes.cast(ctypes.addressof(value.data), ctypes.POINTER(ctypes.c_void_p))[0]
    if not ptr:
        return ""
    return ctypes.wstring_at(ptr)


def device_id(device):
    out = ctypes.c_void_p()
    com_hr(
        com_call(
            device,
            5,
            ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
            ctypes.byref(out),
        ),
        "audio device absent: communications mic",
    )
    if not out.value:
        raise CallError("audio device absent: communications mic")
    text = ctypes.wstring_at(out.value)
    ole32.CoTaskMemFree(out)
    if not text:
        raise CallError("audio device absent: communications mic")
    return text


def friendly_name(device):
    store = ctypes.c_void_p()
    com_hr(
        com_call(
            device,
            4,
            ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)),
            0,
            ctypes.byref(store),
        ),
        "audio device absent: communications mic",
    )
    value = PROPVARIANT()
    try:
        com_hr(
            com_call(
                store,
                5,
                ctypes.WINFUNCTYPE(
                    ctypes.c_long,
                    ctypes.c_void_p,
                    ctypes.POINTER(PROPERTYKEY),
                    ctypes.POINTER(PROPVARIANT),
                ),
                ctypes.byref(PKEY_NAME),
                ctypes.byref(value),
            ),
            "audio device absent: communications mic",
        )
        return prop_wstr(value)
    finally:
        ole32.PropVariantClear(ctypes.byref(value))
        release(store)


def enumerator():
    return create(CLSID_ENUM, IID_ENUM, "audio device absent: communications mic")


def default_capture_id():
    enum = enumerator()
    device = ctypes.c_void_p()
    try:
        com_hr(
            com_call(
                enum,
                4,
                ctypes.WINFUNCTYPE(
                    ctypes.c_long,
                    ctypes.c_void_p,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_void_p),
                ),
                FLOW_CAPTURE,
                ROLE_COMMUNICATIONS,
                ctypes.byref(device),
            ),
            "audio device absent: communications mic",
        )
        if not device.value:
            raise CallError("audio device absent: communications mic")
        return device_id(device)
    finally:
        release(device)
        release(enum)


def capture_id_named(name):
    enum = enumerator()
    collection = ctypes.c_void_p()
    try:
        com_hr(
            com_call(
                enum,
                3,
                ctypes.WINFUNCTYPE(
                    ctypes.c_long,
                    ctypes.c_void_p,
                    ctypes.c_int,
                    ctypes.c_uint32,
                    ctypes.POINTER(ctypes.c_void_p),
                ),
                FLOW_CAPTURE,
                DEVICE_ACTIVE,
                ctypes.byref(collection),
            ),
            "audio device absent: " + name,
        )
        count = ctypes.c_uint()
        com_hr(
            com_call(
                collection,
                3,
                ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)),
                ctypes.byref(count),
            ),
            "audio device absent: " + name,
        )
        for index in range(count.value):
            item = ctypes.c_void_p()
            com_hr(
                com_call(
                    collection,
                    4,
                    ctypes.WINFUNCTYPE(
                        ctypes.c_long,
                        ctypes.c_void_p,
                        ctypes.c_uint,
                        ctypes.POINTER(ctypes.c_void_p),
                    ),
                    index,
                    ctypes.byref(item),
                ),
                "audio device absent: " + name,
            )
            try:
                if friendly_name(item) == name:
                    return device_id(item)
            finally:
                release(item)
    finally:
        release(collection)
        release(enum)
    raise CallError("audio device absent: " + name)


def set_default(device, role):
    policy = create(CLSID_POLICY, IID_POLICY, "audio device absent: communications mic")
    try:
        com_hr(
            com_call(
                policy,
                13,
                ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int),
                device,
                role,
            ),
            "audio device absent: communications mic",
        )
    finally:
        release(policy)


def read_card():
    path = ROOT / "call.txt"
    if not path.is_file():
        raise CallError("missing call.txt")
    data = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split(" ", 1)
        data[parts[0]] = parts[1].strip() if len(parts) == 2 else ""
    return data


def read_lease():
    desktop = ""
    audio = ""
    path = ROOT / "call.lease.txt"
    if not path.is_file():
        return desktop, audio
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split(" ", 1)
        if len(parts) != 2:
            continue
        if parts[0] == "desktop":
            desktop = parts[1].strip()
        elif parts[0] == "audio-route":
            audio = parts[1].strip()
    return desktop, audio


def write_audio(state):
    desktop, _audio = read_lease()
    lines = []
    if desktop:
        lines.append("desktop " + desktop)
    lines.append("audio-route " + state)
    (ROOT / "call.lease.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_job():
    path = ROOT / "call.job.txt"
    if not path.is_file():
        return None
    data = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[0] in ("id", "contact"):
            data[parts[0]] = parts[1].strip()
    return data


def visible_windows():
    found = []
    seen = set()

    def callback(hwnd, _param):
        key = int(hwnd or 0)
        if key in seen or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0 or width * height < MIN_WINDOW:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        seen.add(key)
        found.append((key, int(pid.value)))
        return True

    user32.EnumWindows(ENUM_PROC(callback), 0)
    return found


def telegram_windows():
    hits = []
    for hwnd, pid in visible_windows():
        image = run.process_image(pid)
        if image and Path(image).name.lower() == "telegram.exe":
            hits.append((hwnd, pid))
    return hits


def focused_telegram():
    hits = telegram_windows()
    if not hits:
        return None, False
    foreground = int(user32.GetForegroundWindow() or 0)
    for hwnd, pid in hits:
        if hwnd == foreground:
            return pid, True
    return hits[0][1], False


def node_up():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.4)
    try:
        sock.connect(("127.0.0.1", 8765))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def stimulus():
    found = sorted(ROOT.glob("*_chatterbox_out_*.wav"))
    if not found:
        raise CallError("mouth wav absent")
    return found[-1]


def wav_seconds(path):
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        frames = handle.getnframes()
    if rate < 8000 or frames <= 0:
        raise CallError("mouth wav: " + path.name)
    return frames / float(rate)


def guarded(fn):
    buf = io.StringIO()
    old = sys.stderr
    sys.stderr = buf
    try:
        return fn()
    except SystemExit:
        text = [line for line in buf.getvalue().splitlines() if line.strip()]
        raise CallError(text[-1] if text else "call failed")
    except hear.EarError as exc:
        raise CallError(str(exc))
    finally:
        sys.stderr = old


def release_vad():
    pid = run.read_pid("vad.pid")
    if pid and run.process_image(pid) and hear.VAD_PROC is None:
        (ROOT / "vad.stop").write_bytes(b"")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and run.process_image(pid):
            time.sleep(0.05)
        if run.process_image(pid):
            raise CallError("vad resident")


def note_capture(label, path, lines, blockers):
    kind, ms = hear.judge_wav(path)
    with wave.open(str(path), "rb") as handle:
        peak = hear.pcm_peak(handle.readframes(handle.getnframes()), handle.getsampwidth())
    lines.append(label + " peak " + str(peak) + " " + kind + " " + str(ms) + "ms")
    if kind != "keep":
        blockers.append("audio device absent: " + label + " silent")
        return
    text, lang = guarded(lambda: hear.transcribe(path))
    lines.append(label + " hear " + lang + " " + text)


def prove_cable(wav, mouth_name, mic_name, lines, blockers):
    seconds = wav_seconds(wav)
    guarded(lambda: hear.start_vad(mic_name))
    try:
        guarded(lambda: mouth.play_wav(wav, mouth_name))
        heard = guarded(lambda: hear.take_utterance(seconds + 8))
        note_capture("cable", heard, lines, blockers)
    finally:
        hear.stop_vad()


def wait_file(path, proc, err_path, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.is_file():
            return
        if proc.poll() is not None:
            text = err_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            raise CallError(text[-1] if text else "audio device absent: loopback")
        time.sleep(0.05)
    raise CallError("audio device absent: loopback")


def prove_loopback(wav, mouth_name, lines, blockers):
    seconds = wav_seconds(wav)
    tmp = Path(tempfile.mkdtemp(prefix="trident-call-"))
    go1 = tmp / "go1"
    go2 = tmp / "go2"
    done1 = tmp / "done1"
    done2 = tmp / "done2"
    err_path = tmp / "player.err"
    err = err_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            run.venv_python(),
            "-c",
            PLAYER,
            str(go1),
            str(go2),
            str(done1),
            str(done2),
            str(wav),
            mouth_name,
        ],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=err,
        stderr=subprocess.STDOUT,
    )
    try:
        guarded(lambda: hear.start_vad("loopback:" + str(proc.pid)))
        try:
            hear.set_hold(True)
            go1.write_bytes(b"")
            wait_file(done1, proc, err_path, seconds + 30)
            time.sleep(1.2)
            if (ROOT / "vad.utterance.txt").is_file():
                blockers.append("own mic hold failed")
                hear.remove_file("vad.utterance.txt")
            hear.set_hold(False)
            go2.write_bytes(b"")
            heard = guarded(lambda: hear.take_utterance(seconds + 8))
            wait_file(done2, proc, err_path, 5)
            note_capture("loopback", heard, lines, blockers)
        finally:
            hear.set_hold(False)
            hear.stop_vad()
    finally:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        err.close()
        shutil.rmtree(tmp, ignore_errors=True)


def probe_policy(mic_name, lines, blockers):
    saved = default_capture_id()
    cable = capture_id_named(mic_name)
    try:
        set_default(cable, ROLE_COMMUNICATIONS)
        if default_capture_id() != cable:
            blockers.append("audio device absent: communications mic")
        else:
            lines.append("communications mic " + mic_name)
    finally:
        set_default(saved, ROLE_COMMUNICATIONS)
        if default_capture_id() != saved:
            blockers.append("audio device absent: communications mic")


def measure(mouth_name, mic_name, lines, blockers):
    try:
        probe_policy(mic_name, lines, blockers)
    except CallError as exc:
        blockers.append(str(exc))
    try:
        wav = stimulus()
        lines.append("stimulus " + wav.name)
        release_vad()
        prove_cable(wav, mouth_name, mic_name, lines, blockers)
    except CallError as exc:
        blockers.append(str(exc))
        hear.stop_vad()
    try:
        wav = stimulus()
        release_vad()
        prove_loopback(wav, mouth_name, lines, blockers)
    except CallError as exc:
        blockers.append(str(exc))
        hear.stop_vad()


def own_mic(lines, blockers):
    try:
        lines.append("own-mic closed " + guarded(hear.wasapi_capture_name))
    except CallError as exc:
        blockers.append(str(exc))


def gates(card, lines, blockers):
    _pid, focused = focused_telegram()
    if _pid is None:
        blockers.append("Telegram desktop absent")
    elif not focused:
        blockers.append("Telegram desktop not focused")
    job = read_job()
    if job is None:
        blockers.append("call job absent")
    contact = ""
    if job is not None and job.get("contact"):
        contact = job["contact"]
    else:
        contact = card.get("call.contact", "")
    if not contact.strip():
        blockers.append("owner contact config absent")
    desktop, _audio = read_lease()
    if not desktop:
        blockers.append("Endgame lease absent")
    elif desktop != "running":
        blockers.append("Endgame lease " + desktop)
    if not node_up():
        blockers.append("node absent")
    lines.append("call.contact " + (contact.strip() or "empty"))


def peer_args():
    url = os.environ.get("TRIDENT_PEER", "").strip()
    return argparse.Namespace(
        nvidia=bool(url),
        url=url or None,
        model="nano",
        timeout=180.0,
        once=False,
        image=None,
    )


def still_open():
    pid, _focused = focused_telegram()
    if pid is None:
        return False
    if read_job() is None:
        return False
    desktop, _audio = read_lease()
    return desktop == "running"


def turns(pid):
    import assistant

    args = peer_args()
    py = run.venv_python()
    guarded(lambda: hear.start_vad("loopback:" + str(pid)))
    try:
        while still_open():
            if hear.VAD_PROC is None or hear.VAD_PROC.poll() is not None:
                raise CallError("vad exited")
            if (ROOT / "vad.utterance.txt").is_file():
                wav = hear.take_utterance()
                kind, _ms = hear.judge_wav(wav)
                if kind != "keep":
                    continue
                text, lang = hear.transcribe(wav)
                if not assistant.turn_after_transcript(py, args, text, lang, True):
                    return
            elif not assistant.drain_seat(py, args, True):
                return
            else:
                time.sleep(0.05)
    finally:
        hear.stop_vad()


def arm(mouth_name, mic_name):
    saved = default_capture_id()
    cable = capture_id_named(mic_name)
    (ROOT / "call.restore.txt").write_text(saved + "\n", encoding="utf-8")
    set_default(cable, ROLE_COMMUNICATIONS)
    if default_capture_id() != cable:
        restore_route()
        raise CallError("audio device absent: communications mic")
    (ROOT / "mouth.out.txt").write_text(mouth_name + "\n", encoding="utf-8")
    write_audio("running")


def restore_route():
    path = ROOT / "call.restore.txt"
    if not path.is_file():
        raise CallError("audio route restore absent")
    saved = path.read_text(encoding="utf-8").strip()
    if not saved:
        raise CallError("audio route restore absent")
    set_default(saved, ROLE_COMMUNICATIONS)
    if default_capture_id() != saved:
        raise CallError("audio device absent: communications mic")
    path.unlink()
    out = ROOT / "mouth.out.txt"
    if out.is_file():
        out.unlink()


def hangup():
    com_init()
    restore_route()
    hear.stop_vad()
    write_audio("completed")
    print("audio-route completed")


def write_fact(lines):
    text = "\n".join(lines) + "\n"
    (ROOT / "call.fact.txt").write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    sys.stdout.flush()


def accept():
    write_audio("queued")
    com_init()
    lines = []
    blockers = []
    armed = False
    try:
        card = read_card()
        mouth_name = card.get("call.mouth", "")
        mic_name = card.get("call.telegram-mic", "")
        if not mouth_name:
            blockers.append("audio device absent: call.mouth")
        if not mic_name:
            blockers.append("audio device absent: call.telegram-mic")
        if mouth_name and mic_name:
            measure(mouth_name, mic_name, lines, blockers)
        own_mic(lines, blockers)
        gates(card, lines, blockers)
        if blockers:
            return
        arm(mouth_name, mic_name)
        armed = True
        pid, focused = focused_telegram()
        if pid is None or not focused:
            raise CallError("Telegram desktop absent")
        turns(pid)
        restore_route()
        hear.stop_vad()
        write_audio("completed")
        armed = False
        lines.append("audio-route completed")
    except CallError as exc:
        blockers.append(str(exc))
    finally:
        if armed:
            try:
                restore_route()
            except CallError as exc:
                blockers.append(str(exc))
            hear.stop_vad()
        if blockers:
            write_audio("failed")
            lines.extend(blockers)
            lines.append("audio-route failed")
        write_fact(lines)
        if blockers:
            raise SystemExit(2)


def main():
    argv = sys.argv[1:]
    if argv == ["hangup"]:
        try:
            hangup()
        except CallError as exc:
            die(str(exc))
        return
    if argv:
        die("usage: telegram.py [hangup]")
    accept()


if __name__ == "__main__":
    main()
