import base64
import ctypes
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import namedtuple
from pathlib import Path

import win32

ROOT = Path(__file__).resolve().parent
PORT = 8765
Q = '<|"|>'
MEDIA = "<__media__>"
OPS = {
    "node": ("hello",),
    "call": ("dial", "hang", "wait"),
}
PROFILES = {
    "voice": {
        "memory": "gemma.memory.txt",
        "tools": ("ring", "hang", "look", "act", "run", "remember", "quiet"),
    },
    "eyes": {
        "memory": "gemma.eyes.txt",
        "tools": (),
    },
    "area": {
        "memory": "gemma.area.txt",
        "tools": (),
    },
}
HANDS = False
REGION = None
SYSTEM = (
    "You are Gemma, the organism on Wojciech's computer. "
    "Think one or two short sentences, then either one tool call or the words he should hear. "
    "He never hears the thought. A tool call is how you act. Do not write a tool name as speech. "
    "Call down: he cannot hear you. The computer microphone is closed. Cable speech is the room. A written line is Wojciech. "
    "If the call is down, nothing new has happened, and no remembered line starts with quiet, the action is ring. "
    "If you already rang and nothing new happened, do not ring again. "
    "If a remembered line starts with quiet, do not ring until he speaks. "
    "Call up: the user line is Wojciech. Speak one or two short sentences in his language, or act with a tool. "
    "Area and Eyes are other members on this same computer. Each has her own memory. "
    "A place is box_2d: y, x, y, x. Numbers run from 0 to 1000. y is down from the top. x is across. "
    "Area marks the work with one box_2d. Later pictures use that same grid and black out everything outside the box. "
    "Eyes names things with box_2d. "
    "If the work is not on screen yet, use run, then look. "
    "In the thought, judge the miss. A larger miss is a larger number. If the miss shrank, use a smaller number. "
    "One correction, then look again. Up is toward the top. "
    "move 200 right, move 200 left, move 200 up, and move 200 down shift the pointer by that many on the grid. "
    "click y x, right y x, and drag y x y x use that same grid. "
    "wheel n, key name, type text. down name holds a key. up name lets that key up. "
    "To write a file, run Python or cmd, or open a program, use run. "
    "Before you move, click, or drag on your own, be on the call. "
    "If he asked, do the work. "
    "If he wants the call to end, the action is hang and you write no words. "
    "If you are confused or stuck, the action is ring."
)
EYES = (
    "You are Eyes. You see one picture. Black is outside the work. "
    "Reply with JSON only. Each thing is {\"box_2d\": [y, x, y, x], \"label\": \"name\"}. "
    "Numbers are 0 to 1000. y is down from the top. x is across. "
    "Only what the question asks about. No actions."
)
AREA = (
    "You are Area. You see one picture. "
    "Reply with one JSON object and nothing else: {\"box_2d\": [y, x, y, x], \"label\": \"work\"}. "
    "Numbers are 0 to 1000. y is down from the top. x is across. "
    "The box is the region the work is in."
)
TOOL_TEXT = {
    "ring": "Place the call. The next words you write, after this tool, are what he hears once he has answered. If he asked for a joke or a message, those words are that joke or message. If nothing is waiting, say you are up and ask if he wants anything.",
    "hang": "End the phone call now. Use this when he wants to stop, hang up, or says goodbye. Say nothing else.",
    "look": "See the work. Area and Eyes answer with box_2d. The grid is 0 to 1000, top then across.",
    "act": "One correction on that same grid. Opening a program is run.",
    "run": "Run one PowerShell command and return the output. Use this to write a file, run Python, run cmd, or open a program.",
    "remember": "Store one short fact that stays in later turns.",
    "quiet": "Stay quiet and do not ring until he speaks. line clear ends that.",
}
TOOL_LINE = {
    "ring": "A short greeting. The joke or the message belongs in the words you write after this tool.",
    "hang": "Leave empty.",
    "look": "What to notice, or empty.",
    "act": "move 200 right, move 200 left, move 200 up, move 200 down, click y x, right y x, drag y x y x, wheel n, key name, type text, down name, up name, or a short goal. Numbers are 0 to 1000, top then across.",
    "run": "The PowerShell command. Set-Content writes a file. python -c runs Python. cmd /c runs cmd.",
    "remember": "The fact.",
    "quiet": "Why to stay quiet.",
}
Card = namedtuple(
    "Card",
    "id frm to module op body agent profile room image resource",
)
SCHED = None
STOP = threading.Event()


def die(message):
    print(message, file=sys.stderr, flush=True)
    err = SystemExit(2)
    err.message = message
    raise err


