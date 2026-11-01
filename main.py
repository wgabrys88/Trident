import argparse, base64, codecs, ctypes, hashlib, json, math, os, queue, subprocess, sys, threading, time, urllib.request, uuid
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
    time.sleep(0.05)
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
def stroke(hwnd, points, pause=0.008):
    if len(points) < 2: raise ValueError('a stroke needs at least two client points')
    focus(hwnd)
    screen = [at(hwnd, int(x), int(y)) for x, y in points]
    pointer(*screen[0], 0)
    pointer(*screen[0], 2)
    for x, y in screen[1:]:
        pointer(x, y, 0)
        time.sleep(pause)
    pointer(*screen[-1], 4)
    return screen
def screen_area(desktop, x, y, width, height, shown_width=None, shown_height=None):
    shown_width = shown_width or desktop['image_width']
    shown_height = shown_height or desktop['image_height']
    sx, sy = desktop['image_width'] / shown_width, desktop['image_height'] / shown_height
    return dict(x=desktop['screen']['x'] + int(round(x * sx)), y=desktop['screen']['y'] + int(round(y * sy)), width=max(1, int(round(width * sx))), height=max(1, int(round(height * sy))))
WATCH = "import ctypes,sys,time\nu=ctypes.WinDLL('user32');u.GetForegroundWindow.restype=ctypes.c_void_p\nu.GetWindowTextW.argtypes=(ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_int)\nu.GetAsyncKeyState.argtypes=(ctypes.c_int,);u.GetAsyncKeyState.restype=ctypes.c_short\ntitle,vk=sys.argv[1],int(sys.argv[2]);buf=ctypes.create_unicode_buffer(512);last=False\nwhile True:\n h=u.GetForegroundWindow();u.GetWindowTextW(h,buf,512)\n down=bool(u.GetAsyncKeyState(vk)&0x8000)\n if buf.value.lower().endswith(title.lower()) and down and not last: break\n last=down;time.sleep(0.02)\n"
class Live(BaseHTTPRequestHandler):
    fan = None
    def log_message(self, fmt, *args): return
    def do_POST(self):
        if self.path.partition('?')[0] != '/log': self.send_error(404); return
        item = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode())
        type(self).fan.write(item["agent"], item["kind"], item["body"]); self.send_response(204); self.end_headers()
    def do_GET(self):
        fan = type(self).fan
        route = self.path.partition('?')[0]
        if route not in ('/', '/md', '/events'): self.send_error(404); return
        if route == '/events':
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache"); self.end_headers()
            with fan.lock:
                box = queue.Queue()
                for event in fan.events: box.put(event)
                fan.boxes.append(box)
            try:
                while True: self._sse(box.get())
            finally:
                with fan.lock: fan.boxes.remove(box)
            return
        with fan.lock: raw = fan.path.read_bytes() if route == '/md' else fan.html()
        kind = "text/markdown; charset=utf-8" if route == '/md' else "text/html; charset=utf-8"
        self.send_response(200); self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def _sse(self, item):
        self.wfile.write(b"data: " + json.dumps(item).encode() + b"\n\n"); self.wfile.flush()
