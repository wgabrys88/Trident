import argparse, base64, ctypes, hashlib, json, math, os, queue, socket, subprocess, sys, threading, time, urllib.request, uuid
from datetime import datetime, timezone
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
    U.ShowWindow(hwnd, 3)
    if U.GetForegroundWindow() != hwnd:
        chord((0x12, 0)); ok = U.SetForegroundWindow(hwnd); chord((0x12, 2))
        if not ok: raise OSError('SetForegroundWindow failed')
    time.sleep(0.3)
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
            while True: self._sse(box.get())
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
        self.paste("click the notepad window and hit Enter\n\n" + WATCH)
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
def capture(path):
    command = "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
    command += 'Add-Type \'using System.Runtime.InteropServices; public class Dpi { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }\'; [Dpi]::SetProcessDPIAware() | Out-Null; '
    command += "$r=[System.Windows.Forms.SystemInformation]::VirtualScreen; $b=New-Object System.Drawing.Bitmap $r.Width,$r.Height; $g=[System.Drawing.Graphics]::FromImage($b); "
    command += "$g.CopyFromScreen($r.X,$r.Y,0,0,$r.Size); $b.Save('" + str(path).replace("'", "''") + "',[System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $b.Dispose()"
    subprocess.run(['powershell.exe', '-NoProfile', '-Command', command], check=True, creationflags=CREATE_NO_WINDOW)
    return path
class Deadline(Exception): pass
class Blocked(Exception): pass
class ACP:
    def __init__(self, mission):
        self.mission, self.inbox, self.sequence, self.text = mission, queue.Queue(), 0, ''
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
        for line in pipe: self.inbox.put((kind, line))
        self.inbox.put((kind, None))
    def send(self, message):
        self.proc.stdin.write((json.dumps(dict(jsonrpc='2.0', **message)) + '\n').encode()); self.proc.stdin.flush()
    def rpc(self, method, params):
        self.sequence += 1
        identity = self.sequence
        request = dict(id=identity, method=method, params=params)
        while True:
            if self.mission.end is not None and time.monotonic() >= self.mission.end:
                self.cancel(); raise Deadline('goal deadline reached')
            if request is not None: self.send(request); request = None
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
                if 'error' in item: raise RuntimeError(json.dumps(item['error'], ensure_ascii=False))
                return item['result']
    def update(self, params):
        if self.loading is not None:
            self.mission.record('session-history', params); return
        update, session = params['update'], params['sessionId']
        kind, role = update['sessionUpdate'], self.roles[session]
        if kind == 'subagent_spawned':
            self.roles[update['subagentSessionId']] = 'delegate'; self.mission.record('subagent', update)
        if kind == 'agent_message_chunk':
            if role == 'delegate': self.mission.record(role, update['content']['text'])
            else: self.text += update['content']['text']
        elif kind in ('tool_call', 'tool_call_update'):
            identity = update['toolCallId']
            if kind == 'tool_call':
                if role == 'planner' and self.mission.state['policy'] is None: raise ValueError('planner must declare the goal policy before using tools')
                if self.text: self.mission.record(role, self.text); self.text = ''
                self.tools[identity] = dict(role=role, session_id=session, **update)
            else: self.tools[identity].update(update)
            event = self.tools[identity]
            if event['status'] in ('completed', 'failed'):
                del self.tools[identity]; self.mission.outcome(dict(time=utc(), **event))
            else: self.mission.save('tool-start', dict(role=role, toolCallId=identity, title=event['title'], status=event['status']))
        elif kind == 'agent_thought_chunk': pass
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
        self.active, self.text = self.mission.state['sessions'][role], ''
        write(self.mission.work / 'prompt.txt', text)
        content = [dict(type='text', text=text)]
        for path in [*images, *files]:
            path = Path(path).resolve() if Path(path).is_absolute() else (self.mission.work / path).resolve()
            data = path.read_bytes(); digest = hashlib.sha256(data).hexdigest()
            if role == 'planner': self.mission.state['reviews'][str(path)] = digest
            content.append(dict(type='text', text=json.dumps(dict(path=str(path), sha256=digest, bytes=len(data)))))
            content.append(dict(type='image', mimeType='image/png', data=base64.b64encode(data).decode()) if path.suffix.lower() == '.png' else
                dict(type='text', text=data.decode('utf-8') if path.suffix.lower() in ('.txt', '.json', '.md', '.py', '.csv', '.html', '.svg', '.log') else base64.b64encode(data).decode()))
        result = self.rpc('session/prompt', dict(sessionId=self.active, prompt=content))
        self.active = None
        if result['stopReason'] != 'end_turn': raise RuntimeError(f"Cursor stopped: {result['stopReason']}")
        self.mission.record(role, self.text)
        return json.loads(self.text) if role == 'planner' else dict(summary=self.text)
    def cancel(self):
        if self.active:
            self.send(dict(method='session/cancel', params=dict(sessionId=self.active)))
            self.mission.save('cancel', dict(session_id=self.active, time=utc()))
    def close(self):
        close_job(self.job)
        if self.proc is not None:
            self.proc.wait(); self.proc.stdin.close(); self.proc.stdout.close(); self.proc.stderr.close()