def safe_token(text, stars=False):
    if stars and text == "*":
        return text
    if not text or any(ch not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_" for ch in text):
        die("bad token")
    return text


def node_id():
    path = ROOT / "node.id"
    try:
        text = path.read_text(encoding="ascii").strip()
    except OSError:
        text = ""
    if text:
        return safe_token(text)
    fresh = uuid.uuid4().hex
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
    except FileExistsError:
        return safe_token(path.read_text(encoding="ascii").strip())
    try:
        os.write(fd, (fresh + "\n").encode("ascii"))
    finally:
        os.close(fd)
    return fresh


def split_host(addr):
    host, sep, port = (addr or "").strip().rpartition(":")
    if sep != ":" or not host or not port.isdigit():
        die("bad address")
    number = int(port)
    if number < 1 or number > 65535:
        die("bad address")
    return host, number


def make_card(module, op, body="", image=None, to="*", profile="", agent="", room="", resource="", ident="", frm=""):
    if module not in OPS or op not in OPS[module]:
        die("unknown card " + module + " " + op)
    return Card(
        safe_token(ident or str(time.time_ns())),
        safe_token(frm or node_id()),
        safe_token(to, True),
        module,
        op,
        body or "",
        agent or "",
        profile or "",
        room or "",
        image,
        resource or "",
    )


def render(card):
    lines = [
        "id " + card.id,
        "from " + card.frm,
        "to " + card.to,
        "module " + card.module,
        "op " + card.op,
    ]
    if card.agent:
        lines.append("agent " + card.agent)
    if card.profile:
        lines.append("profile " + card.profile)
    if card.room:
        lines.append("room " + card.room)
    if card.resource:
        lines.append("resource " + card.resource)
    body = card.body or ""
    if body and not body.endswith("\n"):
        body += "\n"
    text = "\n".join(lines) + "\nbody <<\n" + body + "<<\n"
    if card.image is not None:
        image = card.image
        if image and not image.endswith("\n"):
            image += "\n"
        text += "image <<\n" + image + "<<\n"
    return text + ".\n"


def parse_card(raw):
    text = raw or ""
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    fields = {}
    blocks = {}
    index = 0
    saw = False
    while index < len(lines):
        line = lines[index]
        if line == "." and "body" in blocks:
            saw = True
            break
        stripped = line.rstrip(" \t")
        if stripped.endswith("<<") and stripped[:-2].strip():
            key = stripped[:-2].strip()
            index += 1
            buf = []
            while index < len(lines) and lines[index] != "<<":
                buf.append(lines[index])
                index += 1
            if index >= len(lines) or lines[index] != "<<":
                die("truncated card")
            index += 1
            blocks[key] = "\n".join(buf)
            continue
        if line == "":
            index += 1
            continue
        key, sep, rest = line.partition(" ")
        if sep != " " or not key:
            die("bad card line")
        fields[key] = rest.strip()
        index += 1
    if not saw:
        die("truncated card")
    module = fields.get("module", "")
    op = fields.get("op", "")
    if module not in OPS or op not in OPS[module]:
        die("unknown card " + module + " " + op)
    if "body" not in blocks:
        die("card missing body")
    image = blocks["image"] if "image" in blocks else None
    return Card(
        safe_token(fields.get("id", "")),
        safe_token(fields.get("from", "")),
        safe_token(fields.get("to", ""), True),
        module,
        op,
        blocks["body"],
        fields.get("agent", ""),
        fields.get("profile", ""),
        fields.get("room", ""),
        image,
        fields.get("resource", ""),
    )


def mark(root, card, state):
    base = Path(root) / "node.state"
    for name in ("queued", "running", "completed", "failed"):
        path = base / name / (card.id + ".card")
        if path.is_file():
            path.unlink()
    dest = base / state
    dest.mkdir(parents=True, exist_ok=True)
    (dest / (card.id + ".card")).write_text(render(card), encoding="utf-8")


def needs_of(card):
    if card.module == "call" and card.op == "hang":
        return ()
    if card.module == "call":
        return ("call",)
    return ("node",)


class Scheduler:
    def __init__(self, root):
        self.root = Path(root)
        self.cv = threading.Condition()
        self.owner = {}
        self.queue = []
        self.alive = True

    def start(self):
        threading.Thread(target=self.loop, daemon=True).start()

    def submit(self, card, fn):
        box = {"card": card, "fn": fn, "event": threading.Event(), "result": None, "error": None}
        with self.cv:
            self.queue.append(box)
            mark(self.root, card, "queued")
            self.cv.notify()
        return box

    def wait(self, box, timeout):
        if not box["event"].wait(timeout):
            die("card timed out " + box["card"].id)
        if box["error"] is not None:
            raise box["error"]
        return box["result"]

    def loop(self):
        while self.alive:
            with self.cv:
                pick = None
                for index, box in enumerate(self.queue):
                    needs = needs_of(box["card"])
                    if all(not self.owner.get(item) for item in needs):
                        pick = index
                        break
                if pick is None:
                    self.cv.wait(timeout=0.05)
                    continue
                box = self.queue.pop(pick)
                for item in needs_of(box["card"]):
                    self.owner[item] = box["card"].id
                mark(self.root, box["card"], "running")
            threading.Thread(target=self._run, args=(box,), daemon=True).start()

    def _run(self, box):
        try:
            box["result"] = box["fn"](box["card"])
            mark(self.root, box["card"], "completed")
        except SystemExit as exc:
            box["error"] = exc
            mark(self.root, box["card"], "failed")
        except Exception as exc:
            err = SystemExit(2)
            err.message = str(exc)
            box["error"] = err
            mark(self.root, box["card"], "failed")
        finally:
            with self.cv:
                for item, owner in list(self.owner.items()):
                    if owner == box["card"].id:
                        self.owner[item] = None
                self.cv.notify_all()
            box["event"].set()


def cuda_name():
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        die("cuda query timed out")
    except OSError:
        return ""
    if completed.returncode != 0:
        return ""
    lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    return lines[0] if lines else ""


def gemma_ready():
    return (ROOT / "gemma-brain.exe").is_file() and (ROOT / "gemma.gguf").is_file()


def caps_text():
    cuda = cuda_name()
    lines = [
        "cpu " + str(os.cpu_count() or 0),
        "cuda " + (cuda or "none"),
    ]
    if gemma_ready():
        lines.append("brain gemma")
        if (ROOT / "gemma-mmproj.gguf").is_file():
            lines.append("vision gemma")
    lines.append("playback " + ("yes" if (ROOT / "chatterbox.exe").is_file() else "no"))
    lines.append("mic no")
    lines.append("capture closed")
    for module, names in OPS.items():
        for op in names:
            lines.append("op " + module + "." + op)
    return "\n".join(lines) + "\n"


def park_gemma():
    import gemma

    gemma.cancel_preload()


def local_infer(prompt, image):
    import gemma

    if not gemma_ready():
        die("brain missing gemma")
    if image and MEDIA not in prompt:
        die("image prompt missing <__media__>")
    try:
        (ROOT / "gemma.lastprompt.txt").write_text(prompt, encoding="utf-8")
    except OSError:
        pass
    try:
        text = gemma.resident_generate(prompt, image or "", False)
    except SystemExit as exc:
        die(getattr(exc, "message", "") or "gemma failed")
    if not str(text or "").strip():
        die("gemma returned empty")
    return text


DESK_TOKENS = 560
GRID = 1000


def clip(text, limit=200):
    flat = " ".join((text or "").split())
    if len(flat) > limit:
        flat = flat[:limit].rstrip()
    return flat


def desktop_lease():
    lib = ctypes.WinDLL("user32", use_last_error=True)
    lib.OpenInputDesktop.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    lib.OpenInputDesktop.restype = ctypes.c_void_p
    lib.CloseDesktop.argtypes = [ctypes.c_void_p]
    lib.CloseDesktop.restype = ctypes.c_int
    handle = lib.OpenInputDesktop(0, 0, 1)
    if not handle:
        die("desktop lease absent")
    lib.CloseDesktop(handle)


class _HDR(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


def black_outside(rgb, wide, high, box):
    y0, x0, y1, x1 = box
    top = max(0, min(high, int(round(y0 / GRID * high))))
    bottom = max(0, min(high, int(round(y1 / GRID * high))))
    left = max(0, min(wide, int(round(x0 / GRID * wide))))
    right = max(0, min(wide, int(round(x1 / GRID * wide))))
    if top:
        rgb[:top, :, :] = 0
    if bottom < high:
        rgb[bottom:, :, :] = 0
    if left:
        rgb[:, :left, :] = 0
    if right < wide:
        rgb[:, right:, :] = 0


def png_rgb(wide, high, bgra, box=None):
    import struct
    import zlib
    import numpy as np

    pix = np.frombuffer(bgra, dtype=np.uint8).reshape(high, wide, 4)
    rgb = np.ascontiguousarray(np.flipud(pix)[:, :, [2, 1, 0]])
    if box:
        black_outside(rgb, wide, high, box)
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(high))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", wide, high, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b"")


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("flags", ctypes.c_uint32), ("hCursor", ctypes.c_void_p), ("pt", _POINT)]


