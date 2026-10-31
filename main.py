import argparse, base64, ctypes, hashlib, json, math, os, queue, socket, subprocess, sys, threading, time, urllib.request, uuid
from datetime import datetime, timezone
from contextlib import ExitStack, contextmanager
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT = Path(__file__).resolve().parent
PORT, MODEL = 8765, "typesafe/jev-1.14"
PALETTE = ("#7CFFB2", "#7CC4FF", "#FFD27C", "#FF8FB8", "#C9B6FF", "#FFB07C")
ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
U = ctypes.WinDLL("user32", use_last_error=True)
U.SetProcessDPIAware()
U.SendInput.argtypes, U.SendInput.restype = (wintypes.UINT, ctypes.c_void_p, ctypes.c_int), wintypes.UINT
U.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
U.IsWindowVisible.argtypes, U.IsWindowVisible.restype = (wintypes.HWND,), wintypes.BOOL
U.IsIconic.argtypes, U.IsIconic.restype = (wintypes.HWND,), wintypes.BOOL
U.EnumWindows.argtypes = (ENUM, wintypes.LPARAM)
U.ShowWindow.argtypes, U.SetForegroundWindow.argtypes = (wintypes.HWND, ctypes.c_int), (wintypes.HWND,)
U.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
U.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
U.GetSystemMetrics.argtypes, U.GetSystemMetrics.restype = (ctypes.c_int,), ctypes.c_int
U.GetForegroundWindow.argtypes, U.GetForegroundWindow.restype = (), wintypes.HWND
U.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
TOP = wintypes.HWND(-1)
class Mouse(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
class Key(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
class Input(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", Mouse), ("ki", Key)]
    _anonymous_ = ("u",); _fields_ = [("type", wintypes.DWORD), ("u", _U)]
def send(items):
    buf = (Input * len(items))(*items)
    if U.SendInput(len(items), ctypes.byref(buf), ctypes.sizeof(Input)) != len(items): raise ctypes.WinError(ctypes.get_last_error())
def pointer(x, y, flags):
    vx, vy, vw, vh = (U.GetSystemMetrics(n) for n in (76, 77, 78, 79))
    ax, ay = int((x - vx) * 65535 / (vw - 1)), int((y - vy) * 65535 / (vh - 1))
    send([Input(type=0, mi=Mouse(ax, ay, 0, flags | 0xC001, 0, 0))])
def chord(*pairs):
    send([Input(type=1, ki=Key(vk, 0, flag, 0, 0)) for vk, flag in pairs])
def focus(hwnd):
    if U.IsIconic(hwnd): U.ShowWindow(hwnd, 9)
    if U.GetForegroundWindow() != hwnd:
        chord((0x12, 0)); ok = U.SetForegroundWindow(hwnd); chord((0x12, 2))
        if not ok: raise OSError('SetForegroundWindow failed')
    time.sleep(0.3)
    if U.GetForegroundWindow() != hwnd: raise OSError('target is not foreground')
def windows(part):
    found = []
    @ENUM
    def visit(hwnd, _lp):
        buf = ctypes.create_unicode_buffer(512); U.GetWindowTextW(hwnd, buf, 512)
        if buf.value.lower().endswith(part.lower()) and U.IsWindowVisible(hwnd): found.append(hwnd)
        return True
    wincheck(U.EnumWindows(visit, 0))
    return found
def at(hwnd, x, y):
    point = wintypes.POINT(x, y); wincheck(U.ClientToScreen(hwnd, ctypes.byref(point))); return point.x, point.y
WATCH = "import ctypes,sys,time\nu=ctypes.WinDLL('user32');u.GetForegroundWindow.restype=ctypes.c_void_p\nu.GetWindowTextW.argtypes=(ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_int)\nu.GetAsyncKeyState.argtypes=(ctypes.c_int,);u.GetAsyncKeyState.restype=ctypes.c_short\ntitle,vk=sys.argv[1],int(sys.argv[2]);buf=ctypes.create_unicode_buffer(512);last=False\nwhile True:\n h=u.GetForegroundWindow();u.GetWindowTextW(h,buf,512)\n down=bool(u.GetAsyncKeyState(vk)&0x8000)\n if buf.value.lower().endswith(title.lower()) and down and not last: break\n last=down;time.sleep(0.02)\n"
class Live(BaseHTTPRequestHandler):
    fan = None
    def log_message(self, fmt, *args): return
    def do_POST(self):
        item = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode())
        type(self).fan.write(item["agent"], item["kind"], item["body"]); self.send_response(204); self.end_headers()
    def do_GET(self):
        fan = type(self).fan
        if self.path.startswith("/events"):
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache"); self.end_headers()
            with fan.lock: box = fan.listen()
            try:
                while True: self._sse(box.get())
            finally:
                with fan.boxlock: fan.boxes.remove(box)
        raw = fan.path.read_bytes() if self.path.startswith("/md") else fan.html()
        kind = "text/markdown; charset=utf-8" if self.path.startswith("/md") else "text/html; charset=utf-8"
        self.send_response(200); self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def _sse(self, item):
        self.wfile.write(b"data: " + json.dumps(item).encode() + b"\n\n"); self.wfile.flush()
class Journal:
    def __init__(self, root):
        self.path = root / time.strftime("run_%Y%m%d_%H%M%S.md")
        self.path.write_text("", encoding="utf-8")
        self.lock, self.boxlock, self.boxes, self.events, self.colors = threading.Lock(), threading.Lock(), [], [], {}
        Live.fan = self
        subprocess.Popen(["powershell.exe", "-NoExit", "-NoProfile", "-Command",
            f"$Host.UI.RawUI.WindowTitle='Trident journal'; Get-Content -LiteralPath '{self.path}' -Wait -Encoding utf8"],
            creationflags=0x10)
    def pin(self):
        rows = []
        while not rows: rows = windows("Trident journal"); time.sleep(0.05)
        x = U.GetSystemMetrics(0) - 500
        while True: U.SetWindowPos(rows[0], TOP, x, 16, 480, 300, 0x0050); time.sleep(0.4)
    def listen(self):
        box = queue.Queue()
        with self.boxlock: self.boxes.append(box)
        return box
    def write(self, agent, kind, body=""):
        with self.lock:
            if agent not in self.colors:
                self.colors[agent] = PALETTE[len(self.colors) % len(PALETTE)]
                self.emit(agent, "agent", "joined")
            self.emit(agent, kind, body)
    def emit(self, agent, kind, body):
        color = self.colors[agent]
        text = f"## {time.strftime('%H:%M:%S')} {color} {agent} {kind}\n\n" + (body.rstrip() + "\n\n" if body else "")
        record(self.path, text); self.events.append((color, text))
        with self.boxlock:
            for box in self.boxes: box.put({"color": color, "text": text})
    def html(self):
        with self.lock:
            rows = "".join('<pre style="color:%s">%s</pre>' % (c, t.replace("&", "&amp;").replace("<", "&lt;")) for c, t in self.events)
        return ("<!doctype html><meta charset=utf-8><title>Trident</title><style>body{margin:0;background:#10141a}"
                "pre{white-space:pre-wrap;margin:0;padding:8px 14px;font:14px/1.4 Consolas,monospace}</style><div id=p>"
                + rows + "</div><script>const p=document.getElementById('p'),es=new EventSource('/events');"
                "es.onmessage=e=>{const m=JSON.parse(e.data),d=document.createElement('pre');d.style.color=m.color;d.textContent=m.text;p.appendChild(d);scrollTo(0,9e9)}</script>").encode()
    def serve(self):
        threading.Thread(target=self.pin, daemon=True).start()
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80)); self.host = probe.getsockname()[0]; probe.close()
        self.write("hub", "listen", f"http://{self.host}:{PORT}/")
        ThreadingHTTPServer.allow_reuse_address = True
        ThreadingHTTPServer(("0.0.0.0", PORT), Live).serve_forever()