PLANNER = '''You are Trident's persistent planner and validator. The human goal is the only assignment. You guide the actor from its first action through completion. Use Read, search and the supplied actual screenshots to inspect results; you have no edit, shell or desktop-input authority. Work from observed tool outcomes, not actor promises. Read relevant past experience from ledger.md when it helps. Plan one concrete executable action at a time. When the goal requires execution, scripts must actually run; writing requested code is an artifact, not proof of an external action. Additional agents must be genuine Cursor agents with recorded IDs and no desktop-input authority. You are the ongoing second agent, not an end-only reviewer.
The body supplies the current desktop image after policy creation and after each actor action. Use it directly; do not spend an actor action recapturing the same observation or inspecting the controller implementation. Give concise executable instructions using the supplied reusable hands. Do not forbid the execution tool needed for an action or invent preparatory checklists and approval steps outside the human goal.
Use the supplied context; inspect the body implementation only if the human task requires it. Send no progress prose. Your entire answer must be one JSON object. Return only JSON, without markdown: {"policy":{"deadline":null,"source":null,"desktop":false},"criteria":["goal acceptance condition"],"plan":["step"],"decision":"act","action":"one concrete action and how to check it","checks":[{"criterion":"goal acceptance condition","passed":false,"evidence":[],"reason":"observed basis"}],"handover":null,"message":"next action or verified finding"}.
Derive all timing and handover requirements from the human goal. deadline is an ISO 8601 timestamp with timezone, or null when no time limit was specified; source is an exact quote of the goal's timing instruction, or null. Calculate relative limits from started_at, not from the current time. desktop says whether this goal needs desktop interaction. After the first decision repeat policy and criteria exactly. Never invent a time limit, closing window, tool-count limit, retry, substitute runtime or missing lesson.
decision is act, complete, handover or blocked. After each action first propose a decision with the existing evidence paths to inspect. The body will supply their actual contents for your final verdict before accepting any decision. Complete requires a passing check for EVERY criterion supported by those contents or your actual Read calls. Cite output files and immutable evidence/<run>/<step>.json records for session IDs, handovers and tool outcomes; state.json, story.txt and ledger.md change continuously and cannot prove completion. Screenshots must show the requested result; setup screenshots and code alone are insufficient. A tool error stays in memory even after repair. For handover return {"continue":true or false,"session_id":null or a known actor session ID,"instructions":"verified progress, unresolved failures, and next action"}. Choose handover and continuation according to the goal, not a fixed wake rule. Complete only after the goal is met; use blocked for missing human information. Never create state.json, story.txt, ledger.md or stop: the body owns them.'''
ACTOR = '''You are Trident's acting agent. Execute exactly the planner's current action using the shortest direct tool sequence, then report actual results and existing evidence paths for validation. If it fails, stop and report so the planner can decide the next action. Do not add defensive wrappers, fallback executables, retry loops or compatibility paths. You alone own mouse and keyboard input. Delegated agents may read, research or work on files but must never drive the desktop. Respect the human goal's constraints and deliverable. When execution is requested, do not substitute scripts or future intentions. Inspect the current interface before interacting. For desktop work use native application tools and actual screenshots; a command exit is not visual proof. The body path is supplied in context. Its reusable Windows hands are Desk.open, Desk.paste, Desk.watch and Desk.draw; set Desk.hwnd to a window handle when targeting an existing window. windows, focus, chord, pointer, at and capture are also available. Import those helpers directly when useful; do not rebuild them or read the implementation without a task reason. Check input and window API results. Do not edit state.json, story.txt, ledger.md or stop. The body records outcomes and the planner decides completion. Never fabricate an artifact or hide a failure.'''
def failed(value):
    if isinstance(value, dict):
        if 'toolCallId' in value: return value.get('status') == 'failed' or bool(value.get('error')) or failed(value.get('rawOutput'))
        return value.get('status') == 'failed' or value.get('exitCode') not in (None, 0) or bool(value.get('failure') or value.get('error') or value.get('rejected') or value.get('isError')) or any(failed(v) for v in value.values())
    return isinstance(value, list) and any(failed(v) for v in value)
