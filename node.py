import base64
import ctypes
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import namedtuple
from pathlib import Path

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
        "tools": ("remember", "place", "desk", "ring", "next", "stop"),
    },
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


def vulkan_name():
    try:
        vk = __import__("ctypes").WinDLL("vulkan-1")
    except OSError:
        return ""
    ctypes_mod = __import__("ctypes")

    class VkApplicationInfo(ctypes_mod.Structure):
        _fields_ = [
            ("sType", ctypes_mod.c_uint32),
            ("pNext", ctypes_mod.c_void_p),
            ("pApplicationName", ctypes_mod.c_char_p),
            ("applicationVersion", ctypes_mod.c_uint32),
            ("pEngineName", ctypes_mod.c_char_p),
            ("engineVersion", ctypes_mod.c_uint32),
            ("apiVersion", ctypes_mod.c_uint32),
        ]

    class VkInstanceCreateInfo(ctypes_mod.Structure):
        _fields_ = [
            ("sType", ctypes_mod.c_uint32),
            ("pNext", ctypes_mod.c_void_p),
            ("flags", ctypes_mod.c_uint32),
            ("pApplicationInfo", ctypes_mod.POINTER(VkApplicationInfo)),
            ("enabledLayerCount", ctypes_mod.c_uint32),
            ("ppEnabledLayerNames", ctypes_mod.c_void_p),
            ("enabledExtensionCount", ctypes_mod.c_uint32),
            ("ppEnabledExtensionNames", ctypes_mod.c_void_p),
        ]

    vk.vkCreateInstance.argtypes = [ctypes_mod.POINTER(VkInstanceCreateInfo), ctypes_mod.c_void_p, ctypes_mod.POINTER(ctypes_mod.c_void_p)]
    vk.vkCreateInstance.restype = ctypes_mod.c_int
    vk.vkEnumeratePhysicalDevices.argtypes = [ctypes_mod.c_void_p, ctypes_mod.POINTER(ctypes_mod.c_uint32), ctypes_mod.c_void_p]
    vk.vkEnumeratePhysicalDevices.restype = ctypes_mod.c_int
    vk.vkGetPhysicalDeviceProperties.argtypes = [ctypes_mod.c_void_p, ctypes_mod.c_void_p]
    vk.vkGetPhysicalDeviceProperties.restype = None
    vk.vkDestroyInstance.argtypes = [ctypes_mod.c_void_p, ctypes_mod.c_void_p]
    vk.vkDestroyInstance.restype = None
    app = VkApplicationInfo()
    app.sType = 0
    app.apiVersion = (1 << 22) | (2 << 12)
    info = VkInstanceCreateInfo()
    info.sType = 1
    info.pApplicationInfo = ctypes_mod.pointer(app)
    inst = ctypes_mod.c_void_p()
    if vk.vkCreateInstance(ctypes_mod.byref(info), None, ctypes_mod.byref(inst)) != 0 or not inst.value:
        return ""
    try:
        count = ctypes_mod.c_uint32(0)
        if vk.vkEnumeratePhysicalDevices(inst, ctypes_mod.byref(count), None) != 0 or count.value < 1:
            return ""
        arr = (ctypes_mod.c_void_p * count.value)()
        if vk.vkEnumeratePhysicalDevices(inst, ctypes_mod.byref(count), ctypes_mod.cast(arr, ctypes_mod.c_void_p)) != 0:
            return ""
        props = (ctypes_mod.c_ubyte * 4096)()
        vk.vkGetPhysicalDeviceProperties(arr[0], ctypes_mod.cast(props, ctypes_mod.c_void_p))
        raw = bytes(props[20:276]).split(b"\x00", 1)[0]
        return raw.decode("utf-8", errors="replace").strip()
    finally:
        vk.vkDestroyInstance(inst, None)


def gemma_ready():
    return (ROOT / "gemma-brain.exe").is_file() and (ROOT / "gemma.gguf").is_file()