class _ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", ctypes.c_int),
        ("xHotspot", ctypes.c_uint32),
        ("yHotspot", ctypes.c_uint32),
        ("hbmMask", ctypes.c_void_p),
        ("hbmColor", ctypes.c_void_p),
    ]


def stamp_cursor(user32, gdi32, hdc, left, top, src_w, src_h, wide, high):
    info = _CURSORINFO()
    info.cbSize = ctypes.sizeof(_CURSORINFO)
    user32.GetCursorInfo.argtypes = [ctypes.POINTER(_CURSORINFO)]
    user32.GetCursorInfo.restype = ctypes.c_int
    if not user32.GetCursorInfo(ctypes.byref(info)) or not info.hCursor or not (info.flags & 1):
        return
    if info.pt.x < left or info.pt.y < top or info.pt.x >= left + src_w or info.pt.y >= top + src_h:
        return
    icon = _ICONINFO()
    hotx, hoty = 0, 0
    user32.GetIconInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ICONINFO)]
    user32.GetIconInfo.restype = ctypes.c_int
    if user32.GetIconInfo(info.hCursor, ctypes.byref(icon)):
        hotx, hoty = icon.xHotspot, icon.yHotspot
        if icon.hbmMask:
            gdi32.DeleteObject(icon.hbmMask)
        if icon.hbmColor:
            gdi32.DeleteObject(icon.hbmColor)
    x = int((info.pt.x - left - hotx) * wide / max(1, src_w))
    y = int((info.pt.y - top - hoty) * high / max(1, src_h))
    user32.DrawIconEx.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
        ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint,
    ]
    user32.DrawIconEx.restype = ctypes.c_int
    user32.DrawIconEx(hdc, x, y, info.hCursor, 0, 0, 0, None, 3)


def desk_png(box=None):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.GetDC.restype = ctypes.c_void_p
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.ReleaseDC.restype = ctypes.c_int
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.SetStretchBltMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
    gdi32.SetStretchBltMode.restype = ctypes.c_int
    gdi32.StretchBlt.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint32,
    ]
    gdi32.StretchBlt.restype = ctypes.c_int
    gdi32.GetDIBits.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteObject.restype = ctypes.c_int
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.restype = ctypes.c_int
    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    if sw < 48 or sh < 48:
        die("vision absent")
    scale = ((DESK_TOKENS * 2304) / float(sw * sh)) ** 0.5
    wide = max(48, int(round(sw * scale / 48)) * 48)
    high = max(48, int(round(sh * scale / 48)) * 48)
    src = user32.GetDC(0)
    if not src:
        die("vision absent")
    mem = gdi32.CreateCompatibleDC(src)
    bmp = gdi32.CreateCompatibleBitmap(src, wide, high)
    old = gdi32.SelectObject(mem, bmp) if mem and bmp else None
    try:
        if mem:
            gdi32.SetStretchBltMode(mem, 4)
        if not mem or not bmp or not gdi32.StretchBlt(mem, 0, 0, wide, high, src, 0, 0, sw, sh, 0x00CC0020):
            die("vision absent")
        stamp_cursor(user32, gdi32, mem, 0, 0, sw, sh, wide, high)
        gdi32.SelectObject(mem, old)
        old = None
        hdr = _HDR()
        hdr.biSize = ctypes.sizeof(_HDR)
        hdr.biWidth = wide
        hdr.biHeight = high
        hdr.biPlanes = 1
        hdr.biBitCount = 32
        hdr.biCompression = 0
        hdr.biSizeImage = wide * high * 4
        buf = (ctypes.c_ubyte * (wide * high * 4))()
        if gdi32.GetDIBits(mem, bmp, 0, high, buf, ctypes.byref(hdr), 0) != high:
            die("vision absent")
        png = png_rgb(wide, high, bytes(buf), box)
    finally:
        if old:
            gdi32.SelectObject(mem, old)
        if bmp:
            gdi32.DeleteObject(bmp)
        if mem:
            gdi32.DeleteDC(mem)
        user32.ReleaseDC(0, src)
    if len(png) < 32:
        die("vision absent")
    return base64.b64encode(png).decode("ascii"), wide, high


def deliver(image_b64):
    mod = sys.modules.get("call")
    if mod is None:
        return
    live = getattr(mod, "LIVE", None)
    if live is None or getattr(live, "closed", True) or getattr(live, "client", None) is None or not getattr(live, "peer_id", 0):
        return
    mod.picture(base64.b64decode("".join((image_b64 or "").split())))


