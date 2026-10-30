"""Trident: desktop beats, shared journal, and timed Cursor runs in one file."""
import argparse, ctypes, json, math, os, queue, re, socket, subprocess, sys, threading, time, urllib.request, uuid
from collections import Counter
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
        title, vk = arg.rsplit(" ", 1)
        self.paste("click the notepad window and hit Enter\n\n" + WATCH)
        proc = subprocess.Popen([sys.executable, "-c", WATCH, title, vk], creationflags=0x08000000)
        proc.wait(); return f"awake {title.strip()}"
    def draw(self, arg):
        focus(self.hwnd); rect = wintypes.RECT(); U.GetClientRect(self.hwnd, ctypes.byref(rect))
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
ROTATE_AT = 42
STOP_GRACE = 60
HEAD = re.compile(r'(?m)^## (\d\d:\d\d:\d\d) (\S+)')
WAKE = re.compile(r'(?m)^## wake ')
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
def read(path):
    path = Path(path)
    return path.read_text(encoding='utf-8-sig') if path.is_file() else ''
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
def measured(path):
    heads = list(HEAD.finditer(read(path)))
    counts = dict(Counter(head.group(2) for head in heads))
    return {'run': Path(path).stem.removeprefix('run_'), 'source': str(path),
            'first_event': heads[0].group(1) if heads else None, 'last_event': heads[-1].group(1) if heads else None,
            'counts': counts, 'recorded_errors': counts.get('err', 0)}
def learned_line(rows, ledger):
    errors = sum(row['recorded_errors'] for row in rows)
    calls = sum(row['counts'].get('tool', 0) for row in rows)
    names = ', '.join(row['run'] for row in rows)
    advice = 'Compare the newest ledger line with the run markdown.' if ledger.strip() else 'There is no ledger lesson yet.'
    return f'{len(rows)} earlier wakes ({names}), {errors} recorded errors, {calls} tool calls. {advice}'
def newest(ledger):
    parts = WAKE.split(ledger or '')
    return '## wake ' + parts[-1].strip() if len(parts) > 1 else 'There is no ledger lesson yet.'
def choose_brain(sentence, model):
    if model:
        if model not in BRAINS: raise SystemExit('unknown mind model: ' + model)
        return model
    return next((slug for slug in sorted(BRAINS, key=len, reverse=True) if slug in sentence.lower()), BRAIN)
def closing_clock(minutes, end):
    begin = end - max(0.05 * float(minutes), 2) * 60
    return (f'The wake has {float(minutes):g} minutes and ends at {time.strftime("%H:%M:%S", time.localtime(end))}. '
            f'From {time.strftime("%H:%M:%S", time.localtime(begin))} on, stop new work, write story.txt and lesson.txt, and write the stop file.')
def render(work, run, run_id, sentence, minutes, end, model):
    ledger = read(work / 'ledger.md')
    prior = [row for path in sorted(work.glob('run_*.md')) if (row := measured(path))['run'] != run_id]
    parts = [
        'You are the Trident organism. main.py is your body. story.txt is the plan: a short ordered list of subtasks. campaign.txt is the task list. ledger.md is memory. The run markdown is the record. You act with Cursor\'s own tools. The sentence at the bottom is the only assignment.',
        closing_clock(minutes, end),
        'Write story.txt, the whole plan, whenever it changes. Write lesson.txt as this wake\'s one lesson, 1-7200 characters. Write an empty file named stop when the assignment is finished. In the closing window, write those three and do not start new work. Your last message is not completion. The stop file is.',
        'Programs you start from this chat die when the chat ends. Put anything the next chat must reopen into story.txt. There is no vision server and there are no Trident tool flags.',
        'STORY', read(work / 'story.txt') or '(empty)',
        'CAMPAIGN', read(work / 'campaign.txt') or '(empty)',
        'LEDGER', newest(ledger),
        'MEASURED', json.dumps({'current': measured(run), 'historical': prior}, ensure_ascii=False),
        'LEARNED', learned_line(prior, ledger),
        'BRAIN', model, 'RUN', str(run), 'SENTENCE', sentence.strip(),
    ]
    return '\n\n'.join(parts)