def caps_text():
    cuda = cuda_name()
    lines = [
        "cpu " + str(os.cpu_count() or 0),
        "cuda " + (cuda or "none"),
        "vulkan " + (vulkan_name() or "none"),
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


def park_mouth():
    import mouth

    if mouth.chatterbox_running_any():
        mouth.stop_resident()


def park_gemma():
    import gemma

    gemma.cancel_preload()


def local_infer(prompt, image):
    import gemma

    if not gemma_ready():
        die("brain missing gemma")
    if image and MEDIA not in prompt:
        die("image prompt missing <__media__>")
    park_mouth()
    try:
        text = gemma.resident_generate(prompt, image or "", False)
    except SystemExit as exc:
        die(getattr(exc, "message", "") or "gemma failed")
    if not str(text or "").strip():
        die("gemma returned empty")
    return text


DESK_TOKENS = 560
DESK_SYSTEM = (
    "You see one full desktop screenshot. "
    "Answer with one JSON object and no other text. "
    "see is one sentence about the whole screen and is never empty. "
    "do is click, type, key, wait, or done. "
    "click includes box_2d as y0, x0, y1, x1, each a number from 0 to 1000. "
    "type includes text and an optional key. "
    "key is enter, backspace, ctrl-a, or ctrl-l. "
    "done is a look with no action."
)
DESK_KEYS = {
    "enter": ((0x0D, 0), (0x0D, 2)),
    "backspace": ((0x08, 0), (0x08, 2)),
    "ctrl-a": ((0x11, 0), (0x41, 0), (0x41, 2), (0x11, 2)),
    "ctrl-l": ((0x11, 0), (0x4C, 0), (0x4C, 2), (0x11, 2)),
}


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


def png_rgb(wide, high, bgra):
    import struct
    import zlib
    import numpy as np

    pix = np.frombuffer(bgra, dtype=np.uint8).reshape(high, wide, 4)
    rgb = np.ascontiguousarray(np.flipud(pix)[:, :, [2, 1, 0]])
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(high))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", wide, high, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b"")


def desk_png():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.SetProcessDPIAware.restype = ctypes.c_int
    user32.SetProcessDPIAware()
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
        if not mem or not bmp or not gdi32.StretchBlt(mem, 0, 0, wide, high, src, 0, 0, sw, sh, 0x00CC0020):
            die("vision absent")
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
        png = png_rgb(wide, high, bytes(buf))
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


def desk_prompt(goal):
    return (
        "<bos><|turn>system\n"
        + DESK_SYSTEM
        + "<turn|>\n<|turn>user\n"
        + MEDIA
        + "\n"
        + goal
        + "<turn|>\n<|turn>model\n"
    )


def desk_field(found, names):
    for name in names:
        value = found.get(name)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""


def desk_object(reply):
    raw = reply or ""
    start = raw.find("{")
    if start < 0:
        die("desk unparsed " + clip(raw, 160))
    try:
        found, _end = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError:
        die("desk unparsed " + clip(raw, 160))
    if not isinstance(found, dict):
        die("desk unparsed " + clip(raw, 160))
    see = desk_field(found, ("see", "screenshot_description", "description"))
    do = desk_field(found, ("do", "action"))
    if not see:
        die("desk see absent " + clip(raw, 140))
    if not do:
        die("desk action absent " + clip(raw, 140))
    name = do.lower()
    if name not in DESK_ACTS:
        die("desk action absent " + name + " " + clip(raw, 140))
    found["see"] = see
    found["do"] = name
    return found


_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _KEY(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", _PTR),
    ]


class _MOUSE(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", _PTR),
    ]


class _HARD(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_ushort), ("wParamH", ctypes.c_ushort)]


class _IN(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSE), ("ki", _KEY), ("hi", _HARD)]

    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


def send_input(items):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(_IN), ctypes.c_int]
    user32.SendInput.restype = ctypes.c_uint
    batch = (_IN * len(items))(*items)
    sent = user32.SendInput(len(items), batch, ctypes.sizeof(_IN))
    if sent != len(items):
        die("desk action absent")