def request(url, payload, timeout, headers=None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={**(headers or {}), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response: return response.read()
def log(kind, body=""):
    request(f"http://127.0.0.1:{PORT}/log", {"agent": str(os.getpid()), "kind": kind, "body": body}, 30)
def decide(state, questions):
    key = (ROOT.parent / ".env").read_text(encoding="utf-8").strip()
    return json.loads(request("https://openrouter.ai/api/alpha/decisions",
        {"model": MODEL, "state": state, "questions": questions}, 60, {"Authorization": f"Bearer {key}"}))["answers"]
class Desk:
    hwnd = 0
    def run(self, beat):
        verb, arg = beat
        return {"open": self.open, "type": self.paste, "watch": self.watch, "draw": self.draw}[verb](arg)
    def open(self, arg):
        title, command = arg.split("|", 1); subprocess.Popen(command.strip()); rows, end = [], time.time() + 20
        while time.time() < end and not rows:
            rows = windows(title); time.sleep(0.1)
        if not rows: raise TimeoutError(f"window not found: {title}")
        self.hwnd = rows[0]; focus(self.hwnd); return f"opened {title.strip()}"
    def paste(self, text):
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", "Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
                       input=text, text=True, check=True, encoding="utf-8", creationflags=0x08000000)
        focus(self.hwnd); chord((0x11, 0), (0x41, 0), (0x41, 2), (0x11, 2)); time.sleep(0.05)
        chord((0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2)); time.sleep(0.5); return "typed"
    def watch(self, arg):
        title, vk = arg.rsplit(" ", 1)
        self.paste(f"Activate {title} and press Windows virtual-key code {vk}\n\n" + WATCH)
        proc = subprocess.Popen([sys.executable, "-c", WATCH, title, vk], creationflags=0x08000000)
        proc.wait(); return f"awake {title.strip()}"
    def draw(self, arg):
        focus(self.hwnd); rect = wintypes.RECT(); wincheck(U.GetClientRect(self.hwnd, ctypes.byref(rect)))
        cx, cy = rect.right // 2, int(rect.bottom * 0.58)
        pts = [(int(cx + 130 * math.cos(i * math.tau / 48)), int(cy + 130 * math.sin(i * math.tau / 48))) for i in range(49)] if arg == "circle" else [(cx - 190, cy - 80), (cx + 190, cy - 80), (cx + 190, cy + 80), (cx - 190, cy + 80), (cx - 190, cy - 80)]
        screen = [at(self.hwnd, x, y) for x, y in pts]
        pointer(*screen[0], 0); time.sleep(0.05); pointer(*screen[0], 2)
        for point in screen[1:]:
            pointer(*point, 0); time.sleep(0.02)
        pointer(*screen[-1], 4); time.sleep(0.25); return f"drew {arg}"
STRATEGY = {"strategy": {"type": "choice", "instructions": "The beat at index is current. Trace length equals how many beats finished, in order.",
    "criteria": {"obey": "Trace length equals index, so the beat at index has not been run.",
                 "skip": "Trace length is greater than index, so that beat was already run.", "halt": "The sentence is finished or must not continue."}}}
DONE = {"done": {"type": "noul", "instructions": "Does the trace list one result for every beat?",
                 "criteria": {"true": "Trace length equals the number of beats.", "false": "A beat has no result."}}}
def wake(sentence):
    with lease('desktop'):
        beats = [line.strip().partition(" ")[::2] for line in sentence.splitlines() if line.strip()]
        labels = [f"{verb} {arg}" for verb, arg in beats]
        desk, trace, index = Desk(), [], 0
        log("wake", sentence.strip())
        while index < len(beats):
            choice = decide({"beats": labels, "index": index, "trace": trace}, STRATEGY)["strategy"]["choice"]
            log("jev", f"{index} {choice}")
            if choice == "skip": index += 1; continue
            if choice != "obey": break
            log("run", labels[index]); trace.append(desk.run(beats[index]))
            log("did", trace[-1]); index += 1
        log("done", json.dumps(decide({"beats": labels, "trace": trace}, DONE)["done"]))
BRAIN = 'grok-4.7-medium-fast'
BRAINS = tuple('grok-4.7-' + level + speed for level in ('low', 'medium', 'high', 'xhigh') for speed in ('', '-fast'))
CREATE_NO_WINDOW = 0x08000000
CREATE_SUSPENDED = 0x00000004
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
ntdll = ctypes.WinDLL('ntdll', use_last_error=True)
kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
kernel32.CreateJobObjectW.restype = wintypes.HANDLE
kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
kernel32.SetInformationJobObject.restype = wintypes.BOOL
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CreateMutexW.argtypes, kernel32.CreateMutexW.restype = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR), wintypes.HANDLE
kernel32.ReleaseMutex.argtypes, kernel32.ReleaseMutex.restype = (wintypes.HANDLE,), wintypes.BOOL
ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
ntdll.NtResumeProcess.restype = ctypes.c_long
class JobBasicLimits(ctypes.Structure):
    _fields_ = [
        ('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
        ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
        ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD),
    ]