class Journal:
    def __init__(self, root):
        self.root, self.path = root, root / f"hub_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.md"
        self.path.open('x', encoding='utf-8').close()
        self.lock, self.boxes, self.events, self.colors, self.sources = threading.Lock(), [], [], {}, {}
        Live.fan = self
    def follow(self):
        for path in sorted(self.root.glob('run_*.md')):
            if path not in self.sources: self.sources[path] = [0, codecs.getincrementaldecoder('utf-8')()]
            source = self.sources[path]
            with path.open('rb') as handle:
                handle.seek(source[0]); text = source[1].decode(handle.read()); source[0] = handle.tell()
            if text: self.write(path.stem, 'run', text)
    def write(self, agent, kind, body=""):
        with self.lock:
            if agent not in self.colors:
                self.colors[agent] = PALETTE[len(self.colors) % len(PALETTE)]
            color = self.colors[agent]
            text = f"## {time.strftime('%H:%M:%S')} {color} {agent} {kind}\n\n" + (body.rstrip() + "\n\n" if body else "")
            record(self.path, text)
            event = dict(color=color, text=text)
            self.events.append(event)
            for box in self.boxes: box.put(event)
    def html(self):
        return ("<!doctype html><meta charset=utf-8><title>Trident</title><style>body{margin:0;background:#10141a}"
                "pre{white-space:pre-wrap;margin:0;padding:8px 14px;font:14px/1.4 Consolas,monospace}</style><div id=p>"
                "</div><script>const p=document.getElementById('p'),es=new EventSource('/events');es.onopen=()=>p.replaceChildren();"
                "es.onmessage=e=>{const m=JSON.parse(e.data),d=document.createElement('pre');d.style.color=m.color;d.textContent=m.text;p.appendChild(d);scrollTo(0,9e9)}</script>").encode()
    def serve(self):
        with JournalServer(("0.0.0.0", PORT), Live) as server:
            self.write('hub', 'listen', f'http://127.0.0.1:{PORT}/')
            server.serve_forever(poll_interval=0.2)
class JournalServer(ThreadingHTTPServer):
    def service_actions(self): Live.fan.follow()

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
        with lease('desktop', wait=True): self.paste(f"Activate {title} and press Windows virtual-key code {vk}\n\n" + WATCH)
        proc = subprocess.Popen([sys.executable, "-c", WATCH, title, vk], creationflags=0x08000000)
        proc.wait(); return f"awake {title.strip()}"
    def draw(self, arg):
        focus(self.hwnd); rect = wintypes.RECT(); wincheck(U.GetClientRect(self.hwnd, ctypes.byref(rect)))
        cx, cy = rect.right // 2, int(rect.bottom * 0.58)
        pts = [(int(cx + 130 * math.cos(i * math.tau / 48)), int(cy + 130 * math.sin(i * math.tau / 48))) for i in range(49)] if arg == "circle" else [(cx - 190, cy - 80), (cx + 190, cy - 80), (cx + 190, cy + 80), (cx - 190, cy + 80), (cx - 190, cy - 80)]
        stroke(self.hwnd, pts, pause=0.02); return f"drew {arg}"
DONE = {"done": {"type": "noul", "instructions": "Does the trace list one result for every beat?",
                 "criteria": {"true": "Trace length equals the number of beats.", "false": "A beat has no result."}}}
def run_beats(sentence):
    beats = [line.strip().partition(" ")[::2] for line in sentence.splitlines() if line.strip()]
    labels = [f"{verb} {arg}" for verb, arg in beats]
    desk, trace = Desk(), []
    log("beats", sentence.strip())
    for verb, arg in beats:
        log("run", f"{verb} {arg}")
        with ExitStack() as desktop:
            if verb != 'watch': desktop.enter_context(lease('desktop', wait=True))
            trace.append({'open': desk.open, 'type': desk.paste, 'watch': desk.watch, 'draw': desk.draw}[verb](arg))
        log("did", trace[-1])
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
kernel32.WaitForSingleObject.argtypes, kernel32.WaitForSingleObject.restype = (wintypes.HANDLE, wintypes.DWORD), wintypes.DWORD
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
def lease(name, end=None, wait=False):
    handle = kernel32.CreateMutexW(None, False, 'Local\\Trident-' + name)
    wincheck(handle)
    try:
        while True:
            timeout = min(200, math.ceil(remaining(end) * 1000)) if wait and end is not None else 200 if wait else 0
            status = kernel32.WaitForSingleObject(handle, timeout)
            if status in (0, 0x80): break
            if status != 258: raise ctypes.WinError(ctypes.get_last_error())
            if not wait: raise Blocked(f'{name} already has a Trident owner')
        try: yield
        finally: wincheck(kernel32.ReleaseMutex(handle))
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
def choose_brain(sentence, model):
    if model:
        if model not in BRAINS: raise SystemExit('unknown mind model: ' + model)
        return model
    return next((slug for slug in sorted(BRAINS, key=len, reverse=True) if slug in sentence.lower()), BRAIN)
