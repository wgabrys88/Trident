import base64
import binascii
import os
import shutil
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
    "brain": ("infer",),
    "agent": ("turn",),
    "mouth": ("say",),
    "ear": ("listen",),
    "room": ("post", "task"),
    "eye": ("shot", "ask"),
    "endgame": ("run",),
    "telegram": ("run",),
    "call": ("live", "hangup", "restore", "hold"),
    "tool": ("call",),
}
PROFILES = {
    "voice": {
        "memory": "voice.memory.txt",
        "tools": ("remember", "place", "next", "stop"),
        "brain": "gemma",
    },
    "jarvis": {
        "memory": "gemma.memory.txt",
        "tools": ("remember", "place", "cursor", "next", "stop"),
        "brain": "gemma",
    },
}
MAP = (
    "gemma.py text+image inference only\n"
    "gemma.txt the only gemma weight and sampling config\n"
    "qwen.py qwen text weights\n"
    "node.py cards, queues, scheduler, agent, tools, caps, room, peers\n"
    "mouth.py playback\n"
    "hear.py ear capture where a microphone exists\n"
    "assistant.py voice organism, turns through the agent\n"
    "run.py voice start and stop, peer from TRIDENT_PEERS or --url\n"
    "endgame-ai/ desktop organism, module endgame, not edited on this seat\n"
    "endgame-vision-chat/ eye.shot via vision_frame.grab\n"
    "screenshot-vision/ eye.ask via its ask CLI\n"
    "telegram-control/ bot clone, not the desktop call\n"
    "desk.py desktop telegram call and ask\n"
    "vision.py locate when a window has no edit\n"
    "cursor.py one cursor agent, fifo, exit code\n"
)
Card = namedtuple(
    "Card",
    "id frm to module op body agent profile room image resource",
)
LOADED = {"model": ""}
PEERS = {}
CAPS = {"text": ""}
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
        die("peer is host:port")
    number = int(port)
    if number < 1 or number > 65535:
        die("peer port out of range")
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


def queue_dir(root=None):
    return (ROOT if root is None else Path(root)) / "node.queue"


def enqueue(card, root=None):
    base = queue_dir(root)
    base.mkdir(parents=True, exist_ok=True)
    path = base / (card.id + ".card")
    tmp = base / (card.id + ".card.tmp")
    tmp.write_bytes(render(card).encode("utf-8"))
    os.replace(tmp, path)