def desk_key(name):
    seq = DESK_KEYS.get(str(name).strip().lower())
    if not seq:
        die("desk action absent")
    items = []
    for vk, flags in seq:
        item = _IN()
        item.type = 1
        item.ki = _KEY(vk, 0, flags, 0, 0)
        items.append(item)
    send_input(items)


def type_text(text):
    items = []
    for ch in text:
        code = ord(ch)
        down = _IN()
        down.type = 1
        down.ki = _KEY(0, code, 0x0004, 0, 0)
        up = _IN()
        up.type = 1
        up.ki = _KEY(0, code, 0x0004 | 0x0002, 0, 0)
        items.extend((down, up))
    if items:
        send_input(items)


def move_click(x, y):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = ctypes.c_int
    user32.mouse_event.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p]
    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    px = int(max(0.0, min(1000.0, float(x))) / 1000.0 * max(1, sw - 1))
    py = int(max(0.0, min(1000.0, float(y))) / 1000.0 * max(1, sh - 1))
    if not user32.SetCursorPos(px, py):
        die("desk action absent")
    time.sleep(0.05)
    user32.mouse_event(0x0002, 0, 0, 0, None)
    user32.mouse_event(0x0004, 0, 0, 0, None)


def act_click(obj):
    box = obj.get("box_2d")
    if not isinstance(box, list) or len(box) != 4:
        die("desk action absent")
    y0, x0, y1, x1 = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
    move_click((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    return "click %.0f %.0f %.0f %.0f" % (y0, x0, y1, x1)


def act_type(obj):
    text = str(obj.get("text", ""))
    key = str(obj.get("key", "")).strip().lower()
    if not text or (key and key not in DESK_KEYS):
        die("desk action absent")
    type_text(text)
    time.sleep(0.3)
    if not key:
        return "type " + text
    desk_key(key)
    return "type " + text + " key " + key


def act_key(obj):
    name = str(obj.get("key", "")).strip().lower()
    desk_key(name)
    return "key " + name


def act_wait(obj):
    del obj
    time.sleep(1.0)
    return "wait"


def act_done(_obj):
    return "done"


DESK_ACTS = {
    "click": act_click,
    "type": act_type,
    "key": act_key,
    "wait": act_wait,
    "done": act_done,
}


def deliver(image_b64):
    mod = sys.modules.get("call")
    if mod is None:
        return
    live = getattr(mod, "LIVE", None)
    if live is None or not getattr(live, "up", False):
        return
    mod.picture(base64.b64decode("".join((image_b64 or "").split())))


def desk_turn(goal):
    text = (goal or "").strip()
    if not text:
        die("empty goal")
    if not gemma_ready():
        die("brain missing gemma")
    if not (ROOT / "gemma-mmproj.gguf").is_file():
        die("vision absent")
    desktop_lease()
    image, wide, high = desk_png()
    reply = local_infer(desk_prompt(text), image)
    obj = desk_object(reply)
    seen = " ".join(obj["see"].split())
    taken = DESK_ACTS[obj["do"]](obj)
    if not str(taken or "").strip():
        die("desk action absent")
    if obj["do"] != "done":
        image, wide, high = desk_png()
    deliver(image)
    report = "see " + seen + "\nact " + taken + "\nshot " + str(wide) + " " + str(high)
    return report, image


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


def decl(name, description, fields):
    props = []
    required = []
    for fname, fdesc, req in fields:
        props.append(fname + ":{description:" + Q + fdesc + Q + ",type:" + Q + "STRING" + Q + "}")
        if req:
            required.append(Q + fname + Q)
    return (
        "<|tool>declaration:"
        + name
        + "{description:"
        + Q
        + description
        + Q
        + ",parameters:{properties:{"
        + ",".join(props)
        + "},required:["
        + ",".join(required)
        + "],type:"
        + Q
        + "OBJECT"
        + Q
        + "}}<tool|>"
    )


def tool_decls(names):
    table = {
        "remember": ("Store one fact that stays after old turns are dropped.", (("line", "The fact, one short line.", True),)),
        "place": ("Report this machine: cuda, vulkan, engines, playback, microphone.", ()),
        "desk": ("Look at the whole desktop. One call is one look or the one action he named. The picture is sent to his Telegram. A longer task is one call for each step.", (("line", "The goal, one short line.", True),)),
        "ring": ("Place the Telegram call to Wojciech when the work needs him.", ()),
        "next": ("Store one line of work for later. This does not run the work.", (("line", "The work, one short line.", True),)),
        "stop": ("Stop the local voice. Does not stop the brain.", (("line", "Waiting work to drop, or empty.", False),)),
    }
    parts = []
    for name in names:
        description, fields = table[name]
        parts.append(decl(name, description, fields))
    return "".join(parts)


def memory_file(profile, root=None):
    if profile not in PROFILES:
        die("unknown profile " + profile)
    return (ROOT if root is None else Path(root)) / PROFILES[profile]["memory"]


def parse_store(raw):
    facts, pairs, works = [], [], []
    pending = None
    lines = (raw or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    while index < len(lines):
        stripped = lines[index].rstrip(" \t")
        if stripped.endswith("<<") and stripped[:-2].strip() in ("fact", "user", "model", "work"):
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
            elif key == "work" and body:
                works.append(body)
            elif key == "user":
                if pending is not None:
                    pairs.append((pending, ""))
                pending = body
            else:
                if pending is None:
                    pending = ""
                pairs.append((pending, body))
                pending = None
            continue
        index += 1
    if pending is not None:
        pairs.append((pending, ""))
    return facts, pairs, works


def render_store(facts, pairs, works):
    parts = []
    for fact in facts:
        parts.append("fact <<\n" + fact + "\n<<\n")
    for line in works:
        parts.append("work <<\n" + line + "\n<<\n")
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
        return [], [], []
    return parse_store(path.read_text(encoding="utf-8"))


def write_memory(path, facts, pairs, works):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(render_store(facts, pairs, works).encode("utf-8"))
    os.replace(tmp, path)


def prompt_for(profile, question, suffix, root):
    path = memory_file(profile, root)
    facts, pairs, works = read_memory(path)
    names = PROFILES[profile]["tools"]
    head = (
        "<bos><|turn>system\nYou are Gemma, resident in Trident. Wojciech is the owner. "
        "The meaning of his words is the decision. "
        "What you remember is written in this prompt. "
        "Speak one or two short sentences in the language of his words. "
        "The computer microphone stays closed. "
        "A tool runs only when that meaning calls for it.\n"
        + tool_decls(names)
        + "<turn|>\n"
    )
    if facts:
        head += "Remembered:\n" + "\n".join(facts) + "\n"
    if works:
        head += "Work waiting:\n" + "\n".join(works) + "\n"
    parts = [head]
    kept = list(pairs)

    def build(items):
        body = list(parts)
        for user, model in items:
            body.append("<|turn>user\n" + user + "<turn|>\n")
            if model:
                body.append("<|turn>model\n" + model + "<turn|>\n")
        body.append("<|turn>user\n" + question.strip() + "<turn|>\n")
        body.append("<|turn>model\n" + suffix)
        return "".join(body)

    text = build(kept)
    while len(text) > 80000 and kept:
        kept = kept[1:]
        text = build(kept)
    if len(text) > 80000:
        die("prompt too long")
    if len(kept) != len(pairs):
        write_memory(path, facts, kept, works)
    return text


def answer_text(text):
    cleaned = text or ""
    cut = cleaned.find("<|tool_call>")
    if cut >= 0:
        cleaned = cleaned[:cut]
    if "<channel|>" in cleaned:
        cleaned = cleaned.split("<channel|>")[-1]
    for token in ("<|channel>thought", "<|channel>", "<turn|>", "<|turn>", "<bos>", "<eos>", "<|think|>", "`"):
        cleaned = cleaned.replace(token, "")
    return " ".join(cleaned.split())


def parse_tool_call(text):
    import re

    match = re.search(r"<\|tool_call>\s*call:([A-Za-z_][A-Za-z0-9_]*)\s*\{(.*?)\}\s*<tool_call\|>", text or "", re.DOTALL)
    if not match:
        return None
    args = {}
    for key, quoted, bare in re.findall(r"(\w+)\s*:\s*(?:<\|\"\|>(.*?)<\|\"\|>|([^,}\n]*))", match.group(2), re.DOTALL):
        args[key] = (quoted if quoted else bare).strip()
    return match.group(1), args, match.group(0)


def is_stop(text):
    found = parse_tool_call(text or "")
    return bool(found) and found[0] == "stop"


def tool_response(name, fields):
    parts = [key + ":" + Q + value + Q for key, value in fields]
    return "<|tool_response>response:" + name + "{" + ",".join(parts) + "}<tool_response|>"


def tool_remember(profile, args, root):
    line = clip(args.get("line", ""))
    if not line:
        die("empty fact")
    path = memory_file(profile, root)

    def run():
        facts, pairs, works = read_memory(path)
        if line not in facts:
            facts.append(line)
            write_memory(path, facts, pairs, works)

    with_memory(path, run)
    return line


def tool_next(profile, args, root):
    line = clip(args.get("line", ""))
    if not line:
        die("empty work")
    path = memory_file(profile, root)

    def run():
        facts, pairs, works = read_memory(path)
        if line not in works:
            works.append(line)
            write_memory(path, facts, pairs, works)

    with_memory(path, run)
    return line


def tool_stop(profile, args, root):
    target = clip(args.get("line", ""))
    path = memory_file(profile, root)

    def run():
        facts, pairs, works = read_memory(path)
        if target and target in works:
            works = [item for item in works if item != target]
            write_memory(path, facts, pairs, works)

    with_memory(path, run)
    return target or "voice"


def tool_place():
    return " ".join(caps_text().split())


def tool_desk(args):
    report, image = desk_turn(args.get("line", ""))
    return report, image


def tool_ring(_args):
    import call

    live = getattr(call, "LIVE", None)
    if live is not None and getattr(live, "up", False):
        return "up"
    return call.dial()


def run_tool(profile, name, args, root):
    if name not in PROFILES[profile]["tools"]:
        die("tool refused " + name)
    if name == "remember":
        return tool_remember(profile, args, root)
    if name == "next":
        return tool_next(profile, args, root)
    if name == "stop":
        return tool_stop(profile, args, root)
    if name == "place":
        return tool_place()
    if name == "desk":
        return tool_desk(args)
    if name == "ring":
        return tool_ring(args)
    die("unknown tool " + name)


def append_turn(profile, question, reply, root):
    path = memory_file(profile, root)
    spoken = answer_text(reply)
    if not spoken:
        return

    def run():
        facts, pairs, works = read_memory(path)
        pairs.append((clip(question, 400), clip(spoken, 400)))
        write_memory(path, facts, pairs, works)

    with_memory(path, run)


def agent_turn(profile, question, image_b64="", root=None):
    if profile not in PROFILES:
        die("unknown profile " + profile)
    if not (question or "").strip():
        die("empty question")

    def generate(prompt, image):
        return local_infer(prompt, image)

    if image_b64:
        prompt = question if MEDIA in question else MEDIA + "\n" + question
        text = generate(prompt, image_b64)
        if not answer_text(text):
            die("gemma returned empty")
        append_turn(profile, question, text, root)
        return text
    trail = ""
    shot = ""
    saw = False
    while True:
        suffix = trail + (MEDIA if shot else "")
        text = generate(prompt_for(profile, question, suffix, root), shot)
        shot = ""
        if not str(text or "").strip():
            die("agent follow-up empty" if saw else "gemma returned empty")
        found = parse_tool_call(text)
        if not found:
            if not answer_text(text):
                die("agent follow-up empty" if saw else "gemma returned empty")
            append_turn(profile, question, text, root)
            return text
        name, args, raw = found
        if name == "stop":
            tool_stop(profile, args, root)
            return text
        result = run_tool(profile, name, args, root)
        if isinstance(result, tuple):
            result, shot = result
        trail += raw + tool_response(name, [("text", result)])
        saw = True


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
