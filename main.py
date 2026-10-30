"""Trident. The sentence is a beat list."""
from __future__ import annotations
import ctypes, json, math, queue, socket, subprocess, sys, threading, time, urllib.request
from abc import ABC, abstractmethod
from ctypes import wintypes
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT = Path(__file__).resolve().parent
KEY = (ROOT.parent / ".env").read_text(encoding="utf-8").strip()
PORT, MODEL = 8765, "typesafe/jev-1.14"
ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
U = ctypes.WinDLL("user32", use_last_error=True)
U.SetProcessDPIAware()
U.SendInput.argtypes, U.SendInput.restype = (wintypes.UINT, ctypes.c_void_p, ctypes.c_int), wintypes.UINT
U.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
U.IsWindowVisible.argtypes, U.IsWindowVisible.restype = (wintypes.HWND,), wintypes.BOOL
U.EnumWindows.argtypes = (ENUM, wintypes.LPARAM)
U.ShowWindow.argtypes, U.SetForegroundWindow.argtypes = (wintypes.HWND, ctypes.c_int), (wintypes.HWND,)
U.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
U.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
U.GetSystemMetrics.argtypes, U.GetSystemMetrics.restype = (ctypes.c_int,), ctypes.c_int
class Mouse(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
class Key(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
class Input(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", Mouse), ("ki", Key)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]
def send(items):
    buf = (Input * len(items))(*items)
    U.SendInput(len(items), ctypes.byref(buf), ctypes.sizeof(Input))
def pointer(x, y, flags):
    vx, vy, vw, vh = (U.GetSystemMetrics(n) for n in (76, 77, 78, 79))
    ax, ay = int((x - vx) * 65535 / (vw - 1)), int((y - vy) * 65535 / (vh - 1))
    send([Input(type=0, mi=Mouse(ax, ay, 0, flags | 0x8001, 0, 0))])
def chord(*pairs):
    send([Input(type=1, ki=Key(vk, 0, flag, 0, 0)) for vk, flag in pairs])
def focus(hwnd):
    U.ShowWindow(hwnd, 3); chord((0x12, 0)); U.SetForegroundWindow(hwnd); chord((0x12, 2)); time.sleep(0.3)
def windows(part):
    found = []
    @ENUM
    def visit(hwnd, _lp):
        buf = ctypes.create_unicode_buffer(512); U.GetWindowTextW(hwnd, buf, 512)
        if buf.value.lower().endswith(part.lower()) and U.IsWindowVisible(hwnd): found.append(hwnd)
        return True
    U.EnumWindows(visit, 0)
    return found
def at(hwnd, x, y):
    point = wintypes.POINT(x, y); U.ClientToScreen(hwnd, ctypes.byref(point)); return point.x, point.y
WATCH = "\n".join(["import ctypes,sys,time", "u=ctypes.WinDLL('user32');u.GetForegroundWindow.restype=ctypes.c_void_p",
    "u.GetWindowTextW.argtypes=(ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_int)",
    "u.GetAsyncKeyState.argtypes=(ctypes.c_int,);u.GetAsyncKeyState.restype=ctypes.c_short",
    "title,vk=sys.argv[1],int(sys.argv[2]);buf=ctypes.create_unicode_buffer(512);last=False", "while True:",
    " h=u.GetForegroundWindow();u.GetWindowTextW(h,buf,512)", " down=bool(u.GetAsyncKeyState(vk)&0x8000)",
    " if buf.value.lower().endswith(title.lower()) and down and not last: break", " last=down;time.sleep(0.02)", ""])
PAGE = ("<!doctype html><meta charset=utf-8><title>Trident</title><style>body{margin:0;background:#10141a;color:#e8eef7;"
        "font:15px/1.45 Segoe UI,sans-serif}header{padding:12px 18px;background:#18202b}pre{white-space:pre-wrap;margin:0;"
        "padding:16px 18px;font:14px/1.45 Consolas,monospace}</style><header>Trident live journal</header><pre id=p></pre>"
        "<script>const p=document.getElementById('p');const es=new EventSource('/events');"
        "es.onmessage=e=>{p.textContent+=e.data+'\\n';scrollTo(0,9e9)}</script>")
@dataclass(frozen=True)
class Beat:
    verb: str
    arg: str
    @staticmethod
    def parse(line):
        verb, _, arg = line.strip().partition(" ")
        return Beat(verb, arg)
class Live(BaseHTTPRequestHandler):
    fan = None
    def log_message(self, fmt, *args): return
    def do_GET(self):
        fan = type(self).fan
        if self.path.startswith("/events"):
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache"); self.end_headers()
            with fan.lock:
                backlog, box = fan.path.read_text(encoding="utf-8"), fan.listen()
            self._sse(backlog)
            while True: self._sse(box.get())
        raw = fan.path.read_bytes() if self.path.startswith("/md") else PAGE.encode()
        kind = "text/markdown; charset=utf-8" if self.path.startswith("/md") else "text/html; charset=utf-8"
        self.send_response(200); self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def _sse(self, text):
        self.wfile.write(("".join(f"data: {line}\n" for line in text.splitlines()) + "\n").encode()); self.wfile.flush()
class Journal:
    def __init__(self, root):
        self.path = root / time.strftime("run_%Y%m%d_%H%M%S.md")
        self.path.write_text("", encoding="utf-8")
        self.lock, self.boxes, self.boxlock = threading.Lock(), [], threading.Lock()
        Live.fan = self
        ThreadingHTTPServer.allow_reuse_address = True
        threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", PORT), Live).serve_forever, daemon=True).start()
        self.view = subprocess.Popen(["powershell.exe", "-NoExit", "-NoProfile", "-Command",
            f"$Host.UI.RawUI.WindowTitle='Trident journal'; Get-Content -LiteralPath '{self.path}' -Wait -Encoding utf8"],
            creationflags=0x10)
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80)); self.host = probe.getsockname()[0]; probe.close()
    def listen(self):
        box = queue.Queue()
        with self.boxlock: self.boxes.append(box)
        return box
    def write(self, kind, body=""):
        text = f"## {time.strftime('%H:%M:%S')} {kind}\n\n" + (body.rstrip() + "\n\n" if body else "")
        with self.lock:
            with self.path.open("a", encoding="utf-8") as handle: handle.write(text); handle.flush()
            sys.stdout.write(text); sys.stdout.flush()
            with self.boxlock:
                for box in self.boxes: box.put(text)
class Jev:
    def ask(self, state, questions):
        req = urllib.request.Request("https://openrouter.ai/api/alpha/decisions", method="POST",
            data=json.dumps({"model": MODEL, "state": state, "questions": questions}).encode(),
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as res: return json.loads(res.read().decode())["answers"]
class Hand(ABC):
    @abstractmethod
    def run(self, beat): ...
class Desk(Hand):
    def __init__(self):
        self.hwnd = 0
        self.act = {"open": lambda arg: self.open(arg), "type": lambda arg: self.paste(arg),
                    "watch": lambda arg: self.watch(arg), "draw": lambda arg: self.draw(arg)}
    def run(self, beat):
        return self.act[beat.verb](beat.arg)
    def open(self, arg):
        title, command = arg.split("|", 1); subprocess.Popen(command.split()); rows, end = [], time.time() + 20
        while time.time() < end and not rows:
            rows = windows(title); time.sleep(0.1)
        self.hwnd = rows[0]; focus(self.hwnd); return f"opened {title.strip()}"
    def paste(self, text):
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", "Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
                       input=text, text=True, check=True, encoding="utf-8", creationflags=0x08000000)
        focus(self.hwnd); chord((0x11, 0), (0x41, 0), (0x41, 2), (0x11, 2)); time.sleep(0.05)
        chord((0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2)); time.sleep(0.5); return "typed"
    def watch(self, arg):
        title, vk = arg.rsplit(" ", 1); path = ROOT / "_wake.py"; path.write_text(WATCH, encoding="utf-8")
        self.paste("click the notepad window and hit Enter\n\n" + WATCH)
        proc = subprocess.Popen([sys.executable, str(path), title, vk], creationflags=0x08000000)
        proc.wait(); path.unlink(); return f"awake {title.strip()}"
    def draw(self, arg):
        focus(self.hwnd); rect = wintypes.RECT(); U.GetClientRect(self.hwnd, ctypes.byref(rect))
        cx, cy = rect.right // 2, int(rect.bottom * 0.58)
        if arg == "circle":
            pts = [(int(cx + 130 * math.cos(i * math.tau / 48)), int(cy + 130 * math.sin(i * math.tau / 48))) for i in range(49)]
        else:
            pts = [(cx - 190, cy - 80), (cx + 190, cy - 80), (cx + 190, cy + 80), (cx - 190, cy + 80), (cx - 190, cy - 80)]
        screen = [at(self.hwnd, x, y) for x, y in pts]
        pointer(*screen[0], 0); time.sleep(0.05); pointer(*screen[0], 2)
        for point in screen[1:]:
            pointer(*point, 0); time.sleep(0.02)
        pointer(*screen[-1], 4); time.sleep(0.25); return f"drew {arg}"
STRATEGY = {"strategy": {"type": "choice", "instructions": "The beat at index is current. Trace length equals how many beats finished, in order.",
    "criteria": {"obey": "Trace length equals index, so the beat at index has not been run.",
                 "skip": "Trace length is greater than index, so that beat was already run.",
                 "halt": "The sentence is finished or must not continue."}}}
DONE = {"done": {"type": "noul", "instructions": "Does the trace list one result for every beat?",
                 "criteria": {"true": "Trace length equals the number of beats.", "false": "A beat has no result."}}}
class Organism:
    def __init__(self):
        self.journal, self.jev, self.desk = Journal(ROOT), Jev(), Desk()
    def wake(self, sentence):
        beats = [Beat.parse(line) for line in sentence.splitlines() if line.strip()]
        try:
            self.journal.write("wake", sentence.strip()); self.journal.write("listen", f"http://{self.journal.host}:{PORT}/")
            trace, index = [], 0
            while index < len(beats):
                state = {"beats": [f"{b.verb} {b.arg}" for b in beats], "index": index, "trace": trace}
                choice = self.jev.ask(state, STRATEGY)["strategy"]["choice"]
                self.journal.write("jev", f"{index} {choice}")
                if choice == "skip": index += 1; continue
                if choice != "obey": break
                beat = beats[index]; self.journal.write("run", f"{beat.verb} {beat.arg}")
                trace.append(self.desk.run(beat)); self.journal.write("did", trace[-1]); index += 1
            done = self.jev.ask({"beats": [f"{b.verb} {b.arg}" for b in beats], "trace": trace}, DONE)
            self.journal.write("done", json.dumps(done["done"]))
        finally:
            time.sleep(0.4); self.journal.view.kill()
def main():
    Organism().wake(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
if __name__ == "__main__":
    main()