def claim_one(root=None):
    base = queue_dir(root)
    if not base.is_dir():
        return None
    for path in sorted(base.glob("*.card")):
        held = path.with_suffix(".held")
        try:
            os.replace(path, held)
        except OSError:
            continue
        try:
            raw = held.read_text(encoding="utf-8")
        except OSError as exc:
            die("cannot read " + held.name + ": " + str(exc))
        try:
            held.unlink()
        except OSError as exc:
            die("cannot remove " + held.name + ": " + str(exc))
        return parse_card(raw)
    return None


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
    if card.resource.strip():
        return tuple(card.resource.split())
    if card.module == "agent" and card.op == "turn":
        profile = card.profile or "jarvis"
        if profile not in PROFILES:
            die("unknown profile " + profile)
        brain = PROFILES[profile]["brain"]
        if engine_here(brain):
            return ("gpu", "weights:" + brain)
        return ("cpu",)
    table = {
        ("brain", "infer"): ("gpu", "weights:gemma"),
        ("mouth", "say"): ("playback",),
        ("ear", "listen"): ("ear",),
        ("room", "post"): ("room",),
        ("room", "task"): ("room",),
        ("node", "hello"): ("node",),
        ("eye", "shot"): ("screenshot",),
        ("eye", "ask"): ("gpu", "weights:internvl"),
        ("endgame", "run"): ("desktop",),
        ("telegram", "run"): ("desktop",),
        ("call", "live"): ("signal",),
        ("call", "hangup"): ("signal",),
        ("call", "restore"): ("signal",),
        ("call", "hold"): ("signal",),
        ("tool", "call"): ("cpu",),
    }
    return table[(card.module, card.op)]


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

    def finish_all(self, count, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            done = self.root / "node.state" / "completed"
            failed = self.root / "node.state" / "failed"
            n_done = len(list(done.glob("*.card"))) if done.is_dir() else 0
            n_fail = len(list(failed.glob("*.card"))) if failed.is_dir() else 0
            with self.cv:
                idle = not self.queue and not any(self.owner.values())
            if idle and n_done + n_fail >= count:
                if n_fail:
                    die("scheduler failed")
                return
            time.sleep(0.02)
        die("scheduler stuck")


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
    ctypes = __import__("ctypes")

    class VkApplicationInfo(ctypes.Structure):
        _fields_ = [
            ("sType", ctypes.c_uint32),
            ("pNext", ctypes.c_void_p),
            ("pApplicationName", ctypes.c_char_p),
            ("applicationVersion", ctypes.c_uint32),
            ("pEngineName", ctypes.c_char_p),
            ("engineVersion", ctypes.c_uint32),
            ("apiVersion", ctypes.c_uint32),
        ]

    class VkInstanceCreateInfo(ctypes.Structure):
        _fields_ = [
            ("sType", ctypes.c_uint32),
            ("pNext", ctypes.c_void_p),
            ("flags", ctypes.c_uint32),
            ("pApplicationInfo", ctypes.POINTER(VkApplicationInfo)),
            ("enabledLayerCount", ctypes.c_uint32),
            ("ppEnabledLayerNames", ctypes.c_void_p),
            ("enabledExtensionCount", ctypes.c_uint32),
            ("ppEnabledExtensionNames", ctypes.c_void_p),
        ]

    vk.vkCreateInstance.argtypes = [ctypes.POINTER(VkInstanceCreateInfo), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    vk.vkCreateInstance.restype = ctypes.c_int
    vk.vkEnumeratePhysicalDevices.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
    vk.vkEnumeratePhysicalDevices.restype = ctypes.c_int
    vk.vkGetPhysicalDeviceProperties.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    vk.vkGetPhysicalDeviceProperties.restype = None
    vk.vkDestroyInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    vk.vkDestroyInstance.restype = None
    app = VkApplicationInfo()
    app.sType = 0
    app.apiVersion = (1 << 22) | (2 << 12)
    info = VkInstanceCreateInfo()
    info.sType = 1
    info.pApplicationInfo = ctypes.pointer(app)
    inst = ctypes.c_void_p()
    if vk.vkCreateInstance(ctypes.byref(info), None, ctypes.byref(inst)) != 0 or not inst.value:
        return ""
    try:
        count = ctypes.c_uint32(0)
        if vk.vkEnumeratePhysicalDevices(inst, ctypes.byref(count), None) != 0 or count.value < 1:
            return ""
        arr = (ctypes.c_void_p * count.value)()
        if vk.vkEnumeratePhysicalDevices(inst, ctypes.byref(count), ctypes.cast(arr, ctypes.c_void_p)) != 0:
            return ""
        props = (ctypes.c_ubyte * 4096)()
        vk.vkGetPhysicalDeviceProperties(arr[0], ctypes.cast(props, ctypes.c_void_p))
        raw = bytes(props[20:276]).split(b"\x00", 1)[0]
        return raw.decode("utf-8", errors="replace").strip()
    finally:
        vk.vkDestroyInstance(inst, None)


def adapter_names():
    return cuda_name(), vulkan_name()


def mic_present():
    try:
        import sounddevice as sd
    except ImportError:
        return False
    devices = sd.query_devices()
    for info in devices:
        if int(info.get("max_input_channels") or 0) < 1:
            continue
        name = str(info.get("name") or "").lower()
        if "cable" in name:
            continue
        return True
    return False


def chrome_present():
    if shutil.which("chrome") or shutil.which("chrome.exe"):
        return True
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        root = os.environ.get(env, "")
        if root and (Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe").is_file():
            return True
    return False


def engine_here(name):
    if name == "gemma":
        return (ROOT / "gemma-brain.exe").is_file() and (ROOT / "gemma.gguf").is_file()
    if name == "qwen":
        return (ROOT / "sense.exe").is_file() and (ROOT / "sense.gguf").is_file()
    return False


def clone_file(name, filename):
    return (ROOT / name / filename).is_file()


def yes(flag):
    return "yes" if flag else "no"


def caps_text():
    if CAPS["text"]:
        return CAPS["text"]
    cuda, vulkan = adapter_names()
    lines = [
        "cpu " + str(os.cpu_count() or 0),
        "cuda " + (cuda or "none"),
        "vulkan " + (vulkan or "none"),
    ]
    if engine_here("gemma"):
        lines.append("brain gemma")
    if engine_here("qwen"):
        lines.append("brain qwen")
    lines.append("cursor " + yes(bool(shutil.which("agent"))))
    lines.append("endgame " + yes(clone_file("endgame-ai", "endgame.py")))
    lines.append("screenshot " + yes(clone_file("endgame-vision-chat", "vision_frame.py")))
    lines.append("vision " + yes(clone_file("screenshot-vision", "ask.py")))
    lines.append("telegram " + yes(clone_file("telegram-control", "telegram_pc_remote.py")))
    import desk
    lines.append("desktop " + yes(desk.interactive()))
    lines.append("telegram-desktop " + yes(bool(desk.telegram_exe())))
    lines.append("chrome " + yes(chrome_present()))
    lines.append("playback " + yes((ROOT / "chatterbox.exe").is_file()))
    lines.append("mic " + yes(mic_present()))
    CAPS["text"] = "\n".join(lines) + "\n"
    return CAPS["text"]


def cap_has(name, value):
    for line in caps_text().splitlines():
        if line == name + " " + value:
            return True
    return False


def real_stop(model):
    if model == "gemma":
        import gemma
        gemma.stop_resident()
        return
    if model == "qwen":
        import qwen
        qwen.stop_resident()
        return
    if model == "internvl":
        exe = ROOT / "screenshot-vision" / "main.py"
        if exe.is_file():
            subprocess.run([sys.executable, str(exe), "stop"], cwd=str(exe.parent), shell=False)
        return
    die("unknown weights " + model)


def use_weights(model, stop=None):
    if LOADED["model"] == model:
        return
    if LOADED["model"]:
        if stop is not None:
            stop(LOADED["model"])
        else:
            real_stop(LOADED["model"])
    LOADED["model"] = model


def file_b64(path):
    file_path = Path(path)
    if not file_path.is_file():
        die("missing image: " + str(file_path))
    data = file_path.read_bytes()
    if not data:
        die("empty image: " + str(file_path))
    return base64.b64encode(data).decode("ascii")


def local_infer(brain, prompt, image):
    if brain == "gemma":
        import gemma
        if image and MEDIA not in prompt:
            die("image prompt missing <__media__>")
        use_weights(brain)
        try:
            text = gemma.resident_generate(prompt, image or "", False)
        except SystemExit as exc:
            die(getattr(exc, "message", "") or "gemma failed")
        if not str(text or "").strip():
            die("gemma returned empty")
        return text
    if brain == "qwen":
        if image:
            die("qwen is text only")
        use_weights(brain)
        import qwen
        pid = qwen.ensure_resident()
        text = qwen.resident_ask(pid, prompt)
        if not str(text or "").strip():
            die("qwen returned empty")
        return text
    die("brain missing " + brain)


def transact(addr, card, timeout):
    host, port = split_host(addr)
    try:
        sock = socket.create_connection((host, port), timeout=min(5, timeout))
    except OSError as exc:
        die("peer missing " + str(exc))
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
        die("peer missing empty reply")
    reply = parse_card(data.decode("utf-8"))
    if reply.id != card.id:
        die("card id mismatch")
    if reply.body.startswith("err "):
        die(reply.body)
    return reply


def remote_infer(peer, prompt, image, timeout):
    card = make_card("brain", "infer", prompt, image if image else None, to="*")
    reply = transact(peer, card, timeout)
    if not reply.body.strip():
        die("gemma returned empty")
    return reply.body


def peer_with(cap, value):
    for item in PEERS.values():
        for line in item["caps"].splitlines():
            if line == cap + " " + value:
                return item
    return None


def peer_with_all(pairs):
    for item in PEERS.values():
        lines = item["caps"].splitlines()
        if all(cap + " " + value in lines for cap, value in pairs):
            return item
    return None


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
        "cursor": ("Start one local Cursor agent for a code change in this checkout.", (("task", "What to change, one short line.", True),)),
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


def clip(text, limit=200):
    flat = " ".join((text or "").split())
    if len(flat) > limit:
        flat = flat[:limit].rstrip()
    return flat


def prompt_for(profile, question, suffix, root):
    path = memory_file(profile, root)
    facts, pairs, works = read_memory(path)
    names = PROFILES[profile]["tools"]
    head = (
        "<bos><|turn>system\nYou are Jarvis, the voice of Trident. Wojciech is the owner. "
        "Speak one or two short sentences in the language of the user's words. "
        "Call a tool only by its tool call when its description matches. "
        "The spoken sentence has no channels or file names.\n"
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


def strip_tool_markup(text):
    raw = text or ""
    while "<|tool_call>" in raw and "<tool_call|>" in raw:
        start = raw.find("<|tool_call>")
        end = raw.find("<tool_call|>", start)
        if end < 0:
            break
        raw = raw[:start] + raw[end + len("<tool_call|>"):]
    return raw


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
    call = parse_tool_call(text or "")
    return bool(call) and call[0] == "stop"


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
    room_add(profile, "task", node_id(), line, root)
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
    room_add(profile, "stop", node_id(), target or "voice", root)
    return target or "voice"


def tool_place():
    return " ".join(caps_text().split())


def tool_cursor(args):
    import cursor
    return cursor.start(clip(args.get("task", "")))


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
    if name == "cursor":
        return tool_cursor(args)
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


def agent_turn(profile, question, image_b64="", peer="", generate=None, timeout=180, root=None):
    if profile not in PROFILES:
        die("unknown profile " + profile)
    if not (question or "").strip():
        die("empty question")
    brain = PROFILES[profile]["brain"]
    if generate is None:
        def generate(prompt, image):
            if peer:
                return remote_infer(peer, prompt, image, timeout)
            if engine_here(brain):
                return local_infer(brain, prompt, image)
            other = peer_with("brain", brain)
            if not other:
                die("brain missing " + brain)
            return remote_infer(other["addr"], prompt, image, timeout)
    if image_b64:
        prompt = question if MEDIA in question else MEDIA + "\n" + question
        text = generate(prompt, image_b64)
        if not answer_text(text):
            die("gemma returned empty")
        append_turn(profile, question, text, root)
        return text
    text = generate(prompt_for(profile, question, "", root), "")
    if not str(text or "").strip():
        die("gemma returned empty")
    call = parse_tool_call(text)
    if not call:
        if not answer_text(text):
            die("gemma returned empty")
        append_turn(profile, question, text, root)
        return text
    name, args, raw = call
    if name == "stop":
        tool_stop(profile, args, root)
        return text
    result = run_tool(profile, name, args, root)
    suffix = raw + tool_response(name, [("text", result)])
    reply = generate(prompt_for(profile, question, suffix, root), "")
    if not answer_text(reply):
        die("agent follow-up empty")
    append_turn(profile, question, reply, root)
    return reply


def room_add(room, kind, frm, body, root=None):
    name = safe_token(room or "floor")
    base = (ROOT if root is None else Path(root)) / "node.room"
    base.mkdir(parents=True, exist_ok=True)
    path = base / (name + ".log")
    lock = base / (name + ".lock")
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_RDWR)
    except FileExistsError:
        die("room busy")
    try:
        line = kind + " " + frm + " " + " ".join((body or "").split()) + "\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    finally:
        os.close(fd)
        lock.unlink()


def handle_hello(card):
    PEERS.setdefault(card.frm, {"addr": "", "caps": card.body, "id": card.frm})
    return caps_text(), None


def handle_brain(card):
    if card.image is not None:
        compact = "".join(card.image.split())
        if not compact:
            die("missing image")
        try:
            data = base64.b64decode(compact, validate=True)
        except (ValueError, binascii.Error):
            die("bad image")
        if not data:
            die("empty image")
        if MEDIA not in card.body:
            die("image prompt missing <__media__>")
    if not (card.body or "").strip():
        die("empty text")
    text = local_infer("gemma", card.body, card.image or "")
    return text, None


def handle_agent(card):
    profile = card.profile or "jarvis"
    text = agent_turn(profile, card.body, card.image or "")
    return text, None


def handle_mouth(card):
    text = " ".join((card.body or "").split())
    if not text:
        die("empty say")
    if card.to not in ("*", node_id()) and card.to in PEERS:
        reply = transact(PEERS[card.to]["addr"], card._replace(frm=node_id()), 180)
        return reply.body, None
    if not cap_has("playback", "yes"):
        other = peer_with("playback", "yes")
        if not other or not other["addr"]:
            die("mouth missing")
        sent = card._replace(frm=node_id(), to=other["id"])
        reply = transact(other["addr"], sent, 180)
        return reply.body, None
    import mouth
    mouth.speak_pieces([(text, "nano", "en")], mouth.play_wav)
    return text, None


def handle_ear(card):
    body = (card.body or "").strip()
    if not body:
        die("empty listen")
    if "\x00" in body:
        die("ear is text")
    if card.to not in ("*", node_id()) and card.to in PEERS:
        reply = transact(PEERS[card.to]["addr"], card._replace(frm=node_id()), 30)
        return reply.body, None
    if not cap_has("mic", "yes"):
        other = peer_with("mic", "yes")
        if not other or not other["addr"]:
            die("ear missing")
        sent = card._replace(frm=node_id(), to=other["id"])
        reply = transact(other["addr"], sent, 30)
        return reply.body, None
    if body == "stop":
        code = subprocess.run([sys.executable, str(ROOT / "run.py"), "stop"], cwd=str(ROOT), shell=False).returncode
        if code:
            die("ear stop failed")
        return "stop", None
    if body == "start":
        if not (ROOT / "assistant.pid").is_file() and not (ROOT / "iris.pid").is_file():
            die("ear down")
        return "listening", None
    room_add(card.room or "floor", "heard", card.frm, body)
    return body, None


def handle_room(card):
    room_add(card.room or "floor", card.op, card.frm, card.body)
    return card.body, None


def handle_shot(card):
    path = str(ROOT / "endgame-vision-chat")
    if path not in sys.path:
        sys.path.insert(0, path)
    import vision_frame
    frame = vision_frame.grab(goal=card.body or "", crop_title=card.room or None)
    return frame.user_prompt, frame.full_b64()


def handle_ask(card):
    script = ROOT / "screenshot-vision" / "main.py"
    if not script.is_file():
        die("vision missing")
    use_weights("internvl")
    mode = (card.agent or "describe").strip()
    argv = [sys.executable, str(script), "ask", "--mode", mode]
    if card.body.strip() and mode == "find":
        argv.extend(["--query", card.body.strip()])
    elif card.body.strip():
        argv.extend(["--prompt", card.body.strip()])
    completed = subprocess.run(argv, cwd=str(script.parent), shell=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
    LOADED["model"] = ""
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        die(err or "vision failed")
    text = (completed.stdout or "").strip()
    if not text:
        die("vision returned empty")
    return text, None


def handle_endgame(card):
    if card.to not in ("*", node_id()) and card.to in PEERS:
        reply = transact(PEERS[card.to]["addr"], card._replace(frm=node_id()), 600)
        return reply.body, None
    if not cap_has("desktop", "yes"):
        other = peer_with("desktop", "yes")
        if not other or not other["addr"]:
            die("desktop lease absent")
        sent = card._replace(frm=node_id(), to=other["id"])
        reply = transact(other["addr"], sent, 600)
        return reply.body, None
    import desk
    return desk.place_ask(desk.parse(card.body), card.id), None


def handle_telegram(card):
    if card.to not in ("*", node_id()) and card.to in PEERS:
        reply = transact(PEERS[card.to]["addr"], card._replace(frm=node_id()), 86400)
        return reply.body, None
    if not cap_has("desktop", "yes") or not cap_has("telegram-desktop", "yes"):
        other = peer_with_all((("desktop", "yes"), ("telegram-desktop", "yes")))
        if not other or not other["addr"]:
            if not cap_has("desktop", "yes"):
                die("desktop lease absent")
            die("telegram desktop absent")
        sent = card._replace(frm=node_id(), to=other["id"])
        reply = transact(other["addr"], sent, 86400)
        return reply.body, None
    import desk
    return desk.place_call(card.id), None


def handle_call(card):
    import desk
    return desk.record_call(card.op, card.body), None


def handle_tool(card):
    profile = card.profile or "jarvis"
    name, _, rest = (card.body or "").partition(" ")
    args = {"line": rest.strip(), "task": rest.strip()}
    if not name.strip():
        die("empty tool")
    return run_tool(profile, name.strip(), args, None), None


HANDLERS = {
    ("node", "hello"): handle_hello,
    ("brain", "infer"): handle_brain,
    ("agent", "turn"): handle_agent,
    ("mouth", "say"): handle_mouth,
    ("ear", "listen"): handle_ear,
    ("room", "post"): handle_room,
    ("room", "task"): handle_room,
    ("eye", "shot"): handle_shot,
    ("eye", "ask"): handle_ask,
    ("endgame", "run"): handle_endgame,
    ("telegram", "run"): handle_telegram,
    ("call", "live"): handle_call,
    ("call", "hangup"): handle_call,
    ("call", "restore"): handle_call,
    ("call", "hold"): handle_call,
    ("tool", "call"): handle_tool,
}


def handle(card):
    return HANDLERS[(card.module, card.op)](card)


def reply_of(card, body, image):
    return Card(card.id, node_id(), card.frm, card.module, card.op, body, "", "", card.room, image, "")


def card_timeout(card):
    if card.module == "telegram":
        return 86400
    return 600


def execute(card, timeout):
    global SCHED
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
        card = parse_card(data.decode("utf-8"))
        try:
            body, image = execute(card, card_timeout(card))
            reply = reply_of(card, body, image)
        except SystemExit as exc:
            reply = reply_of(card, "err " + (getattr(exc, "message", "") or "failed"), None)
        conn.sendall(render(reply).encode("utf-8"))
    except SystemExit as exc:
        print(getattr(exc, "message", "") or "failed", file=sys.stderr, flush=True)
    finally:
        conn.close()


def join_peers():
    raw = os.environ.get("TRIDENT_PEERS", "").strip()
    if not raw:
        return
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        card = make_card("node", "hello", caps_text(), to="*")
        reply = transact(item, card, 10)
        if reply.module != "node" or reply.op != "hello":
            die("peer hello refused")
        PEERS[reply.frm] = {"addr": item, "caps": reply.body, "id": reply.frm}
        print("node: joined " + reply.frm + " " + item, file=sys.stderr, flush=True)


def serve(host="0.0.0.0", port=PORT):
    global SCHED
    SCHED = Scheduler(ROOT)
    SCHED.start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        sock.bind((host, port))
    except OSError as exc:
        die("cannot listen on " + host + ":" + str(port) + ": " + str(exc))
    sock.listen(8)
    sock.settimeout(0.2)
    print("node: listening " + host + ":" + str(port) + " id " + node_id(), file=sys.stderr, flush=True)
    join_peers()
    import cursor
    while not STOP.is_set():
        cursor.tick()
        card = claim_one(ROOT)
        if card is not None:
            try:
                execute(card, card_timeout(card))
            except SystemExit as exc:
                print(getattr(exc, "message", "") or "failed", file=sys.stderr, flush=True)
            continue
        try:
            conn, _addr = sock.accept()
        except socket.timeout:
            continue
        threading.Thread(target=serve_conn, args=(conn,), daemon=True).start()
    sock.close()


def reachable(addr):
    host, port = split_host(addr)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.0)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def remote_place(addr):
    cuda, vulkan = adapter_names()
    Place = namedtuple("Place", "brain url cuda vulkan adapter flip where")
    same = bool(cuda and vulkan and " ".join(cuda.casefold().split()) == " ".join(vulkan.casefold().split()))
    return Place("post", addr, cuda, vulkan, "same" if same else "different", False, "peer")


def local_place():
    cuda, vulkan = adapter_names()
    Place = namedtuple("Place", "brain url cuda vulkan adapter flip where")
    same = bool(cuda and vulkan and " ".join(cuda.casefold().split()) == " ".join(vulkan.casefold().split()))
    adapter = "same" if same else "different"
    if engine_here("gemma"):
        return Place("resident", "", cuda, vulkan, adapter, bool(same and cuda), "local")
    if engine_here("qwen"):
        return Place("cpu", "", cuda, vulkan, adapter, False, "local")
    return Place("missing", "", cuda, vulkan, adapter, False, "local")


def place_line(found):
    return "brain " + found.brain + " " + found.where + " cuda " + (found.cuda or "none") + " vulkan " + (found.vulkan or "none")


def prove_expect(label, fn, needle):
    try:
        fn()
    except SystemExit as exc:
        message = getattr(exc, "message", "") or ""
        if needle not in message:
            die(label + " raised " + message)
        print("prove: " + label, file=sys.stderr, flush=True)
        return
    die(label + " returned")


def prove():
    import tempfile
    log = []
    run_text = (ROOT / "run.py").read_text(encoding="utf-8")
    assistant_text = (ROOT / "assistant.py").read_text(encoding="utf-8")
    if "192.168.16.31" in run_text or "TRIDENT_NVIDIA_URL" in run_text or "TRIDENT_NVIDIA_URL" in assistant_text:
        die("hard-coded peer url remains")
    for needle in ("nvidia_client", "import seat", "drain_seat", "tool_turn", "gemma.place"):
        if needle in assistant_text:
            die("assistant still has " + needle)
    gemma_src = (ROOT / "gemma.py").read_text(encoding="utf-8")
    node_src = (ROOT / "node.py").read_text(encoding="utf-8")
    fabricated = "or " + '"' + "done" + '"'
    if fabricated in gemma_src or fabricated in node_src:
        die("fabricated done remains")
    pins = {
        "endgame-ai": "5ebd9e4d2308fdc07da6f8074040cb26061b32a6",
        "endgame-vision-chat": "6a4604c1572406bbd6a5255ebc92925900aa78e3",
    }
    clone_shas = []
    for name in ("endgame-ai", "endgame-vision-chat", "screenshot-vision", "telegram-control"):
        got = subprocess.check_output(["git", "-C", str(ROOT / name), "rev-parse", "HEAD"], text=True).strip()
        clone_shas.append(name + " " + got)
        if name in pins and got != pins[name]:
            die(name + " sha " + got)
    import gemma
    left = gemma.config_pairs(gemma.settings_text("Hello", ""))
    right = gemma.config_pairs(gemma.resident_settings())
    for key in ("gemma.text", "gemma.image"):
        left.pop(key, None)
        right.pop(key, None)
    if left != right:
        die("brain config mismatch")
    print("prove: brain config", file=sys.stderr, flush=True)
    prove_expect("missing image", lambda: file_b64(ROOT / "no-such-image.png"), "missing image")
    prove_expect("blank image", lambda: handle_brain(make_card("brain", "infer", "see " + MEDIA, image="")), "missing image")
    prove_expect("image without media", lambda: local_infer("gemma", "no token", "aaaa"), "image prompt missing")
    root = Path(tempfile.mkdtemp(prefix="trident-node-"))
    seq = iter([
        '<|tool_call>call:remember{line:<|"|>alpha<|"|>}<tool_call|>',
        "   ",
    ])
    def generate(_prompt, _image):
        return next(seq)
    prove_expect(
        "empty follow-up",
        lambda: agent_turn("jarvis", "remember alpha", generate=generate, root=root),
        "agent follow-up empty",
    )
    enqueue(make_card("room", "post", "one", room="floor", ident="a1"), root)
    enqueue(make_card("room", "post", "two", room="floor", ident="b2"), root)
    ids = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)
    def claim():
        barrier.wait()
        card = claim_one(root)
        with lock:
            if card is not None:
                ids.append(card.id)
    threads = [threading.Thread(target=claim), threading.Thread(target=claim)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if sorted(ids) != ["a1", "b2"]:
        die("queue race " + " ".join(ids))
    if (root / "nvidia_turn.request.txt").exists():
        die("shared inbox remains")
    print("prove: queue", file=sys.stderr, flush=True)
    order = []
    gate = threading.Event()
    sched = Scheduler(root)
    sched.start()
    def job(name, resource, hold):
        def run(card):
            order.append("start " + name)
            if hold:
                gate.wait(2)
            time.sleep(0.05)
            order.append("end " + name)
            return "ok", None
        card = make_card("room", "post", name, room="floor", resource=resource, ident="s" + name)
        return card, run
    a, ra = job("A", "gpu", True)
    b, rb = job("B", "cpu", False)
    c, rc = job("C", "gpu", False)
    sched.submit(a, ra)
    time.sleep(0.05)
    sched.submit(b, rb)
    sched.submit(c, rc)
    time.sleep(0.2)
    gate.set()
    sched.finish_all(3, 3)
    if order.index("start B") > order.index("end A"):
        die("independent resource waited " + " ".join(order))
    if order.index("start C") < order.index("end A"):
        die("gpu conflict was not fifo " + " ".join(order))
    print("prove: scheduler " + " ".join(order), file=sys.stderr, flush=True)
    saved = LOADED["model"]
    LOADED["model"] = ""
    use_weights("gemma", stop=lambda name: log.append(name))
    use_weights("gemma", stop=lambda name: log.append(name))
    use_weights("qwen", stop=lambda name: log.append("unload " + name))
    LOADED["model"] = saved
    if log != ["unload gemma"]:
        die("weights unloaded on the same model " + " ".join(log))
    print("prove: weights", file=sys.stderr, flush=True)
    hits = []
    real = local_infer
    def wrapped(*args, **kwargs):
        hits.append(1)
        return real(*args, **kwargs)
    globals()["local_infer"] = wrapped
    try:
        prove_expect("peer miss", lambda: agent_turn("voice", "hi", peer="127.0.0.1:9", timeout=2, root=root), "peer missing")
    finally:
        globals()["local_infer"] = real
    if hits:
        die("peer miss used the local brain")
    text = caps_text()
    if "mic yes" in text:
        die("claimed a microphone\n" + text)
    for needle in ("endgame yes", "screenshot yes", "vision yes", "telegram yes", "playback yes", "brain gemma"):
        if needle not in text:
            die("caps missing " + needle + "\n" + text)
    print("prove: caps\n" + text, file=sys.stderr, flush=True)
    body, image = handle_shot(make_card("eye", "shot", "prove the screen"))
    if not image or len(base64.b64decode(image)) < 32:
        die("eye.shot returned no image")
    print("prove: eye.shot bytes " + str(len(base64.b64decode(image))), file=sys.stderr, flush=True)
    room_add("floor", "post", node_id(), "skeleton", root)
    log_text = (root / "node.room" / "floor.log").read_text(encoding="utf-8")
    if "skeleton" not in log_text:
        die("room log missing")
    print("prove: room", file=sys.stderr, flush=True)
    if reachable("127.0.0.1:" + str(PORT)):
        die("port busy")
    qdir = ROOT / "node.queue"
    if qdir.is_dir() and any(qdir.glob("*.card")):
        die("node.queue is not empty")
    STOP.clear()
    global SCHED
    saved_peers = os.environ.pop("TRIDENT_PEERS", None)
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline and not reachable("127.0.0.1:" + str(PORT)):
            time.sleep(0.05)
        if not reachable("127.0.0.1:" + str(PORT)):
            die("node did not listen")
        reply = transact("127.0.0.1:" + str(PORT), make_card("node", "hello", "hi"), 5)
        if "mic no" not in reply.body:
            die("hello claimed a microphone")
        print("prove: hello " + reply.frm, file=sys.stderr, flush=True)
    finally:
        STOP.set()
        thread.join(timeout=3)
        if saved_peers is not None:
            os.environ["TRIDENT_PEERS"] = saved_peers
    proof = []
    proof.append("command python node.py --prove")
    proof.append("tip " + subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True).strip())
    proof.extend(clone_shas)
    proof.append("map")
    proof.append(MAP.rstrip())
    proof.append("caps")
    proof.append(text.rstrip())
    proof.append("scheduler " + " ".join(order))
    proof.append("eye.shot bytes " + str(len(base64.b64decode(image))))
    proof.append("hello " + reply.frm)
    proof.append("room " + log_text.strip())
    (ROOT / "node.proof.txt").write_text("\n".join(proof) + "\n", encoding="utf-8")
    print("prove: wrote node.proof.txt", file=sys.stderr, flush=True)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:]
    if argv == ["--prove"]:
        prove()
        return
    if argv and argv[0] == "--peer":
        if len(argv) != 2:
            die("usage: node.py --peer host:port")
        reply = transact(argv[1], make_card("node", "hello", caps_text()), 10)
        sys.stdout.write(reply.body)
        return
    if argv:
        die("usage: node.py [--prove | --peer host:port]")
    try:
        serve()
    except KeyboardInterrupt:
        print("node: stopped", file=sys.stderr, flush=True)
        raise SystemExit(0)


if __name__ == "__main__":
    main()