class JobLimits(ctypes.Structure):
    _fields_ = [
        ('BasicLimitInformation', JobBasicLimits), ('IoInfo', ctypes.c_uint64 * 6),
        ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
        ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t),
    ]
def wincheck(ok):
    if not ok: raise ctypes.WinError(ctypes.get_last_error())

@contextmanager
def lease(name):
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, True, 'Local\\Trident-' + name)
    wincheck(handle)
    if ctypes.get_last_error() == 183:
        wincheck(kernel32.CloseHandle(handle)); raise Blocked(f'{name} already has a Trident owner')
    try: yield
    finally:
        try: wincheck(kernel32.ReleaseMutex(handle))
        finally: wincheck(kernel32.CloseHandle(handle))
def write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_bytes(text.encode('utf-8') if isinstance(text, str) else text)
    os.replace(temp, path)
def record(path, text):
    with open(path, 'a', encoding='utf-8') as handle: handle.write(text)
    sys.stdout.write(text); sys.stdout.flush()
def heading(run, kind, text=''):
    body = '' if text is None else str(text)
    record(run, f"## {time.strftime('%H:%M:%S')} {kind}\n\n" + (body + '\n\n' if body else ''))
def choose_brain(sentence, model):
    if model:
        if model not in BRAINS: raise SystemExit('unknown mind model: ' + model)
        return model
    return next((slug for slug in sorted(BRAINS, key=len, reverse=True) if slug in sentence.lower()), BRAIN)