def window_titles():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    found = []

    def add(hwnd):
        buf = ctypes.create_unicode_buffer(300)
        if not hwnd or user32.GetWindowTextW(hwnd, buf, 300) <= 0:
            return
        title = " ".join(buf.value.split())
        if title and title not in found and len(found) < 8:
            found.append(title)

    add(user32.GetForegroundWindow())

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            add(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return found


def see_screen(goal="", work=False):
    desktop_lease()
    if work:
        return see_work(goal)
    image, _wide, _high = desk_png(None)
    return eyes_report(image, goal), image


def transact(addr, card, timeout):
    host, port = split_host(addr)
    if host not in {"127.0.0.1", "localhost"}:
        die("bad address")
    try:
        sock = socket.create_connection((host, port), timeout=min(5, timeout))
    except OSError as exc:
        die("node down " + str(exc))
    sock.settimeout(timeout)
    try:
        sock.sendall(render(card).encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
            if len(data) > 32_000_000:
                die("card too large")
    finally:
        sock.close()
    if not data:
        die("node down empty reply")
    reply = parse_card(data.decode("utf-8"))
    if reply.id != card.id:
        die("card id mismatch")
    if reply.body.startswith("err "):
        die(reply.body)
    return reply


def decl(name, description):
    return (
        "<|tool>declaration:"
        + name
        + "{description:"
        + Q
        + description
        + Q
        + ",parameters:{properties:{line:{description:"
        + Q
        + TOOL_LINE[name]
        + Q
        + ",type:"
        + Q
        + "STRING"
        + Q
        + "}},required:["
        + Q
        + "line"
        + Q
        + "],type:"
        + Q
        + "OBJECT"
        + Q
        + "}}<tool|>"
    )


def tool_decls(names):
    return "".join(decl(name, TOOL_TEXT[name]) for name in names)


def memory_file(profile, root=None):
    if profile not in PROFILES:
        die("unknown profile " + profile)
    return (ROOT if root is None else Path(root)) / PROFILES[profile]["memory"]


def parse_store(raw):
    facts, pairs = [], []
    pending = None
    lines = (raw or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    while index < len(lines):
        stripped = lines[index].rstrip(" \t")
        if stripped.endswith("<<") and stripped[:-2].strip() in ("fact", "user", "model"):
            key = stripped[:-2].strip()
            index += 1
            buf = []
            while index < len(lines) and lines[index] != "<<":
                buf.append(lines[index])
                index += 1
            if index < len(lines) and lines[index] == "<<":
                index += 1
            body = "\n".join(buf).strip()
            if key == "fact" and body:
                facts.append(body)
            elif key == "user":
                if pending is not None:
                    pairs.append((pending, ""))
                pending = body
            elif key == "model":
                if pending is None:
                    pending = ""
                pairs.append((pending, body))
                pending = None
            continue
        index += 1
    if pending is not None:
        pairs.append((pending, ""))
    return facts, pairs


def render_store(facts, pairs):
    parts = []
    for fact in facts:
        parts.append("fact <<\n" + fact + "\n<<\n")
    for user, model in pairs:
        parts.append("user <<\n" + user + "\n<<\n")
        parts.append("model <<\n" + model + "\n<<\n")
    return "".join(parts)


def with_memory(path, fn):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    deadline = time.time() + 10
    fd = None
    while time.time() < deadline:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            break
        except FileExistsError:
            time.sleep(0.02)
    if fd is None:
        die("memory busy " + path.name)
    try:
        return fn()
    finally:
        os.close(fd)
        lock.unlink()


def read_memory(path):
    if not path.is_file():
        return [], []
    return parse_store(path.read_text(encoding="utf-8"))


def write_memory(path, facts, pairs):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(render_store(facts, pairs).encode("utf-8"))
    os.replace(tmp, path)


def quote(text):
    return " ".join(str(text or "").replace(Q, "'").split())


def log_gemma(kind, text):
    line = "gemma: " + kind + " " + clip(text, 700)
    print(line, file=sys.stderr, flush=True)
    path = ROOT / "gemma.thought.txt"
    try:
        prev = path.read_text(encoding="utf-8") if path.is_file() else ""
        rows = [row for row in (prev + line + "\n").splitlines() if row][-80:]
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    except OSError:
        pass


def prompt_for(profile, question, suffix, root, written=False):
    path = memory_file(profile, root)
    facts, pairs = read_memory(path)
    system = {"eyes": EYES, "area": AREA}.get(profile, SYSTEM)
    head = (
        "<bos><|turn>system\n<|think|>\n"
        + system
        + tool_decls(PROFILES[profile]["tools"])
        + "<turn|>\n"
    )
    if profile == "voice":
        head += "Time " + time.strftime("%Y-%m-%d %H:%M") + "\n"
        head += "Call up\n" if call_flag() == "call up" else "Call down\n"
        if written:
            head += "Written by Wojciech.\n"
    if facts:
        head += "Remembered:\n" + "\n".join(facts) + "\n"
    shown = list(pairs[-6:])

    def build(items):
        body = [head]
        for user, model in items:
            body.append("<|turn>user\n" + user + "<turn|>\n")
            if model:
                body.append("<|turn>model\n" + model + "<turn|>\n")
        body.append("<|turn>user\n" + question.strip() + "<turn|>\n")
        body.append("<|turn>model\n" + suffix)
        return "".join(body)

    text = build(shown)
    while len(text) > 80000 and shown:
        shown = shown[1:]
        text = build(shown)
    if len(text) > 80000:
        die("prompt too long")
    return text


def answer_text(text):
    raw = text or ""
    raw = re.sub(r"```tool_code\b.*?```", " ", raw, flags=re.DOTALL)
    raw = re.sub(r"(?m)^[ \t]*tool_code\s*=.*?$", " ", raw)
    if "<|channel>" not in raw and "<channel|>" in raw:
        raw = raw.split("<channel|>")[-1]
    while "<|channel>" in raw:
        start = raw.find("<|channel>")
        end = raw.find("<channel|>", start)
        if end < 0:
            raw = raw[:start]
            break
        raw = raw[:start] + raw[end + len("<channel|>") :]
    cut = raw.find("<|tool_call>")
    if cut >= 0:
        raw = raw[:cut]
    for token in ("<turn|>", "<|turn>", "<bos>", "<eos>", "<|think|>", "<|tool_response>", "<tool_response|>", "<tool_call|>", "`"):
        raw = raw.replace(token, "")
    return " ".join(raw.split())


def tool_name(name):
    key = (name or "").strip().lower()
    if key == "stop":
        return "hang"
    if key in PROFILES["voice"]["tools"]:
        return key
    return ""


def args_of(body):
    args = {}
    for key, quoted, bare in re.findall(
        r"(?:<\|\"\|>)?(\w+)(?:<\|\"\|>)?\s*:\s*(?:<\|\"\|>(.*?)<\|\"\|>|([^,}]*))",
        body or "",
        re.DOTALL,
    ):
        args[key] = " ".join((quoted if quoted else bare).split())
    return args


def paren_args(body):
    body = body or ""
    if not body.strip():
        return {}
    match = re.search(
        r"line\s*=\s*(?:<\|\"\|>(.*?)<\|\"\|>|\"((?:\\.|[^\"\\])*)\"|'((?:\\.|[^'\\])*)')",
        body,
        re.DOTALL,
    )
    if not match:
        return None
    raw = next(group for group in match.groups() if group is not None)
    return {"line": " ".join(raw.replace('\\"', '"').replace("\\'", "'").split())}


def brace_call(raw):
    match = re.search(
        r"<\|tool_call>\s*call:([A-Za-z_]\w*)\s*\{(.*?)\}\s*(?:<tool_call\|>|<turn\|>)",
        raw,
        re.DOTALL,
    )
    if not match:
        match = re.search(
            r"call:([A-Za-z_]\w*)\s*\{(.*?)\}\s*(?:<tool_call\|>|<turn\|>|$)",
            (raw or "").strip(),
            re.DOTALL,
        )
    if not match or not tool_name(match.group(1)):
        return None
    return tool_name(match.group(1)), args_of(match.group(2)), match.group(0)


def paren_call(raw):
    match = re.search(
        r"(?:<\|tool_call>\s*)?call:([A-Za-z_]\w*)\s*\((.*?)\)\s*(?:<tool_call\|>|<turn\|>|$)",
        (raw or "").strip(),
        re.DOTALL,
    )
    if not match or not tool_name(match.group(1)):
        return None
    args = paren_args(match.group(2))
    if args is None:
        return None
    return tool_name(match.group(1)), args, match.group(0)


def code_call(raw):
    if "tool_code" not in (raw or ""):
        return None
    chunks = [item.group(1) for item in re.finditer(r"```tool_code\b[^\n]*\n(.*?)```", raw, re.DOTALL)]
    chunks.append(raw)
    for chunk in chunks:
        for name in ("stop",) + PROFILES["voice"]["tools"]:
            match = re.search(r"\b" + name + r"\s*\((.*?)\)", chunk, re.DOTALL)
            if not match:
                continue
            args = paren_args(match.group(1))
            if args is None:
                continue
            return tool_name(name), args, match.group(0)
    return None


def parse_tool_call(text):
    raw = text or ""
    found = brace_call(raw) or paren_call(raw) or code_call(raw)
    if found:
        return found
    spoken = answer_text(raw).strip()
    low = spoken.lower().strip(".")
    bare = {
        "stop": "hang",
        "stop the call": "hang",
        "hang": "hang",
        "hang up": "hang",
        "end the call": "hang",
    }
    if low in bare:
        return bare[low], {}, spoken
    quoted = re.search(r"[\"“](.+?)[\"”]", spoken)
    if low.startswith("ring") and quoted and quoted.group(1).strip():
        return "ring", {"line": quoted.group(1).strip()}, spoken
    return None


def is_stop(text):
    found = parse_tool_call(text or "")
    return bool(found) and found[0] == "hang"


def thought_of(text, opened):
    raw = ("<|channel>thought\n" + (text or "")) if opened else (text or "")
    parts = []
    while "<|channel>" in raw:
        start = raw.find("<|channel>")
        end = raw.find("<channel|>", start)
        if end < 0:
            body = raw[start + len("<|channel>") :]
            raw = raw[:start]
        else:
            body = raw[start + len("<|channel>") : end]
            raw = raw[:start] + raw[end + len("<channel|>") :]
        if body.startswith("thought"):
            body = body[len("thought") :]
        cut = body.find("<|tool_call>")
        if cut >= 0:
            body = body[:cut]
        body = " ".join(body.split())
        if body:
            parts.append(body)
    return " ".join(parts)


def tool_mark(name, args):
    parts = []
    for key in sorted(args):
        if args[key] != "":
            parts.append(key + ":" + Q + quote(args[key]) + Q)
    return "<|tool_call>call:" + name + "{" + ",".join(parts) + "}<tool_call|>"


def tool_response(name, text):
    return "<|tool_response>response:" + name + "{value:" + Q + quote(text) + Q + "}<tool_response|>"


def open_suffix(steps, image):
    parts = []
    for thought, name, args, result in steps:
        if thought:
            parts.append("<|channel>thought\n" + thought.strip() + "\n<channel|>")
        parts.append(tool_mark(name, args))
        parts.append(tool_response(name, result))
    if image:
        parts.append(MEDIA + "\n")
    if steps:
        parts.append("<|channel>thought\n")
    return "".join(parts)


def may_touch():
    return HANDS or call_flag() == "call up"


def quiet_set(root=None):
    facts, _pairs = read_memory(memory_file("voice", root))
    return any(item.lower().startswith("quiet") for item in facts)


def tool_remember(profile, args, root):
    line = clip(args.get("line", ""))
    if not line:
        return "empty fact"
    path = memory_file(profile, root)

    def run():
        facts, pairs = read_memory(path)
        if line not in facts:
            facts.append(line)
            write_memory(path, facts, pairs)

    with_memory(path, run)
    return line


def tool_quiet(profile, args, root):
    line = clip(args.get("line", "") or "until he speaks")
    path = memory_file(profile, root)

    def run():
        facts, pairs = read_memory(path)
        if line.lower() == "clear":
            facts = [item for item in facts if not item.lower().startswith("quiet")]
        else:
            fact = "quiet: " + line
            if fact not in facts:
                facts.append(fact)
        write_memory(path, facts, pairs)

    with_memory(path, run)
    return "quiet clear" if line.lower() == "clear" else "quiet: " + line


def tool_hang():
    import call

    live = getattr(call, "LIVE", None)
    if live is not None and (getattr(live, "up", False) or getattr(live, "calls", None) is not None or getattr(live, "phone", None) is not None):
        call.drop_call()
    return "hung"


def tool_ring(args):
    import call

    live = getattr(call, "LIVE", None)
    if live is not None and getattr(live, "up", False):
        return "up"
    if not HANDS and quiet_set():
        return "quiet"
    line = " ".join((args.get("line") or "").split()) or "I am up. Do you want anything?"
    if live is None or not call.armed():
        return "waiting"
    return call.dial(line)


def tool_look(line, work=None):
    titles = window_titles()
    listed = "; ".join(titles)
    ask = " ".join((line or "Where is what?").split())
    if listed:
        ask += " Open windows: " + listed
    if not (ROOT / "gemma-mmproj.gguf").is_file():
        return "windows " + listed, ""
    if work is None:
        work = may_touch()
    seen, image = see_screen(ask, work)
    deliver(image)
    return "windows " + listed + "\n" + seen, image


def pointer_grid():
    point = win32.pointer_pct()
    if not point:
        return None
    across, down = point
    return down / 100.0 * GRID, across / 100.0 * GRID


def pointer_line():
    point = pointer_grid()
    if not point:
        return "Pointer unknown"
    return "Pointer %d %d" % (round(point[0]), round(point[1]))


def parse_box(text):
    raw = text or ""
    start = raw.find("box_2d")
    chunk = raw[start + len("box_2d"):] if start >= 0 else raw
    found = [float(token) for token in re.findall(r"\d+(?:\.\d+)?", chunk)]
    found = [number for number in found if 0 <= number <= GRID]
    if len(found) < 4:
        return None
    y0, x0, y1, x1 = found[:4]
    if y1 < y0:
        y0, y1 = y1, y0
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 <= y0 or x1 <= x0:
        return None
    return y0, x0, y1, x1


def to_pct(y, x):
    return float(x) / GRID * 100.0, float(y) / GRID * 100.0


def member_say(profile, image, question):
    ask = " ".join((question or "").split()) or "Where is what?"
    if profile == "eyes":
        ask += "\n" + pointer_line()
    body = (MEDIA + "\n" + ask) if image else ask
    text = answer_text(local_infer(prompt_for(profile, body, "", None, False), image or ""))
    if not text:
        die("desk see absent")
    append_turn(profile, ask, text, None)
    log_gemma(profile, text)
    return text


def eyes_report(image, goal):
    seen = member_say("eyes", image, goal)
    point = pointer_grid()
    line = "pointer %d %d\n%s" % (round(point[0]), round(point[1]), seen) if point else seen
    if REGION:
        line = "box_2d %d %d %d %d\n" % tuple(int(round(number)) for number in REGION) + line
    return line


def see_work(goal):
    global REGION
    mark = ""
    if REGION is None:
        full, _wide, _high = desk_png(None)
        mark = member_say("area", full, "box_2d")
        REGION = parse_box(mark)
        if REGION is None:
            return "area " + mark, full
    image, _wide, _high = desk_png(REGION)
    report = eyes_report(image, goal)
    if mark:
        report = "area " + clip(mark, 140) + "\n" + report
    return report, image


def numbers(parts):
    try:
        return [float(part) for part in parts]
    except ValueError:
        return None


def grid_ok(point, count):
    return bool(point) and len(point) == count and all(0 <= n <= GRID for n in point[:count])


def parse_direct(line):
    text = " ".join((line or "").split())
    parts = text.split(" ")
    if not parts or not parts[0]:
        return None
    head = parts[0].lower()
    rest = [part[:-1] if part.endswith("%") else part for part in parts[1:]]
    if head == "move" and len(rest) == 2:
        if rest[1].lower() in ("up", "down", "left", "right"):
            point = numbers([rest[0]])
            if point and 0 <= point[0] <= GRID:
                return ("rel", rest[1].lower(), point[0])
        if rest[0].lower() in ("up", "down", "left", "right"):
            point = numbers([rest[1]])
            if point and 0 <= point[0] <= GRID:
                return ("rel", rest[0].lower(), point[0])
        point = numbers(rest)
        if grid_ok(point, 2):
            return ("move", point[0], point[1])
    elif head == "click":
        if len(rest) == 0:
            return ("click",)
        point = numbers(rest)
        if grid_ok(point, 2):
            return ("click", point[0], point[1])
    elif head == "right":
        if len(rest) == 0:
            return ("right",)
        point = numbers(rest)
        if grid_ok(point, 2):
            return ("right", point[0], point[1])
    elif head == "drag":
        point = numbers(rest)
        if grid_ok(point, 4):
            return ("drag",) + tuple(point)
    elif head == "wheel":
        point = numbers(rest)
        if point and len(point) == 1:
            return ("wheel", point[0])
        if point and len(point) == 3 and all(0 <= n <= GRID for n in point[:2]):
            return ("wheel", point[0], point[1], point[2])
    if head == "key" and len(rest) >= 1:
        return ("key", rest[0].lower())
    if head == "type" and len(parts) >= 2:
        return ("type", text.split(" ", 1)[1])
    if head in ("down", "up") and len(rest) >= 1 and rest[0].lower() in win32.VK_MAP:
        return (head, rest[0].lower())
    nums = []
    for token in re.findall(r"\d+(?:\.\d+)?", text):
        number = float(token)
        if 0 <= number <= GRID:
            nums.append(number)
    dirs = [part.lower() for part in parts if part.lower() in ("up", "down", "left", "right")]
    if len(dirs) == 1 and len(nums) == 1:
        return ("rel", dirs[0], nums[0])
    if len(nums) == 4:
        return ("drag", nums[0], nums[1], nums[2], nums[3])
    if len(nums) == 2:
        return ("click", nums[0], nums[1])
    return None


def landed(prefix):
    point = pointer_grid()
    if not point:
        return prefix
    return prefix + " -> %d %d" % (round(point[0]), round(point[1]))


def run_direct(cmd):
    kind = cmd[0]
    if kind == "rel":
        here = pointer_grid()
        if not here:
            return ""
        y, x = here
        if cmd[1] == "right":
            x += cmd[2]
        elif cmd[1] == "left":
            x -= cmd[2]
        elif cmd[1] == "down":
            y += cmd[2]
        else:
            y -= cmd[2]
        if not win32.move_pct(*to_pct(y, x)):
            return ""
        return landed("move %s %.0f" % (cmd[1], cmd[2]))
    if kind == "move":
        if not win32.move_pct(*to_pct(cmd[1], cmd[2])):
            return ""
        return landed("move %.0f %.0f" % (cmd[1], cmd[2]))
    if kind == "click":
        point = to_pct(cmd[1], cmd[2]) if len(cmd) == 3 else win32.pointer_pct()
        if not point or not win32.click_pct(point[0], point[1]):
            return ""
        return landed("click" if len(cmd) == 1 else "click %.0f %.0f" % (cmd[1], cmd[2]))
    if kind == "right":
        point = to_pct(cmd[1], cmd[2]) if len(cmd) == 3 else win32.pointer_pct()
        if not point or not win32.right_pct(point[0], point[1]):
            return ""
        return landed("right" if len(cmd) == 1 else "right %.0f %.0f" % (cmd[1], cmd[2]))
    if kind == "drag":
        start = to_pct(cmd[1], cmd[2])
        end = to_pct(cmd[3], cmd[4])
        if not win32.drag_pct(start[0], start[1], end[0], end[1]):
            return ""
        return landed("drag %.0f %.0f %.0f %.0f" % cmd[1:])
    if kind == "wheel":
        if len(cmd) == 2:
            point = win32.pointer_pct()
            notches = cmd[1]
            label = "wheel %.0f" % notches
        else:
            point = to_pct(cmd[1], cmd[2])
            notches = cmd[3]
            label = "wheel %.0f %.0f %.0f" % (cmd[1], cmd[2], cmd[3])
        if not point or not win32.wheel_pct(point[0], point[1], notches):
            return ""
        return landed(label)
    if kind == "key":
        if not win32.press(cmd[1]):
            return ""
        return "key " + cmd[1]
    if kind == "type":
        if not win32.type_text(cmd[1]):
            return ""
        return "type " + cmd[1]
    if kind == "down":
        if not win32.key_down(cmd[1]):
            return ""
        return "down " + cmd[1]
    if kind == "up":
        if not win32.key_up(cmd[1]):
            return ""
        return "up " + cmd[1]
    return ""


def note_last(line):
    path = memory_file("voice")

    def run():
        facts, pairs = read_memory(path)
        facts = [item for item in facts if not item.lower().startswith("last act:")]
        facts.append("last act: " + clip(line, 160))
        write_memory(path, facts, pairs)

    with_memory(path, run)


def act_direct(line):
    cmd = parse_direct(line)
    if cmd is None:
        return None
    done = run_direct(cmd)
    if done:
        note_last(done)
    return done


def one_step(goal, seen):
    ask = seen + "\nDo this: " + goal + "\nOne line only: drag y x y x, move 200 right, or click y x. Numbers are 0 to 1000, top then across."
    text = local_infer(prompt_for("voice", ask, "", None, False), "")
    found = parse_tool_call(text)
    if found and found[0] == "act":
        return found[1].get("line", "")
    spoken = answer_text(text)
    for chunk in re.split(r"[\n.]", spoken):
        line = " ".join(chunk.split())
        if parse_direct(line):
            return line
    return spoken


def shot_of():
    image, wide, high = desk_png(REGION)
    return image, wide, high


def tool_act(line):
    if not may_touch():
        return "The call is down. Ring him before you touch the desktop.", ""
    reason = win32.seat_fault()
    if reason:
        return "act stopped " + reason, ""
    text = " ".join((line or "").split())
    head = text.lower()
    if head.startswith(("run ", "start ", "start-process ", "powershell ", "python ", "cmd ", "set-content ")):
        return tool_run(text[4:].strip() if head.startswith("run ") else text), ""
    done = act_direct(text) if parse_direct(text) else None
    if done is not None:
        if not done:
            return "act stopped " + (win32.fault or "failed"), ""
        image, wide, high = shot_of()
        deliver(image)
        return "act " + done + " " + str(wide) + " " + str(high), image
    if not (ROOT / "gemma-mmproj.gguf").is_file():
        return "vision absent", ""
    if not text:
        text = "Look, then one correction."
    seen, image = see_screen("Where is the work?", True)
    step = one_step(text, seen)
    if not parse_direct(step):
        return "act unparsed. Use drag y x y x or move 200 right.\n" + clip(step, 80), image
    done = act_direct(step)
    if not done:
        return "act stopped " + (win32.fault or "failed"), image
    image, wide, high = shot_of()
    deliver(image)
    return "act " + done + " " + str(wide) + " " + str(high) + "\n" + seen, image


def shell_command(line):
    parts = " ".join((line or "").split()).split(" ")
    if len(parts) >= 3 and parts[0].lower() == "start-process" and not any(part.startswith("-") for part in parts[1:]):
        arg = " ".join(parts[2:]).replace("'", "''")
        return "Start-Process " + parts[1] + " -ArgumentList '" + arg + "'"
    return " ".join(parts)


def tool_run(line):
    global REGION
    if not may_touch():
        return "The call is down. Ring him before you run a command."
    command = shell_command(line)
    if not command:
        return "empty command"
    try:
        done = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=str(ROOT),
            capture_output=True,
            timeout=25,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        REGION = None
        return "command timed out"
    except OSError:
        return "command failed"
    out = ((done.stdout or b"") + b"\n" + (done.stderr or b"")).decode("utf-8", errors="replace")
    text = clip(out, 400)
    REGION = None
    if done.returncode and not text:
        return "exit " + str(done.returncode)
    if command.lower().startswith(("start-process", "start ")):
        time.sleep(1)
    return text or "ok"


def run_tool(profile, name, args, root):
    if name not in PROFILES[profile]["tools"]:
        return "unknown tool"
    if name == "remember":
        return tool_remember(profile, args, root)
    if name == "quiet":
        return tool_quiet(profile, args, root)
    if name == "look":
        return tool_look(args.get("line", ""))
    if name == "act":
        return tool_act(args.get("line", ""))
    if name == "run":
        return tool_run(args.get("line", ""))
    if name == "ring":
        return tool_ring(args)
    if name == "hang":
        return tool_hang()
    return "unknown tool"


def append_turn(profile, question, reply, root):
    path = memory_file(profile, root)
    spoken = answer_text(reply)
    if not spoken:
        return

    def run():
        facts, pairs = read_memory(path)
        pairs.append((clip(question, 400), clip(spoken, 400)))
        if len(pairs) > 12:
            pairs = pairs[-12:]
        write_memory(path, facts, pairs)

    with_memory(path, run)


def hear_line(question):
    prompt = (
        "<bos><|turn>user\nHe asked: "
        + question.strip()
        + "\nSay only what he should hear. If he asked for a joke, only the joke.<turn|>\n<|turn>model\n"
    )
    return answer_text(local_infer(prompt, ""))


def park_after(speech):
    mod = sys.modules.get("call")
    live = getattr(mod, "LIVE", None) if mod is not None else None
    if live is None or not getattr(live, "up", False):
        return
    speech = " ".join((speech or "").split())
    opening = " ".join((getattr(live, "opening", "") or "").split())
    if speech and speech != opening:
        live.after = speech


def agent_turn(profile, question, image_b64="", root=None, written=False, hands=False):
    global HANDS, REGION
    if profile not in PROFILES:
        die("unknown profile " + profile)
    if not (question or "").strip():
        die("empty question")
    HANDS = bool(hands) or call_flag() == "call up"
    REGION = None
    steps = []
    shot = image_b64 or ""
    while len(steps) < 36:
        opened = bool(steps)
        ask = question
        image = ""
        if shot and not opened:
            if MEDIA not in ask:
                ask = MEDIA + "\n" + ask
            image = shot
        elif shot:
            image = shot
        text = local_infer(prompt_for(profile, ask, open_suffix(steps, bool(shot) and opened), root, written), image)
        shot = ""
        if not str(text or "").strip():
            die("gemma returned empty")
        thought = thought_of(text, opened)
        if thought:
            log_gemma("thought", thought)
        log_gemma("turn", text)
        found = parse_tool_call("<|channel>thought\n" + text if opened else text)
        if not found:
            speech = answer_text("<|channel>thought\n" + text if opened else text)
            if not speech and steps:
                speech = " ".join((steps[-1][2].get("line") or "").split())
            if not speech:
                die("gemma returned empty")
            if any(item[1] == "ring" for item in steps):
                park_after(speech)
            append_turn(profile, question, speech, root)
            return speech
        name, args, _raw = found
        if name == "hang":
            tool_hang()
            log_gemma("tool", "hang")
            return "<|tool_call>call:hang{}<tool_call|>"
        result = run_tool(profile, name, args, root)
        if isinstance(result, tuple):
            result, shot = result
        log_gemma("tool", name + " " + quote(args.get("line", "")) + " -> " + clip(str(result), 160))
        steps.append((thought, name, args, str(result)))
        if name == "ring":
            if str(result) == "quiet":
                return "quiet"
            said = hear_line(question)
            log_gemma("hear", said)
            if said:
                park_after(said)
            append_turn(profile, question, said or quote(args.get("line", "")), root)
            return said or quote(args.get("line", ""))
    return "I am still on it."


def call_flag():
    mod = sys.modules.get("call")
    live = getattr(mod, "LIVE", None) if mod is not None else None
    if live is not None and getattr(live, "up", False):
        return "call up"
    return "call idle"


def handle(card):
    if card.module == "node" and card.op == "hello":
        return caps_text().rstrip("\n") + "\n" + call_flag() + "\n", None
    import call

    if card.op == "wait":
        return call.arm(), None
    if card.op == "dial":
        return call.dial((card.body or "").strip()), None
    if card.op == "hang":
        return call.hang((card.body or "").strip()), None
    die("unknown card " + card.module + " " + card.op)


def reply_of(card, body, image):
    return Card(card.id, node_id(), card.frm, card.module, card.op, body, "", "", card.room, image, "")


def execute(card, timeout):
    if SCHED is None:
        die("node is not serving")
    box = SCHED.submit(card, handle)
    body, image = SCHED.wait(box, timeout)
    return body, image


def serve_conn(conn):
    data = b""
    try:
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
            if len(data) > 32_000_000:
                die("card too large")
        if not data:
            return
        card = parse_card(data.decode("utf-8"))
        try:
            body, image = execute(card, 600)
            reply = reply_of(card, body, image)
        except SystemExit as exc:
            reply = reply_of(card, "err " + (getattr(exc, "message", "") or "failed"), None)
        conn.sendall(render(reply).encode("utf-8"))
    except SystemExit as exc:
        print(getattr(exc, "message", "") or "failed", file=sys.stderr, flush=True)
    finally:
        conn.close()


def write_pid():
    (ROOT / "node.pid").write_text(str(os.getpid()) + "\n", encoding="ascii")


def clear_pid():
    path = ROOT / "node.pid"
    try:
        text = path.read_text(encoding="ascii")
    except OSError:
        return
    if text.strip() == str(os.getpid()):
        try:
            path.unlink()
        except OSError:
            pass


def serve():
    global SCHED
    SCHED = Scheduler(ROOT)
    SCHED.start()
    write_pid()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        sock.bind(("127.0.0.1", PORT))
    except OSError as exc:
        clear_pid()
        die("cannot listen on 127.0.0.1:" + str(PORT) + ": " + str(exc))
    sock.listen(8)
    sock.settimeout(0.2)
    print("node: listening 127.0.0.1:" + str(PORT), file=sys.stderr, flush=True)
    try:
        while not STOP.is_set():
            try:
                conn, _addr = sock.accept()
            except socket.timeout:
                continue
            threading.Thread(target=serve_conn, args=(conn,), daemon=True).start()
    finally:
        sock.close()
        clear_pid()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:]
    if argv not in ([], ["--line"]):
        die("usage: node.py [--line]")
    line = argv == ["--line"]
    if line:
        import call

        call.arm()
    try:
        serve()
    except KeyboardInterrupt:
        print("node: stopped", file=sys.stderr, flush=True)
        raise SystemExit(0)
    finally:
        if line:
            import call

            call.close_session()


if __name__ == "__main__":
    main()