def open_job(resources):
    job = kernel32.CreateJobObjectW(None, None)
    wincheck(job)
    resources.callback(lambda: wincheck(kernel32.CloseHandle(job)))
    limits = JobLimits()
    limits.BasicLimitInformation.LimitFlags = 0x2000
    wincheck(kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
    return job

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
    def __init__(self, mission, resources):
        self.mission, self.inbox, self.sequence, self.text = mission, queue.Queue(), 0, {}
        self.roles, self.tools, self.ready, self.active = {}, {}, set(), None
        self.loading = None
        mission.state['pending_tools'] = self.tools
        path = mission.work / 'agent/config/cli-config.json'
        config = json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(version=1)
        config.update(permissions=dict(allow=[], deny=[]), approvalMode='allowlist', sandbox=dict(mode='disabled'))
        write(path, json.dumps(config, ensure_ascii=False))
        job = open_job(resources)
        env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
        env.update(CURSOR_CONFIG_DIR=str(mission.work / 'agent/config'), CURSOR_DATA_DIR=str(mission.work / 'agent/data'))
        self.proc = subprocess.Popen(['powershell.exe', '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(Path(env['LOCALAPPDATA']) / 'cursor-agent/agent.ps1'), '--trust', '--workspace', str(mission.work), 'acp'],
            cwd=mission.work, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW | CREATE_SUSPENDED)
        try:
            wincheck(kernel32.AssignProcessToJobObject(job, self.proc._handle))
            status = ntdll.NtResumeProcess(self.proc._handle)
            if status: raise OSError(f'NtResumeProcess {status:#x}')
        except BaseException:
            self.proc.kill(); self.proc.communicate(); raise
        resources.callback(self.proc.stdin.close)
        resources.callback(self.cancel)
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
        started = time.monotonic()
        remaining(self.mission.end)
        self.send(dict(id=identity, method=method, params=params))
        if method == 'session/prompt': self.mission.record('prompt-start', dict(time=utc(), session_id=params['sessionId'], request_id=identity,
            text_bytes=sum(len(c['text'].encode('utf-8')) for c in params['prompt'] if c['type'] == 'text')))
        while True:
            remaining(self.mission.end)
            try: kind, raw = self.inbox.get(timeout=0.2)
            except queue.Empty: continue
            if kind == 'stderr':
                if raw: self.mission.record('diagnostic', raw.decode('utf-8'))
                continue
            if raw is None: raise EOFError('Cursor ACP exited before its response')
            item = json.loads(raw)
            if 'method' in item:
                if item['method'] == 'session/request_permission':
                    option = next(o['optionId'] for o in item['params']['options'] if o['kind'] == 'allow_once')
                    self.send(dict(id=item['id'], result={'outcome': {'outcome': 'selected', 'optionId': option}}))
                    self.mission.save('permission', item['params'])
                elif item['method'] == 'cursor/create_plan':
                    self.mission.record('plan', item['params'])
                    self.send(dict(id=item['id'], result=dict(outcome=dict(outcome='accepted'))))
                elif item['method'] == 'session/update': self.update(item['params'])
                elif 'id' in item: raise Blocked(json.dumps(item['params'], ensure_ascii=False))
                else: self.mission.record('subagent' if item['method'] == 'cursor/task' else item['method'], item['params'])
                continue
            if item['id'] == identity:
                if method == 'session/prompt': self.mission.record('prompt-result', dict(time=utc(), session_id=params['sessionId'], request_id=identity, seconds=time.monotonic() - started, response=item))
                if 'error' in item: raise RuntimeError(json.dumps(item['error'], ensure_ascii=False))
                return item['result']
            raise ValueError('unexpected ACP response: ' + json.dumps(item, ensure_ascii=False))
    def update(self, params):
        update, session = params['update'], params['sessionId']
        if update['sessionUpdate'] == 'usage_update':
            self.mission.record('usage', dict(time=utc(), session_id=session, request_id=self.sequence if session == self.active else None, update=update)); return
        if self.loading is not None: return
        kind, role = update['sessionUpdate'], self.roles[session]
        if kind == 'subagent_spawned':
            self.roles[update['subagentSessionId']] = 'delegate'; self.mission.record('subagent', update)
        if kind == 'agent_message_chunk':
            if role == 'delegate': self.mission.record(role, update['content']['text'])
            else:
                self.text[session] = self.text.get(session, '') + update['content']['text']
        elif kind in ('tool_call', 'tool_call_update'):
            identity = update['toolCallId']
            if kind == 'tool_call':
                if self.text.get(session): self.mission.record(role, self.text[session]); self.text[session] = ''
                self.tools[identity] = dict(role=role, session_id=session, **update)
            else: self.tools[identity].update(update)
            event = self.tools[identity]
            if event['status'] in ('completed', 'failed'):
                event['time'] = utc()
                del self.tools[identity]; self.mission.state['observations'].append(event)
                if event['status'] == 'failed' or event['kind'] == 'execute' and event['rawOutput']['exitCode'] != 0: self.mission.state['failures'].append(event)
                self.mission.record('tool', event); self.mission.save('tool', self.mission.brief(event))
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
        self.rpc('session/set_mode', dict(sessionId=identity, modeId='agent'))
        return identity
    def prompt(self, role, update, images=()):
        self.active = session = self.mission.state['sessions'][role]
        self.text[session] = ''
        text = json.dumps(update, ensure_ascii=False)
        if session not in self.ready:
            text = (OBSERVER if role == 'observer' else ACTOR) + '\n' + CONTRACT + '\n' + json.dumps(self.mission.initial(), ensure_ascii=False) + '\n' + text
            self.ready.add(session)
        write(self.mission.work / 'prompt.txt', text)
        content = [dict(type='text', text=text)]
        for path in images:
            content.extend([dict(type='text', text=str(path)), dict(type='image', mimeType='image/png', data=base64.b64encode(Path(path).read_bytes()).decode())])
        result = self.rpc('session/prompt', dict(sessionId=session, prompt=content))
        self.active = None
        if result['stopReason'] != 'end_turn': raise RuntimeError(f"Cursor stopped: {result['stopReason']}")
        self.mission.record(role, self.text[session])
        return json.loads(self.text[session])
    def cancel(self):
        if self.active:
            self.send(dict(method='session/cancel', params=dict(sessionId=self.active)))
            self.mission.save('cancel', dict(session_id=self.active, time=utc()))
            self.active = None
OBSERVER = 'You are Trident\'s observer. The human goal is the assignment. Assess actual content, quality and tool results; a file, successful command or source edit alone does not establish success. First return only {"timing":{"deadline":null,"source":null},"criteria":["acceptance condition"]}, interpreting timing from the goal without tools. Measure relative limits from started_at. Deadline is a timezone-bearing ISO timestamp or null; source is the exact human timing quote or null. Then use tools as useful and return {"decision":"act","action":{"instruction":"useful next work","desktop":false},"findings":{"c1":{"passed":false,"evidence":[],"reason":"actual finding"}},"handover":null,"message":"assessment"}. Findings update only changed criteria. Inspect real evidence before passing a criterion; complete requires all criteria supported. Decisions are act, complete, blocked, or handover. Handover is {"continue":true,"session_id":null,"instructions":"progress and next work"}; use a recorded actor ID to resume or null for a fresh context. Timing and continuation follow the goal. Desktop input is coordinated through actor actions; your own tools can research, inspect and improve files.'
ACTOR = 'You are Trident\'s actor. Perform useful multistep work toward the current action, observing results between dependent operations. Inspect fresh images and actual interface bounds for desktop input. Choose useful work size and change method when observed results warrant it. Return {"summary":"what actually happened, including failures and unfinished work","artifacts":["existing output path"]}. Report real worker/process IDs and paths. Collect required worker and child results before goal completion; closing this run ends its process job.'
CONTRACT = "Return one JSON object without Markdown. Both contexts and native workers have the installed task tools and the owner's standing authorization. Use genuine native workers for independent work and pass their relevant goal, timing and desktop contracts. Full copies use the supplied Python and body with --sentence, --model and a separate --work; inherit the selected model. Each copy owns its state and sessions and belongs to this run's process job. File work and waiting use desktop=false. Desktop=true holds the shared desktop mutex for the action, supplies a fresh capture after acquisition and captures the result before release. Workers sharing that action coordinate their input; separate copies acquire the mutex themselves. Await children without holding the desktop. Observe interrupted work before continuing because it may have partial effects. State, story, ledger, goal, prompt and stop are controller memory; retrieve deeper history through native search/Read. Tools, short prompts and source can be improved in body; record the limitation, change and actual evidence. New runs load revised controller code and prompts. Helper calls import body: windows(title_suffix)->HWND list; focus(hwnd) activates; at(hwnd,x,y)->physical screen coordinates from client pixels; pointer(x,y,flags) uses physical screen pixels, 0 move/2 left-down/4 left-up; chord(*pairs) takes (virtual_key,0 down/2 up); stroke(hwnd,client_points,pause=0.008)->screen points; screen_area(desktop,x,y,width,height,shown_width=None,shown_height=None)->physical rectangle. capture(path,end=None)->whole-desktop PNG path, end is monotonic. Successful focus/pointer/chord/send return None; checked API failures raise. Desk supports open('title_suffix|command'), paste(text) replacing the focused control, watch('title_suffix vk'), draw('circle' or 'rectangle') using the current tool. Image metadata supplies original dimensions and virtual-screen origin. SendInput insertion does not establish application effect."
class Mission:
    def __init__(self, work, goal, model, run, started_at):
        self.work, self.end = Path(work).resolve(), None
        self.work.mkdir(parents=True, exist_ok=True)
        self.run = run or time.strftime('%Y%m%d_%H%M%S') + '_' + str(os.getpid())
        self.path, self.evidence = self.work / f'run_{self.run}.md', self.work / 'evidence' / self.run
        self.evidence.mkdir(parents=True)
        previous = json.loads((self.work / 'state.json').read_text(encoding='utf-8')) if (self.work / 'state.json').exists() else {}
        self.previous = None
        if previous:
            archive = self.evidence / 'previous.json'
            write(archive, json.dumps(previous, ensure_ascii=False, indent=2))
            self.previous = dict(path=str(archive), facts={k: v for k, v in previous.items() if k in ('goal', 'run', 'status', 'checks', 'handover', 'result')},
                interrupted={k: previous[k] for k in ('action', 'sessions', 'agents', 'pending_tools') if k in previous},
                failures=[{k: v for k, v in f.items() if k in ('time', 'error', 'toolCallId', 'status')} for f in previous.get('failures', [])])
        self.state = dict(goal=goal, model=model, run=self.run, started_at=started_at, status='observing', sessions={}, agents=[],
            timing=None, criteria={}, checks={}, action=None, observations=[], failures=[], artifacts={}, pending_tools={}, handover=None, result=None, desktop=None)
        (self.work / 'stop').unlink(missing_ok=True)
        write(self.work / 'goal.txt', goal + '\n'); self.save('goal', goal)
    def record(self, kind, value):
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        record(self.path, f"## {time.strftime('%H:%M:%S')} {kind}\n\n{text}\n\n")
        if kind == 'subagent': self.state['agents'].append(value); self.save('delegation', value)
    def save(self, kind, value):
        write(self.work / 'state.json', json.dumps(self.state, ensure_ascii=False, indent=2))
        write(self.work / 'story.txt', json.dumps({k: self.state[k] for k in ('goal', 'run', 'status', 'checks', 'action', 'handover', 'result', 'pending_tools')}, ensure_ascii=False, indent=2))
        body = json.dumps(dict(run=self.run, status=self.state['status'], kind=kind, data=value), ensure_ascii=False)
        with (self.work / 'ledger.md').open('a', encoding='utf-8') as ledger: ledger.write(f"## {'run' if kind == 'final' else 'event'} {self.run} {utc()}\n{body}\n\n")
    def brief(self, event):
        summary = {k: event[k] for k in ('time', 'role', 'session_id', 'toolCallId', 'kind', 'status', 'locations') if k in event}
        if event['kind'] == 'execute': summary['result'] = event['rawOutput']
        return summary
    def artifact(self, name):
        path = (self.work / name).resolve()
        info = path.stat()
        self.state['artifacts'][str(path)] = dict(bytes=info.st_size, modified_ns=info.st_mtime_ns, observed_at=utc())
        return str(path)
    def observe(self, name):
        image = capture(self.evidence / f'{name}.png', self.end)
        with image.open('rb') as handle: header = handle.read(24)
        self.state['desktop'] = dict(path=self.artifact(image), observed_at=utc(), screen=dict(zip(('x', 'y', 'width', 'height'),
            (U.GetSystemMetrics(n) for n in (76, 77, 78, 79)))), image_width=int.from_bytes(header[16:20], 'big'), image_height=int.from_bytes(header[20:24], 'big'))
        self.save('observation', self.state['desktop'])
        return image
    def initialize(self, decision):
        timing = decision['timing']
        if timing['deadline'] is not None:
            if not timing['source'] or timing['source'] not in self.state['goal']: raise ValueError('deadline has no goal evidence')
            deadline = datetime.fromisoformat(timing['deadline'])
            if deadline.tzinfo is None: raise ValueError('deadline requires a timezone')
            self.end = time.monotonic() + deadline.timestamp() - time.time()
        if not decision['criteria']: raise ValueError('goal requires acceptance criteria')
        self.state['timing'] = dict(**timing, interpreted_at=utc(), bootstrap_limitation='Free-form timing cannot be enforced before its initial interpretation returns')
        self.state['criteria'] = {f'c{i}': text for i, text in enumerate(decision['criteria'], 1)}
        self.save('acceptance', decision); remaining(self.end)
    def verify(self):
        for identity in self.state['criteria']:
            finding = self.state['checks'][identity]
            if finding['passed'] is not True or not finding['reason'] or not finding['evidence']: raise ValueError('unverified criterion: ' + identity)
            for path in finding['evidence']: self.artifact(path)
        if self.state['pending_tools']: raise ValueError('completion has unfinished tools')
        remaining(self.end)
    def initial(self):
        return dict(**{k: self.state[k] for k in ('goal', 'model', 'started_at', 'timing', 'criteria', 'sessions', 'checks', 'result', 'handover')},
            now=utc(), python=sys.executable, body=str(ROOT / 'main.py'), work=str(self.work), previous=self.previous,
            memory={name: str(self.work / name) for name in ('state.json', 'story.txt', 'ledger.md')})

def run_goal(work, goal, model='', run=''):
    started_at, work = utc(), Path(work).resolve()
    with lease('work-' + hashlib.sha256(os.path.normcase(str(work)).encode('utf-8')).hexdigest()):
        mission = Mission(work, goal, choose_brain(goal, model), run, started_at)
        state = mission.state
        try:
            with ExitStack() as resources:
                client = ACP(mission, resources)
                client.rpc('initialize', dict(protocolVersion=1, clientCapabilities=dict(fs=dict(readTextFile=False, writeTextFile=False), terminal=False,
                    _meta=dict(parameterizedModelPicker=True, subagents=True)), clientInfo=dict(name='trident', version='1')))
                for role in ('actor', 'observer'): state['sessions'][role] = client.session(role)
                mission.save('sessions', state['sessions'])
                mission.initialize(client.prompt('observer', dict(initialize=True)))
                update, image, step = dict(criteria=state['criteria'], timing=state['timing']), None, 0
                while True:
                    remaining(mission.end)
                    decision = client.prompt('observer', dict(now=utc(), **update), [image] if image else [])
                    state['checks'].update(decision['findings'])
                    state['action'], state['handover'] = decision['action'], decision['handover']
                    mission.save('observer', decision)
                    action = decision['decision']
                    if action == 'complete': mission.verify(); state['status'] = 'completed'; break
                    if action == 'blocked': state['status'] = 'blocked'; break
                    if action == 'handover':
                        handover = state['handover']
                        if not handover['instructions']: raise ValueError('handover requires instructions')
                        if not handover['continue']: state['status'] = 'handover'; break
                        identity = handover['session_id']
                        if identity and client.roles[identity] != 'actor': raise ValueError('handover requires a recorded actor session')
                        state['sessions']['actor'] = client.session('actor', identity)
                        state['agents'].append(dict(role='actor', session_id=state['sessions']['actor']))
                    elif action != 'act': raise ValueError('unknown observer decision: ' + action)
                    step += 1
                    state['status'] = 'acting'; mission.save('in-flight', state['action'])
                    offset, image = len(state['observations']), None
                    with ExitStack() as desktop:
                        try:
                            if state['action']['desktop']:
                                desktop.enter_context(lease('desktop', mission.end, wait=True))
                                image = mission.observe(f'{step}-before')
                            result = client.prompt('actor', dict(action=state['action'], findings=decision['findings'], message=decision['message'],
                                handover=state['handover'], desktop=state['desktop'] if image else None), [image] if image else [])
                            state['result'] = result
                            if state['action']['desktop']: image = mission.observe(str(step))
                        except BaseException:
                            resources.close(); raise
                    artifacts = [mission.artifact(path) for path in result['artifacts']]
                    state['status'] = 'observing'; mission.save('actor', result)
                    update = dict(result=result, artifacts=artifacts, desktop=state['desktop'] if image else None,
                        tools=[mission.brief(event) for event in state['observations'][offset:]])
            remaining(mission.end)
        except (Deadline, Blocked) as error:
            state['status'] = 'deadline' if isinstance(error, Deadline) else 'blocked'
            state['failures'].append(dict(time=utc(), error=str(error)))
        except BaseException as error:
            state['status'] = 'failed'; state['failures'].append(dict(time=utc(), error=f'{type(error).__name__}: {error}'))
            raise
        finally:
            final = {key: state[key] for key in ('goal', 'run', 'status', 'checks', 'failures', 'action', 'handover', 'pending_tools')}
            final['elapsed_seconds'] = time.time() - datetime.fromisoformat(started_at).timestamp()
            mission.save('final', final)
            if state['status'] == 'completed': write(mission.work / 'stop', '')
            mission.record('final', final)
        return state['status']


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == 'serve': return Journal(ROOT).serve()
    if argv and not argv[0].startswith('-'): return run_beats(Path(argv[0]).read_text(encoding='utf-8-sig'))
    parser = argparse.ArgumentParser(prog='trident')
    goal = parser.add_mutually_exclusive_group(required=True)
    goal.add_argument('--sentence'); goal.add_argument('--mind-file')
    for name in ('work', 'model', 'run'): parser.add_argument('--' + name, default='')
    args = parser.parse_args(argv)
    sentence = args.sentence if args.sentence is not None else Path(args.mind_file).read_text(encoding='utf-8-sig')
    if not sentence.strip(): raise ValueError('the human goal is empty')
    status = run_goal(args.work or ROOT, sentence.strip(), args.model, args.run)
    return dict(completed=0, deadline=2, handover=2, blocked=3)[status]
if __name__ == '__main__':
    try: sys.exit(main())
    except Blocked as error: sys.stderr.write(str(error) + '\n'); sys.exit(3)
    except KeyboardInterrupt: sys.exit(1)