class Mission:
    def __init__(self, work, goal, model, run):
        self.work, self.end = Path(work).resolve(), None
        self.work.mkdir(parents=True, exist_ok=True)
        self.run = run or time.strftime('%Y%m%d_%H%M%S')
        self.path, self.evidence = self.work / f'run_{self.run}.md', self.work / 'evidence' / self.run
        self.evidence.mkdir(parents=True)
        legacy = {name: (self.work / name).read_text(encoding='utf-8-sig') for name in ('goal.txt', 'story.txt', 'campaign.txt', 'lesson.txt') if (self.work / name).exists()}
        previous = json.loads((self.work / 'state.json').read_text(encoding='utf-8')) if (self.work / 'state.json').exists() else legacy or None
        if previous is not None: write(self.evidence / 'previous.json', json.dumps(previous, ensure_ascii=False))
        self.state = dict(goal=goal, model=model, run=self.run, started_at=utc(), status='planning', sessions={}, agents=[],
            policy=None, criteria=[], plan=[], action=None, checks=[], observations=[], failures=[], evidence=[], reviews={}, latest_artifacts=[], handover=None, result=None)
        self.previous = str(self.evidence / 'previous.json') if previous is not None else None
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
        write(self.work / 'story.txt', '\n'.join(f'{i}. {step}' for i, step in enumerate(self.state['plan'], 1)) + f"\nStatus: {self.state['status']}\nNext: {self.state['action']}\n")
        if kind == 'tool': value = dict(**{key: value[key] for key in ('role', 'toolCallId', 'title', 'status')}, failed=failed(value), error=value.get('rawOutput') if failed(value) else None)
        body = json.dumps(dict(run=self.run, status=self.state['status'], kind=kind, data=value), ensure_ascii=False)
        with (self.work / 'ledger.md').open('a', encoding='utf-8') as ledger: ledger.write(f'## wake {self.run} {utc()}\n{body}\n\n')
    def outcome(self, event):
        self.state['observations'].append(event)
        if failed(event): self.state['failures'].append(event)
        elif event['role'] == 'planner' and event['kind'] == 'read':
            for location in event['locations']:
                path = Path(location['path'])
                if path.is_file(): self.state['reviews'][str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.record('tool', event); self.save('tool', event)
    def artifact(self, path, kind='artifact'):
        path = Path(path)
        path = path.resolve() if path.is_absolute() else (self.work / path).resolve()
        data = path.read_bytes()
        item = dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), kind=kind)
        self.state['evidence'] = [e for e in self.state['evidence'] if e['path'] != str(path)] + [item]
        return path
    def files(self):
        found = {}
        ignored = {self.work / name for name in ('agent', '.git', 'evidence', 'state.json', 'story.txt', 'ledger.md', 'prompt.txt', 'goal.txt', 'stop')} | {self.path}
        for base, directories, names in os.walk(self.work):
            directories[:] = [name for name in directories if Path(base) / name not in ignored]
            for name in names:
                path = Path(base) / name
                if path not in ignored: found[path] = (path.stat().st_mtime_ns, path.stat().st_size)
        return found
    def observe(self, name):
        image = capture(self.evidence / f'{name}.png')
        self.artifact(image, 'screenshot'); self.save('observation', str(image))
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
        elif policy != self.state['policy'] or decision['criteria'] != self.state['criteria']: raise ValueError('goal policy or acceptance criteria changed')
        if not self.state['criteria']: raise ValueError('goal requires acceptance criteria')
        for key in ('plan', 'action', 'checks', 'handover'): self.state[key] = decision[key]
        self.save('planner', decision)
        if self.end is not None and time.monotonic() >= self.end: raise Deadline('goal deadline reached')
        return decision['decision']
    def verify(self, complete=True):
        checks = self.state['checks'] if complete else [c for c in self.state['checks'] if c['passed'] is True]
        if complete and sorted(c['criterion'] for c in checks) != sorted(self.state['criteria']): raise ValueError('verification must cover every goal criterion')
        if not all(c['passed'] is True and c['evidence'] and c['reason'] for c in checks): raise ValueError('goal has unverified criteria')
        for check in checks:
            for path in check['evidence']:
                path = self.artifact(path)
                if self.state['reviews'].get(str(path)) != self.state['evidence'][-1]['sha256']: raise ValueError(f'evidence was not inspected by the planner: {path}')
    def context(self):
        return dict(now=utc(), body=str(ROOT / 'main.py'), previous=self.previous, ledger=str(self.work / 'ledger.md'),
            **{k: v for k, v in self.state.items() if k not in ('observations', 'failures')},
            failures=[dict(tool=f['toolCallId'], role=f['role'], output=f.get('rawOutput'), status=f['status']) for f in self.state['failures'] if 'toolCallId' in f])