def runtime():
    versions = Path(os.environ['LOCALAPPDATA']) / 'cursor-agent' / 'versions'
    folders = [path for path in versions.iterdir() if path.name[:1].isdigit() and (path / 'index.js').exists()]
    if not folders: raise SystemExit('the agent runtime is not installed')
    return max(folders)
def open_job():
    job = kernel32.CreateJobObjectW(None, None)
    wincheck(job)
    limits = JobLimits()
    limits.BasicLimitInformation.LimitFlags = 0x2000
    wincheck(kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
    return job
def close_job(job):
    if job:
        wincheck(kernel32.CloseHandle(job))
def launch(work, model, prompt_path, job):
    folder, vendor = runtime(), work / 'agent'
    profile = vendor / 'config'
    profile.mkdir(parents=True, exist_ok=True)
    (vendor / 'data').mkdir(parents=True, exist_ok=True)
    (profile / 'cli-config.json').write_text(json.dumps({
        'version': 1,
        'permissions': {'allow': ['Read(**)', 'Write(**)', 'Shell(*)', 'WebFetch(*)', 'Mcp(*:*)'], 'deny': []},
        'sandbox': {'mode': 'disabled'}, 'approvalMode': 'unrestricted', 'autoAcceptWebSearch': True,
    }), encoding='utf-8')
    env = os.environ.copy()
    env.update(CURSOR_CONFIG_DIR=str(profile), CURSOR_DATA_DIR=str(vendor / 'data'),
               PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    env.pop('OPENROUTER_API_KEY', None)
    argv = [
        str(folder / 'node.exe'), '-e',
        "process.argv.push(require('node:fs').readFileSync(process.argv.pop(),'utf8'));require(process.argv[1]);",
        str(folder / 'index.js'), '--trust', '--force', '-p', '--model', model,
        '--workspace', str(work), '--output-format', 'stream-json', str(prompt_path),
    ]
    proc = subprocess.Popen(
        argv, cwd=str(work), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW | CREATE_SUSPENDED,
    )
    if not kernel32.AssignProcessToJobObject(job, proc._handle):
        proc.kill()
        raise ctypes.WinError(ctypes.get_last_error())
    status = ntdll.NtResumeProcess(proc._handle)
    if status != 0:
        proc.kill()
        raise OSError(f'NtResumeProcess {status:#x}')
    return proc
def text_of(message):
    content = message.get('content') if isinstance(message, dict) else message
    if isinstance(content, list): return ''.join(part.get('text', '') if isinstance(part, dict) else str(part) for part in content)
    return '' if content is None else str(content)
def consume(proc, work, run, end):
    calls = running = 0
    thought, reason, stop_at = '', None, None
    inbox = queue.Queue()
    def pump():
        for raw in proc.stdout:
            inbox.put(raw)
        inbox.put(None)
    threading.Thread(target=pump, daemon=True).start()
    def finish(why):
        nonlocal thought
        if thought:
            heading(run, 'think', thought)
            thought = ''
        return why
    while True:
        now = time.time()
        if now >= end: return finish('deadline')
        if (work / 'stop').exists():
            stop_at = stop_at or now + STOP_GRACE
            if now >= stop_at: return finish('stopped')
        try:
            raw = inbox.get(timeout=0.2)
        except queue.Empty:
            continue
        if raw is None:
            code = proc.wait(timeout=10)
            if (work / 'stop').exists(): return finish('stopped')
            if code != 0: return finish('failed_agent')
            return finish(reason or 'completed')
        line = raw.decode('utf-8', 'replace').rstrip('\r\n')
        if not line.startswith('{'):
            if line:
                heading(run, 'err', line)
            continue
        item = json.loads(line)
        if not isinstance(item, dict): continue
        kind, sub = item.get('type'), item.get('subtype')
        if kind == 'thinking' and sub == 'delta':
            thought += item.get('text') or ''
            continue
        finish(None)
        if kind == 'assistant' and text_of(item.get('message')):
            heading(run, 'say', text_of(item.get('message')))
        elif kind == 'tool_call' and sub == 'started':
            running += 1
        elif kind == 'tool_call' and sub == 'completed':
            running, calls = max(0, running - 1), calls + 1
            heading(run, 'tool', json.dumps(item.get('tool_call') or {}, ensure_ascii=False))
            if calls >= ROTATE_AT and running == 0 and not (work / 'stop').exists() and time.time() < end: return finish('rotate')
        elif kind == 'system' and sub == 'init':
            heading(run, 'start', str(item.get('model') or ''))
        elif kind == 'result':
            failed = item.get('is_error') or item.get('error') or str(sub or '').startswith('error')
            reason = 'failed_agent' if failed else 'completed'
            if failed: return finish(reason)
def fold_lesson(work, run_id, state, start, minutes):
    lesson = WAKE.sub('', read(work / 'lesson.txt')).strip() or 'No lesson was written.'
    if len(lesson) > 7200: raise SystemExit('a lesson has 1-7200 characters')
    parts = WAKE.split(read(work / 'ledger.md'))[1:]
    kept = [part for part in parts if part.split(None, 1)[0] != run_id]
    stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start))
    kept.append(f'{run_id} {stamp} used {(time.time() - start) / 60:.1f} of {float(minutes):g} min, {state}\n{lesson}\n')
    write(work / 'ledger.md', ''.join('## wake ' + part for part in kept))