def open_job():
    job = kernel32.CreateJobObjectW(None, None)
    wincheck(job)
    limits = JobLimits()
    limits.BasicLimitInformation.LimitFlags = 0x2000
    try: wincheck(kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
    except BaseException:
        wincheck(kernel32.CloseHandle(job)); raise
    return job
def close_job(job):
    if job:
        wincheck(kernel32.CloseHandle(job))
def utc():
    return datetime.now(timezone.utc).isoformat()
def capture(path, end=None):
    command = "$ErrorActionPreference='Stop'; Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
    command += 'Add-Type \'using System.Runtime.InteropServices; public class Dpi { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }\'; [Dpi]::SetProcessDPIAware() | Out-Null; '
    command += "$r=[System.Windows.Forms.SystemInformation]::VirtualScreen; $b=New-Object System.Drawing.Bitmap $r.Width,$r.Height; $g=[System.Drawing.Graphics]::FromImage($b); "
    command += "$g.CopyFromScreen($r.X,$r.Y,0,0,$r.Size); $b.Save('" + str(path).replace("'", "''") + "',[System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $b.Dispose()"
    try: subprocess.run(['powershell.exe', '-NoProfile', '-Command', command], check=True, timeout=remaining(end), creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired: raise Deadline('goal deadline reached') from None
    remaining(end)
    return path
class Deadline(Exception): pass
class Blocked(Exception): pass
def remaining(end):
    if end is None: return None
    seconds = end - time.monotonic()
    if seconds <= 0: raise Deadline('goal deadline reached')
    return seconds
class ACP:
    def __init__(self, mission):
        self.mission, self.inbox, self.sequence, self.text = mission, queue.Queue(), 0, {}
        self.roles, self.tools, self.active, self.proc, self.job = {}, {}, None, None, None
        self.loading = None
        mission.state['pending_tools'] = self.tools
        path = mission.work / 'agent/config/cli-config.json'
        config = json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(version=1)
        config.update(permissions=dict(allow=[], deny=[]), approvalMode='allowlist', sandbox=dict(mode='disabled'))
        write(path, json.dumps(config, ensure_ascii=False))
        self.job = open_job()
        env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
        env.update(CURSOR_CONFIG_DIR=str(mission.work / 'agent/config'), CURSOR_DATA_DIR=str(mission.work / 'agent/data'))
        self.proc = subprocess.Popen(['powershell.exe', '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(Path(env['LOCALAPPDATA']) / 'cursor-agent/agent.ps1'), '--trust', '--workspace', str(mission.work), 'acp'],
            cwd=mission.work, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW | CREATE_SUSPENDED)
        try:
            wincheck(kernel32.AssignProcessToJobObject(self.job, self.proc._handle))
            status = ntdll.NtResumeProcess(self.proc._handle)
            if status: raise OSError(f'NtResumeProcess {status:#x}')
        except BaseException:
            self.proc.kill(); raise
        for pipe, kind in ((self.proc.stdout, 'stdout'), (self.proc.stderr, 'stderr')):
            threading.Thread(target=self.pump, args=(pipe, kind), daemon=True).start()
    def pump(self, pipe, kind):
        try:
            for line in pipe: self.inbox.put((kind, line))
        finally: pipe.close(); self.inbox.put((kind, None))
    def send(self, message):
        self.proc.stdin.write((json.dumps(dict(jsonrpc='2.0', **message)) + '\n').encode()); self.proc.stdin.flush()
    def rpc(self, method, params):
        self.sequence += 1
        identity = self.sequence
        request = dict(id=identity, method=method, params=params)
        started = time.monotonic()
        while True:
            remaining(self.mission.end)
            if request is not None:
                self.send(request); request = None
                if method == 'session/prompt': self.mission.record('prompt-start', dict(time=utc(), session_id=params['sessionId'], request_id=identity,
                    text_bytes=sum(len(c['text'].encode('utf-8')) for c in params['prompt'] if c['type'] == 'text')))
            try: kind, raw = self.inbox.get(timeout=0.2)
            except queue.Empty: continue
            if kind == 'stderr':
                if raw: self.mission.record('diagnostic', raw.decode('utf-8'))
                continue
            if raw is None: raise EOFError('Cursor ACP exited before its response')
            item = json.loads(raw)
            if 'method' in item:
                if item['method'] == 'session/request_permission':
                    event = self.tools[item['params']['toolCall']['toolCallId']]
                    allowed = event['role'] == 'actor' and event['session_id'] == self.mission.state['sessions']['actor'] or event['role'] == 'delegate' and event['kind'] in ('read', 'search', 'edit', 'fetch') or event['role'] == 'planner' and event['kind'] in ('read', 'search', 'fetch')
                    event['permission'] = allowed
                    if not allowed: event['error'] = 'controller denied command or desktop authority'
                    option = next(o['optionId'] for o in item['params']['options'] if o['kind'] == ('allow_once' if allowed else 'reject_once'))
                    self.send(dict(id=item['id'], result={'outcome': {'outcome': 'selected', 'optionId': option}}))
                    self.mission.save('permission', dict(tool=event['toolCallId'], role=event['role'], allowed=allowed))
                elif item['method'] in ('cursor/task', 'cursor/update_todos', 'cursor/generate_image'):
                    self.mission.record('subagent' if item['method'] == 'cursor/task' else item['method'], item['params'])
                    if 'id' in item: self.send(dict(id=item['id'], result={}))
                elif 'id' in item: raise Blocked(json.dumps(item['params'], ensure_ascii=False))
                elif item['method'] == 'session/update': self.update(item['params'])
                continue
            if item['id'] == identity:
                if method == 'session/prompt': self.mission.record('prompt-result', dict(time=utc(), session_id=params['sessionId'], request_id=identity, seconds=time.monotonic() - started, response=item))
                if 'error' in item: raise RuntimeError(json.dumps(item['error'], ensure_ascii=False))
                return item['result']
    def update(self, params):
        update, session = params['update'], params['sessionId']
        if update['sessionUpdate'] == 'usage_update':
            self.mission.record('usage', dict(time=utc(), session_id=session, request_id=self.sequence if session == self.active else None, update=update)); return
        if self.loading is not None:
            self.mission.record('session-history', params); return
        kind, role = update['sessionUpdate'], self.roles[session]
        if kind == 'subagent_spawned':
            self.roles[update['subagentSessionId']] = 'delegate'; self.mission.record('subagent', update)
        if kind == 'agent_message_chunk':
            if role == 'delegate': self.mission.record(role, update['content']['text'])
            else:
                self.text[session] = self.text.get(session, '') + update['content']['text']
                if session != self.active: self.mission.record(role, update['content']['text'])
        elif kind in ('tool_call', 'tool_call_update'):
            identity = update['toolCallId']
            if kind == 'tool_call':
                if role == 'planner' and self.mission.state['policy'] is None: raise ValueError('planner must declare the goal policy before using tools')
                if self.text.get(session): self.mission.record(role, self.text[session]); self.text[session] = ''
                self.tools[identity] = dict(role=role, session_id=session, **update)
            else: self.tools[identity].update(update)
            event = self.tools[identity]
            if event['status'] in ('completed', 'failed'):
                del self.tools[identity]; self.mission.outcome(dict(time=utc(), **event))
            else: self.mission.save('tool-start', dict(role=role, toolCallId=identity, title=event['title'], status=event['status']))
    def session(self, role, identity=None):
        if identity: self.roles[identity] = role
        self.loading = identity
        try:
            result = self.rpc('session/load' if identity else 'session/new',
                dict(cwd=str(self.mission.work), mcpServers=[], **({'sessionId': identity} if identity else {})))
        finally: self.loading = None
        identity = result['sessionId'] if 'sessionId' in result else identity
        self.roles[identity] = role
        model = self.mission.state['model']
        for key, value in (('model', 'grok-4.7'), ('reasoning_effort', model.removeprefix('grok-4.7-').removesuffix('-fast')), ('fast', str(model.endswith('-fast')).lower())):
            configuration = self.rpc('session/set_config_option', dict(sessionId=identity, configId=key, value=value))
        self.mission.state.setdefault('runtime_models', {})[identity] = {option['id']: option['currentValue'] for option in configuration['configOptions'] if option['id'] != 'mode'}
        self.rpc('session/set_mode', dict(sessionId=identity, modeId='ask' if role == 'planner' else 'agent'))
        return identity
    def prompt(self, role, text, images=(), files=()):
        self.active = session = self.mission.state['sessions'][role]
        self.text[session] = ''
        write(self.mission.work / 'prompt.txt', text)
        content = [dict(type='text', text=text)]
        for path in dict.fromkeys([*images, *files]):
            path = self.mission.resolve(path)
            data, digest, size = self.mission.read(path, True)
            mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}.get(path.suffix.lower())
            content.append(dict(type='text', text=json.dumps(dict(path=str(path), sha256=digest, bytes=size))))
            content.append(dict(type='image', mimeType=mime, data=base64.b64encode(data).decode()) if mime else
                dict(type='text', text=data.decode('utf-8') if path.suffix.lower() in ('.txt', '.json', '.md', '.py', '.csv', '.html', '.svg', '.log') else base64.b64encode(data).decode()))
            if role == 'planner': self.mission.state['reviews'][str(path)] = digest
        result = self.rpc('session/prompt', dict(sessionId=self.active, prompt=content))
        self.active = None
        if result['stopReason'] != 'end_turn': raise RuntimeError(f"Cursor stopped: {result['stopReason']}")
        self.mission.record(role, self.text[session])
        return json.loads(self.text[session]) if role == 'planner' else dict(summary=self.text[session])
    def cancel(self):
        if self.active:
            self.send(dict(method='session/cancel', params=dict(sessionId=self.active)))
            self.mission.save('cancel', dict(session_id=self.active, time=utc()))
            self.active = None
    def close(self):
        close_job(self.job)
        if self.proc is not None: self.proc.stdin.close()
PLANNER = '''You are Trident's persistent planner and validator. The human goal is the only assignment. Guide the actor throughout execution using observed tool outcomes and actual images, not promises. You may Read and search; you have no edit, shell or desktop-input authority. Historical context is earlier experience, not the current world. Retrieve relevant older experience from ledger.md by run and JSON kind; kind=final identifies a wake summary. Keep policy and criteria fixed, but revise the plan and action when observations change.
Plan one meaningful observable state change at a time. For desktop work choose one interface change or small visible work region, then inspect its resulting screenshot before planning dependent input. Do not batch the entire deliverable into an unobserved script. Use supplied images directly without redundant recapture or controller inspection. The actor may execute scripts and discover the installed application's supported launch interface; neither an unexecuted script nor a successful process exit proves the requested effect. Give executable instructions using the supplied helper contracts, without invented budgets, preparatory checklists or approvals.
Your final answer must be one JSON object without markdown. Progress preceding tool calls is not a decision. Return {"policy":{"deadline":null,"source":null,"desktop":false},"criteria":["goal acceptance condition"],"plan":["step"],"decision":"act","action":null,"checks":[{"criterion":"goal acceptance condition","passed":false,"evidence":[],"reason":"not observed"}],"handover":null,"message":"finding or next action"}.
First derive only policy and criteria from the goal and supplied timestamps, without tools, executable actions or assertions about an unobserved environment. Set decision=act, action=null and every check passed=false. deadline is a timezone-bearing ISO 8601 timestamp or null for an untimed goal; source is the exact goal timing quote or null. Calculate relative limits from started_at. desktop states whether desktop interaction is required. Subsequent turns may use Read and search and must repeat policy and criteria exactly. Timing and handover come only from the goal, never a fixed tool cap, closing window or retry rule.
Subsequent decisions are act, complete, handover or blocked. Cite the supplied current screenshot, step record and output artifacts for checks. A partial Read can guide planning but cannot authorize an entire file. The controller supplies any newly cited passing-check evidence for one final verdict; that verdict must not introduce additional unseen evidence. Complete requires a passing, evidence-supported check for EVERY criterion. Screenshots must show the requested result, not just setup. state.json, story.txt and ledger.md are mutable memory, not completion proof; step records preserve the observed tool and session history. Failures remain recorded after repair. Additional agents must have genuine recorded IDs and no desktop-input authority. For handover return {"continue":true or false,"session_id":null or a known actor session ID,"instructions":"verified progress, unresolved failures, and next action"}; choose continuation from the goal. Use blocked for missing information. The controller alone writes state.json, story.txt, ledger.md and stop.'''
ACTOR = '''You are Trident's acting agent. Execute the planner's current action, producing one meaningful observable state change, then report actual results and existing evidence paths. For desktop work finish one interface change or small visible work region and return for screenshot validation before dependent actions. Do not turn the whole deliverable into an unobserved batch. If the action or an input precondition fails, stop and report so the planner can choose the next action. Do not add defensive wrappers, fallback executables, retry loops or compatibility paths. Executed scripts are valid tools; a script file alone is not an outcome. Discovering the installed native application's supported launch interface is allowed. You alone own desktop input; delegates may read, research or work on files, never drive the desktop. Respect the human goal's constraints. Inspect the actual current interface and verify target coordinates before input. Check window and input API results and stop on failure rather than merely printing an error. Use native application tools; process exit is not visual proof. Import the supplied helpers instead of rebuilding them or inspecting their implementation just to learn their signatures. Never edit controller-owned state.json, story.txt, ledger.md or stop, fabricate an artifact or hide a failure.'''
HANDS = '''
Windows helpers in the supplied body path: windows(title_suffix) returns visible matching HWNDs; focus(hwnd) activates the target, restores it only if minimized, preserves normal window bounds and raises on failed activation. at(hwnd,x,y) converts client pixels to physical screen pixels. pointer(x,y,flags) uses physical virtual-desktop pixels: 0 moves, 2 presses left, 4 releases left. chord(*pairs) takes (virtual_key,flags), where 0 presses and 2 releases. capture(path,end=None) saves the whole virtual desktop as PNG and returns path; end, when supplied, is a monotonic deadline. The controller supplies post-action captures. Screenshot metadata gives original pixel dimensions and virtual-screen origin; convert coordinates from any resized display to those physical pixels before input. Use measured accessibility bounds when available. A successful SendInput count establishes insertion, not that the intended control received it.
desk=Desk(); desk.hwnd=hwnd targets an existing window. desk.open('title_suffix|command') launches the command, waits for its window, sets desk.hwnd and activates without maximizing. desk.paste(text) activates the target then replaces the focused control's contents using Ctrl+A/Ctrl+V. desk.watch('title_suffix vk') pastes its instructions and blocks until that foreground window receives the numeric virtual-key press. desk.draw('circle' or 'rectangle') traces geometry with the application's current tool; it does not select a brush or establish the goal's visual success. These helpers raise on checked failures; let failures reach the planner.'''
PLANNER += HANDS
ACTOR += HANDS
def failed(value):
    if isinstance(value, dict):
        if 'toolCallId' in value: return value.get('status') == 'failed' or bool(value.get('error')) or failed(value.get('rawOutput'))
        return value.get('status') == 'failed' or value.get('exitCode') not in (None, 0) or bool(value.get('failure') or value.get('error') or value.get('rejected') or value.get('isError')) or any(failed(v) for v in value.values())
    return isinstance(value, list) and any(failed(v) for v in value)
class Mission:
    def __init__(self, work, goal, model, run, started_at):
        self.work, self.end = Path(work).resolve(), None
        self.work.mkdir(parents=True, exist_ok=True)
        self.run = run or time.strftime('%Y%m%d_%H%M%S')
        self.path, self.evidence = self.work / f'run_{self.run}.md', self.work / 'evidence' / self.run
        self.evidence.mkdir(parents=True)
        prior_state = (self.work / 'state.json').exists()
        previous = json.loads((self.work / 'state.json').read_text(encoding='utf-8')) if prior_state else {
            name: (self.work / name).read_text(encoding='utf-8-sig') for name in ('goal.txt', 'story.txt', 'campaign.txt', 'lesson.txt') if (self.work / name).exists()}
        self.previous = None
        if previous:
            archive = self.evidence / 'previous.json'
            write(archive, json.dumps(previous, ensure_ascii=False, indent=2))
            memory = {k: v for k, v in previous.items() if k in ('goal', 'run', 'status', 'sessions', 'agents', 'checks', 'plan', 'action', 'failures', 'pending_tools', 'handover')} if prior_state else previous
            self.previous = dict(kind='historical state' if prior_state else 'legacy text', path=str(archive), contents=memory)
        self.state = dict(goal=goal, model=model, run=self.run, started_at=started_at, status='planning', sessions={}, agents=[],
            policy=None, criteria=[], plan=[], action=None, checks=[], observations=[], failures=[], evidence=[], reviews={}, latest_artifacts=[], handover=None, result=None,
            step_evidence=None, desktop=None, timing=dict(bootstrap_limitation='Free-form timing cannot be enforced before the planner returns its policy'))
        (self.work / 'stop').unlink(missing_ok=True)
        write(self.work / 'goal.txt', goal + '\n'); self.save('goal', goal)
    def record(self, kind, value):
        heading(self.path, kind, value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
        if kind == 'subagent':
            self.state['agents'].append(value); self.save('delegation', value)
    def save(self, kind, value):
        self.state['updated_at'] = utc()
        self.state['error_count'] = len(self.state['failures'])
        write(self.work / 'state.json', json.dumps(self.state, ensure_ascii=False, indent=2))
        story = {key: self.state[key] for key in ('goal', 'run', 'status', 'plan', 'failures', 'action', 'handover')}
        story.update(verified=[c for c in self.state['checks'] if c['passed'] is True], pending_tools=self.state.get('pending_tools', {}))
        write(self.work / 'story.txt', json.dumps(story, ensure_ascii=False, indent=2) + '\n')
        if kind == 'tool': value = dict(**{key: value[key] for key in ('role', 'toolCallId', 'title', 'status')}, failed=failed(value), error=value.get('rawOutput') if failed(value) else None)
        body = json.dumps(dict(run=self.run, status=self.state['status'], kind=kind, data=value), ensure_ascii=False)
        with (self.work / 'ledger.md').open('a', encoding='utf-8') as ledger: ledger.write(f"## {'wake' if kind == 'final' else 'event'} {self.run} {utc()}\n{body}\n\n")
    def outcome(self, event):
        self.state['observations'].append(event)
        if failed(event): self.state['failures'].append(event)
        self.record('tool', event); self.save('tool', event)
    def resolve(self, path):
        path = Path(path)
        return path.resolve() if path.is_absolute() else (self.work / path).resolve()
    def read(self, path, contents=False):
        digest, size, chunks = hashlib.sha256(), 0, []
        remaining(self.end)
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b''):
                remaining(self.end); digest.update(chunk); size += len(chunk)
                if contents: chunks.append(chunk)
        remaining(self.end)
        return b''.join(chunks) if contents else None, digest.hexdigest(), size
    def artifact(self, path, kind='artifact'):
        path = self.resolve(path)
        _, digest, size = self.read(path)
        item = dict(path=str(path), sha256=digest, bytes=size, kind=kind)
        self.state['evidence'] = [e for e in self.state['evidence'] if e['path'] != str(path)] + [item]
        return path
    def unreviewed(self, checks):
        missing = []
        for path in sorted({p for c in checks if c['passed'] is True for p in c['evidence']}):
            path = self.artifact(path)
            if self.state['reviews'].get(str(path)) != self.state['evidence'][-1]['sha256']: missing.append(str(path))
        return missing
    def files(self):
        found = {}
        ignored = {self.work / name for name in ('agent', '.git', 'state.json', 'story.txt', 'ledger.md', 'prompt.txt', 'goal.txt', 'stop')} | {self.path, self.evidence / 'previous.json'}
        for base, directories, names in os.walk(self.work):
            remaining(self.end)
            directories[:] = [name for name in directories if Path(base) / name not in ignored]
            for name in names:
                remaining(self.end)
                path = Path(base) / name
                if path not in ignored: found[path] = (path.stat().st_mtime_ns, path.stat().st_size)
        return found
    def observe(self, name):
        screen = dict(zip(('x', 'y', 'width', 'height'), (U.GetSystemMetrics(n) for n in (76, 77, 78, 79))))
        image = capture(self.evidence / f'{name}.png', self.end)
        with image.open('rb') as handle: header = handle.read(24)
        self.state['desktop'] = dict(path=str(image), observed_at=utc(), screen=screen, image_width=int.from_bytes(header[16:20], 'big'), image_height=int.from_bytes(header[20:24], 'big'), units='physical virtual-desktop pixels')
        self.artifact(image, 'screenshot'); self.save('observation', self.state['desktop'])
        return image
    def decision(self, decision):
        policy = decision['policy']
        if self.state['policy'] is None:
            if policy['deadline'] is not None:
                if not policy['source'] or policy['source'] not in self.state['goal']: raise ValueError('deadline has no goal evidence')
                deadline = datetime.fromisoformat(policy['deadline'])
                if deadline.tzinfo is None: raise ValueError('deadline requires a timezone')
                self.end = time.monotonic() + deadline.timestamp() - time.time()
            self.state['policy'], self.state['criteria'] = policy, decision['criteria']
            self.state['timing'].update(policy_at=utc(), bootstrap_seconds=time.time() - datetime.fromisoformat(self.state['started_at']).timestamp())
        elif policy != self.state['policy'] or decision['criteria'] != self.state['criteria']: raise ValueError('goal policy or acceptance criteria changed')
        if not self.state['criteria']: raise ValueError('goal requires acceptance criteria')
        for key in ('plan', 'action', 'checks', 'handover'): self.state[key] = decision[key]
        self.save('planner', decision)
        remaining(self.end)
        return decision['decision']
    def verify(self, complete=True):
        remaining(self.end)
        checks = self.state['checks'] if complete else [c for c in self.state['checks'] if c['passed'] is True]
        if complete and sorted(c['criterion'] for c in checks) != sorted(self.state['criteria']): raise ValueError('verification must cover every goal criterion')
        if not all(c['passed'] is True and c['evidence'] and c['reason'] for c in checks): raise ValueError('goal has unverified criteria')
        missing = self.unreviewed(checks)
        if missing: raise ValueError(f'evidence was not supplied to the planner at its current hash: {missing}')
        remaining(self.end)
    def context(self):
        keys = ('goal', 'model', 'run', 'started_at', 'status', 'sessions', 'agents', 'runtime_models', 'policy', 'criteria', 'plan', 'action', 'checks', 'handover', 'result', 'pending_tools', 'step_evidence', 'desktop', 'timing', 'failures', 'latest_artifacts')
        return dict(now=utc(), body=str(ROOT / 'main.py'), previous=self.previous if self.state['policy'] is None else None, ledger=str(self.work / 'ledger.md'),
            **{k: v for k, v in self.state.items() if k in keys})
def drive(work, goal, model='', run=''):
    started_at, work = utc(), Path(work).resolve()
    with ExitStack() as ownership:
        ownership.enter_context(lease('work-' + hashlib.sha256(os.path.normcase(str(work)).encode('utf-8')).hexdigest()))
        mission, client = Mission(work, goal, choose_brain(goal, model), run, started_at), None
        state = mission.state
        try:
            client = ACP.__new__(ACP)
            client.__init__(mission)
            client.rpc('initialize', dict(protocolVersion=1, clientCapabilities=dict(fs=dict(readTextFile=False, writeTextFile=False), terminal=False,
                _meta=dict(parameterizedModelPicker=True, subagents=True)), clientInfo=dict(name='trident', version='1')))
            for role in ('actor', 'planner'): state['sessions'][role] = client.session(role)
            state['agents'] = [dict(role=role, session_id=sid) for role, sid in state['sessions'].items()]
            mission.save('sessions', state['agents'])
            image, step = None, 0
            while True:
                state['status'] = 'planning'; mission.save('planning', state['action'])
                initial = state['policy'] is None
                instruction = 'POLICY ONLY: interpret the goal and historical context without tools. Return policy and criteria, decision=act, action=null and no passing checks. Do not assert an unobserved environment or give an executable action.' if initial else 'Inspect the supplied current image and complete step record. Choose one next observable action or a verified decision. Cite the exact evidence needed for passing checks. Read and search are available.'
                files = [state['step_evidence']] if state['step_evidence'] else []
                decision = client.prompt('planner', PLANNER + '\n' + json.dumps(mission.context(), ensure_ascii=False) + '\n' + instruction, [image] if image else [], files)
                if initial:
                    if decision['decision'] != 'act' or decision['action'] is not None or any(c['passed'] is not False for c in decision['checks']): raise ValueError('initial policy cannot claim an action or verified state')
                    mission.decision(decision)
                    if state['policy']['desktop']:
                        ownership.enter_context(lease('desktop'))
                        image = mission.observe('initial')
                    continue
                missing = mission.unreviewed(decision['checks'])
                if missing:
                    mission.save('evidence-review', decision)
                    decision = client.prompt('planner', PLANNER + '\nFINAL EVIDENCE VERDICT: inspect exactly the newly attached evidence. Return the full decision JSON using only evidence already supplied. Do not introduce unseen citations.\n' + json.dumps(mission.context(), ensure_ascii=False), files=missing)
                    if mission.unreviewed(decision['checks']): raise ValueError('final verdict introduced evidence not supplied at its current hash')
                action = mission.decision(decision)
                mission.verify(action == 'complete')
                if action == 'complete':
                    remaining(mission.end); state['verified_at'] = utc(); state['status'] = 'completed'; break
                if action == 'blocked': state['status'] = 'blocked'; break
                if action == 'handover':
                    handover = state['handover']
                    if not handover['instructions']: raise ValueError('handover requires instructions')
                    if not handover['continue']: state['status'] = 'handover'; break
                    state['sessions']['actor'] = client.session('actor', handover['session_id'])
                    state['agents'].append(dict(role='actor', session_id=state['sessions']['actor']))
                    mission.save('handover', handover)
                elif action != 'act': raise ValueError(f'unknown planner decision: {action}')
                if not state['action']: raise ValueError('actor requires an executable next action')
                step += 1
                state['status'] = 'acting'; mission.save('in-flight', state['action'])
                offset = len(state['observations'])
                before = mission.files()
                result = client.prompt('actor', ACTOR + '\n' + json.dumps(mission.context(), ensure_ascii=False), [image] if image else [])
                if (mission.work / 'stop').exists(): raise ValueError('actor created a controller-owned stop file')
                state['result'] = result; state['status'] = 'validating'
                after = mission.files()
                state['latest_artifacts'] = [str(mission.artifact(path)) for path, stamp in after.items() if before.get(path) != stamp]
                if state['policy']['desktop']: image = mission.observe(str(step))
                evidence = mission.evidence / f'{step}.json'
                paths = set(state['latest_artifacts']) | ({str(image)} if image else set())
                write(evidence, json.dumps(dict(result=result, sessions=state['sessions'], agents=state['agents'], handover=state['handover'], policy=state['policy'], desktop=state['desktop'],
                    written=state['latest_artifacts'], artifacts=[e for e in state['evidence'] if e['path'] in paths], deleted=[str(path) for path in before if path not in after], tools=state['observations'][offset:]), ensure_ascii=False, indent=2))
                state['step_evidence'] = str(mission.artifact(evidence, 'tool outcomes'))
                state['latest_artifacts'].append(str(evidence)); mission.save('actor', result)
        except Deadline as error:
            state['status'] = 'deadline'; state['failures'].append(dict(time=utc(), error=str(error)))
        except Blocked as error:
            state['status'] = 'blocked'; state['failures'].append(dict(time=utc(), error=str(error)))
        except BaseException as error:
            state['status'] = 'failed'; state['failures'].append(dict(time=utc(), error=f'{type(error).__name__}: {error}'))
            raise
        finally:
            try:
                if client is not None:
                    try:
                        if state['status'] == 'deadline': client.cancel()
                    finally: client.close()
            except BaseException as error:
                state['status'] = 'failed'; state['failures'].append(dict(time=utc(), error=f'cleanup {type(error).__name__}: {error}'))
                raise
            finally:
                if state['status'] == 'completed':
                    try: remaining(mission.end)
                    except Deadline as error: state['status'] = 'deadline'; state['failures'].append(dict(time=utc(), error=str(error)))
                if state['status'] != 'completed': (mission.work / 'stop').unlink(missing_ok=True)
                final = {key: state[key] for key in ('goal', 'run', 'status', 'plan', 'checks', 'failures', 'action', 'handover')}
                final.update(pending_tools=state['pending_tools'], elapsed_seconds=time.time() - datetime.fromisoformat(state['started_at']).timestamp())
                mission.save('final', final)
                if state['status'] == 'completed': write(mission.work / 'stop', '')
                mission.record('final', final)
        return state['status']

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == 'serve': return Journal(ROOT).serve()
    if argv and not argv[0].startswith('-'): return wake(Path(argv[0]).read_text(encoding='utf-8-sig'))
    parser = argparse.ArgumentParser(prog='trident')
    goal = parser.add_mutually_exclusive_group(required=True)
    goal.add_argument('--sentence'); goal.add_argument('--mind-file')
    for name in ('work', 'model', 'run'): parser.add_argument('--' + name, default='')
    args = parser.parse_args(argv)
    sentence = args.sentence if args.sentence is not None else Path(args.mind_file).read_text(encoding='utf-8-sig')
    if not sentence.strip(): raise ValueError('the human goal is empty')
    status = drive(args.work or ROOT, sentence.strip(), args.model, args.run)
    return dict(completed=0, deadline=2, handover=2, blocked=3)[status]
if __name__ == '__main__':
    try: sys.exit(main())
    except Blocked as error: sys.stderr.write(str(error) + '\n'); sys.exit(3)
    except KeyboardInterrupt: sys.exit(1)