def drive(work, goal, model='', run=''):
    mission, client = Mission(work, goal, choose_brain(goal, model), run), None
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
            instruction = 'INITIAL DECISION ONLY: use the supplied goal and timestamps without tools. Declare policy and criteria now and give the actor its first action. Read and search tools remain available on ALL subsequent turns.' if state['policy'] is None else 'Read tools are available NOW. Inspect the actual output files and step evidence. The reviews map records real inspections; never claim a Read absent from those observations. Return the next decision as JSON with evidence paths for the final verdict.'
            decision = client.prompt('planner', PLANNER + '\n' + json.dumps(mission.context(), ensure_ascii=False) + '\n' + instruction, [image] if image else [])
            if state['result'] is not None:
                state['proposal'] = decision; mission.save('proposal', decision)
                files = sorted({path for check in decision['checks'] for path in check['evidence']} | set(state['latest_artifacts']))
                decision = client.prompt('planner', PLANNER + '\nFINAL EVIDENCE VERDICT: inspect the attached actual file contents and images. Your preceding decision was a proposal. Return the full decision JSON after validating each check against this evidence. Never claim a tool call that did not occur.\n' + json.dumps(mission.context(), ensure_ascii=False), [image] if image else [], files=files)
            action = mission.decision(decision)
            mission.verify(action == 'complete')
            if image is None and state['policy']['desktop'] and action in ('act', 'handover'):
                image = mission.observe('initial'); continue
            if action == 'complete':
                state['status'] = 'completed'; break
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
            write(evidence, json.dumps(dict(result=result, sessions=state['sessions'], agents=state['agents'], handover=state['handover'], policy=state['policy'], written=state['latest_artifacts'], deleted=[str(path) for path in before if path not in after], tools=state['observations'][offset:]), ensure_ascii=False))
            mission.artifact(evidence, 'tool outcomes'); state['latest_artifacts'].append(str(evidence)); mission.save('actor', result)
    except Deadline as error:
        state['status'] = 'deadline'; state['failures'].append(dict(time=utc(), error=str(error)))
    except Blocked as error:
        state['status'] = 'blocked'; state['failures'].append(dict(time=utc(), error=str(error)))
    except BaseException as error:
        state['status'] = 'failed'; state['failures'].append(dict(time=utc(), error=f'{type(error).__name__}: {error}'))
        raise
    finally:
        try:
            if client is not None: client.close()
        except BaseException as error:
            state['status'] = 'failed'; state['failures'].append(dict(time=utc(), error=f'cleanup {type(error).__name__}: {error}'))
            raise
        finally:
            if state['status'] != 'completed': (mission.work / 'stop').unlink(missing_ok=True)
            final = dict(status=state['status'], failures=state['failures'], next_action=state['action'], handover=state['handover'])
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
    except KeyboardInterrupt: sys.exit(1)