def drive(work, sentence, minutes, model, run_id):
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    for name in ('stop', 'lesson.txt'):
        path = work / name
        if path.exists():
            path.unlink()
    start = time.time()
    end = start + float(minutes) * 60
    run_id = run_id or time.strftime('%Y%m%d_%H%M%S')
    run = work / f'run_{run_id}.md'
    write(work / 'goal.txt', sentence.strip() + '\n')
    model = choose_brain(sentence, model)
    outcome, chat = 'failed', 0
    heading(run, 'wake', f'minutes={float(minutes):g}')
    while time.time() < end and not (work / 'stop').exists():
        chat += 1
        write(work / 'prompt.txt', render(work, run, run_id, sentence, minutes, end, model))
        heading(run, 'chat', f'{model} {chat}')
        job, proc = open_job(), None
        try:
            proc = launch(work, model, work / 'prompt.txt', job)
            outcome = consume(proc, work, run, end)
        finally:
            close_job(job)
            if proc is not None and proc.poll() is None:
                proc.wait(timeout=15)
        heading(run, 'agent-exit', outcome)
        if outcome != 'rotate': break
    else:
        outcome = 'deadline' if time.time() >= end else 'stopped'
    fold_lesson(work, run_id, outcome, start, minutes)
    heading(run, 'mind', json.dumps({'state': outcome, 'minutes': round((time.time() - start) / 60, 1)}))
    return outcome
def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == 'serve': return Journal(ROOT).serve()
    if argv and not argv[0].startswith('-'): return wake(Path(argv[0]).read_text(encoding='utf-8-sig'))
    parser = argparse.ArgumentParser(prog='trident', add_help=False)
    parser.add_argument('--minutes', required=True)
    for name in ('work', 'sentence', 'mind-file', 'model', 'run'): parser.add_argument('--' + name, default='')
    args = parser.parse_args(argv)
    minutes = float(args.minutes)
    if minutes <= 0: raise SystemExit('minutes must be positive')
    if bool(args.sentence.strip()) == bool(args.mind_file): raise SystemExit('pass exactly one of --sentence or --mind-file')
    sentence = args.sentence.strip() if args.sentence.strip() else read(args.mind_file).strip()
    if not sentence: raise SystemExit('the sentence is empty')
    outcome = drive(Path(args.work).resolve() if args.work else ROOT, sentence, minutes, args.model, args.run)
    if outcome not in ('completed', 'stopped', 'deadline'): raise SystemExit(outcome)
if __name__ == '__main__':
    main()
