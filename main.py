import argparse, base64, codecs, ctypes, hashlib, json, math, os, queue, shutil, subprocess, sys, threading, time, urllib.error, urllib.request
from contextlib import ExitStack, contextmanager
from ctypes import wintypes
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
BODY = Path(__file__).resolve()
ROOT = BODY.parent
HUB_PORT = 8765
JEV, DECISIONS = 'typesafe/jev-1.14', 'https://openrouter.ai/api/alpha/decisions'
GROK = 'grok-4.7'
MINDS = tuple(f'{GROK}-{effort}{speed}' for effort in ('low', 'medium', 'high', 'xhigh') for speed in ('', '-fast'))
MIND = f'{GROK}-medium-fast'
NO_WINDOW, SUSPENDED = 0x08000000, 0x00000004
PALETTE = ('#7CFFB2', '#7CC4FF', '#FFD27C', '#FF8FB8', '#C9B6FF', '#FFB07C')
INITIATIVE = ('No concrete goal was given this time. Read your story.md and recent journals, look at this computer, and take the current date and anything time-sensitive from the internet. '
              'Decide what is genuinely useful for the owner now and do it when it is clearly useful and reversible, otherwise do nothing and record one honest paragraph on why. You decide when the work is done.')
user32, kernel32, ntdll = (ctypes.WinDLL(name, use_last_error=True) for name in ('user32', 'kernel32', 'ntdll'))
ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
def signature(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype, function.argtypes = result, arguments
    return function
SendInput = signature(user32, 'SendInput', wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_int)
EnumWindows = signature(user32, 'EnumWindows', wintypes.BOOL, ENUM_WINDOWS_PROC, wintypes.LPARAM)
GetWindowTextW = signature(user32, 'GetWindowTextW', ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
IsWindowVisible = signature(user32, 'IsWindowVisible', wintypes.BOOL, wintypes.HWND)
IsIconic = signature(user32, 'IsIconic', wintypes.BOOL, wintypes.HWND)
ShowWindow = signature(user32, 'ShowWindow', wintypes.BOOL, wintypes.HWND, ctypes.c_int)
SetForegroundWindow = signature(user32, 'SetForegroundWindow', wintypes.BOOL, wintypes.HWND)
GetForegroundWindow = signature(user32, 'GetForegroundWindow', wintypes.HWND)
GetClientRect = signature(user32, 'GetClientRect', wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
ClientToScreen = signature(user32, 'ClientToScreen', wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.POINT))
GetSystemMetrics = signature(user32, 'GetSystemMetrics', ctypes.c_int, ctypes.c_int)
CreateJobObjectW = signature(kernel32, 'CreateJobObjectW', wintypes.HANDLE, wintypes.LPVOID, wintypes.LPCWSTR)
SetInformationJobObject = signature(kernel32, 'SetInformationJobObject', wintypes.BOOL, wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
AssignProcessToJobObject = signature(kernel32, 'AssignProcessToJobObject', wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE)
CloseHandle = signature(kernel32, 'CloseHandle', wintypes.BOOL, wintypes.HANDLE)
CreateMutexW = signature(kernel32, 'CreateMutexW', wintypes.HANDLE, wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
WaitForSingleObject = signature(kernel32, 'WaitForSingleObject', wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD)
ReleaseMutex = signature(kernel32, 'ReleaseMutex', wintypes.BOOL, wintypes.HANDLE)
NtResumeProcess = signature(ntdll, 'NtResumeProcess', ctypes.c_long, wintypes.HANDLE)
user32.SetProcessDPIAware()
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('mouseData', wintypes.DWORD), ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD), ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]
class INPUT(ctypes.Structure):
    class _Union(ctypes.Union):
        _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT)]
    _anonymous_ = ('union',)
    _fields_ = [('type', wintypes.DWORD), ('union', _Union)]
class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64), ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t), ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', wintypes.DWORD), ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]
class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION), ('IoInfo', ctypes.c_uint64 * 6), ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]
class Deadline(Exception): pass
class Blocked(Exception): pass
def check(value):
    if not value: raise ctypes.WinError(ctypes.get_last_error())
    return value
def utc():
    return datetime.now(timezone.utc).isoformat()
def remaining(end):
    if end is None: return None
    left = end - time.monotonic()
    if left <= 0: raise Deadline('goal deadline reached')
    return left
def virtual_screen():
    return tuple(GetSystemMetrics(metric) for metric in (76, 77, 78, 79))
def send(*inputs):
    batch = (INPUT * len(inputs))(*inputs)
    if SendInput(len(inputs), ctypes.byref(batch), ctypes.sizeof(INPUT)) != len(inputs): raise ctypes.WinError(ctypes.get_last_error())
def pointer(x, y, flags):
    left, top, width, height = virtual_screen()
    send(INPUT(type=0, mi=MOUSEINPUT(round((x - left) * 65535 / (width - 1)), round((y - top) * 65535 / (height - 1)), 0, flags | 0xC001, 0, 0)))
def click(x, y, pause=0.04):
    point = (int(round(x)), int(round(y)))
    pointer(*point, 0)
    time.sleep(pause)
    try:
        pointer(*point, 2)
        time.sleep(pause)
    finally:
        pointer(*point, 4)
    return point
def drag(x1, y1, x2, y2, steps=12, pause=0.018):
    start, end = (int(round(x1)), int(round(y1))), (int(round(x2)), int(round(y2)))
    pointer(*start, 0)
    time.sleep(pause)
    pointer(*start, 2)
    try:
        for index in range(1, int(steps) + 1):
            pointer(int(round(x1 + (x2 - x1) * index / steps)), int(round(y1 + (y2 - y1) * index / steps)), 0)
            time.sleep(pause)
    finally:
        pointer(*end, 4)
    return end
def chord(*keys):
    send(*(INPUT(type=1, ki=KEYBDINPUT(code, 0, transition, 0, 0)) for code, transition in keys))
def focus(hwnd):
    if IsIconic(hwnd): ShowWindow(hwnd, 9)
    if GetForegroundWindow() != hwnd:
        chord((0x12, 0)); raised = SetForegroundWindow(hwnd); chord((0x12, 2))
        if not raised: raise OSError(f'SetForegroundWindow refused {hwnd}')
    time.sleep(0.05)
    if GetForegroundWindow() != hwnd: raise OSError(f'window {hwnd} is not in the foreground')
def find_windows(title_suffix):
    found, suffix = [], title_suffix.lower()
    @ENUM_WINDOWS_PROC
    def visit(hwnd, _):
        title = ctypes.create_unicode_buffer(512)
        GetWindowTextW(hwnd, title, 512)
        if IsWindowVisible(hwnd) and title.value.lower().endswith(suffix): found.append(hwnd)
        return True
    check(EnumWindows(visit, 0))
    return found
def client_size(hwnd):
    rect = wintypes.RECT()
    check(GetClientRect(hwnd, ctypes.byref(rect)))
    return rect.right, rect.bottom
def to_screen(hwnd, x, y):
    point = wintypes.POINT(int(x), int(y))
    check(ClientToScreen(hwnd, ctypes.byref(point)))
    return point.x, point.y
def stroke(hwnd, points, pause=0.008, focus_window=True):
    if focus_window: focus(hwnd)
    path = [to_screen(hwnd, x, y) for x, y in points]
    pointer(*path[0], 0); pointer(*path[0], 2)
    try:
        for x, y in path[1:-1]: pointer(x, y, 0); time.sleep(pause)
        pointer(*path[-1], 0)
    finally:
        pointer(*path[-1], 4)
    return path
def powershell(script, end=None):
    try: return subprocess.run(['powershell.exe', '-NoProfile', '-Command', script], check=True, timeout=remaining(end), creationflags=NO_WINDOW, capture_output=True, text=True).stdout
    except subprocess.TimeoutExpired: raise Deadline('goal deadline reached') from None
def capture(path, end=None, region=None):
    left, top, width, height = region or virtual_screen()
    target = str(Path(path)).replace("'", "''")
    powershell("$ErrorActionPreference='Stop';Add-Type -AssemblyName System.Drawing;"
               "Add-Type 'using System.Runtime.InteropServices;public class Dpi{[DllImport(\"user32.dll\")]public static extern bool SetProcessDPIAware();}';[Dpi]::SetProcessDPIAware()|Out-Null;"
               f"$b=New-Object System.Drawing.Bitmap {width},{height};$g=[System.Drawing.Graphics]::FromImage($b);$g.CopyFromScreen({left},{top},0,0,$b.Size);"
               f"$b.Save('{target}',[System.Drawing.Imaging.ImageFormat]::Png);$g.Dispose();$b.Dispose()", end)
    return Path(path)
def paste(hwnd, text):
    encoded = base64.b64encode(text.encode('utf-8')).decode()
    powershell(f"Set-Clipboard -Value ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}')))")
    focus(hwnd)
    chord((0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2))
    time.sleep(0.1)
def go(hwnd, url):
    with lease('desktop', wait=True):
        focus(hwnd)
        chord((0x11, 0), (0x4C, 0), (0x4C, 2), (0x11, 2))
        time.sleep(0.15)
        paste(hwnd, url)
        chord((0x0D, 0), (0x0D, 2))
    return url
@contextmanager
def lease(name, end=None, wait=False):
    handle = check(CreateMutexW(None, False, 'Local\\Trident-' + name))
    try:
        while True:
            slice_ms = (min(200, math.ceil(remaining(end) * 1000)) if end is not None else 200) if wait else 0
            status = WaitForSingleObject(handle, slice_ms)
            if status in (0, 0x80): break
            if status != 0x102: raise ctypes.WinError(ctypes.get_last_error())
            if not wait: raise Blocked(f'{name} is held by another Trident')
        try: yield
        finally: check(ReleaseMutex(handle))
    finally: check(CloseHandle(handle))
def job_object(resources):
    job = check(CreateJobObjectW(None, None))
    resources.callback(lambda: check(CloseHandle(job)))
    limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    limits.BasicLimitInformation.LimitFlags = 0x2000
    check(SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
    return job
def post(url, payload, timeout, headers):
    request = urllib.request.Request(url, json.dumps(payload).encode('utf-8'), {**headers, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response: return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'HTTP Error {error.code}: {error.read().decode()}') from error
def image_part(path):
    return dict(type='image_url', image_url=dict(url='data:image/png;base64,' + base64.b64encode(Path(path).read_bytes()).decode()))
def decide(state, questions):
    for question in questions.values():
        if question['type'] == 'choice': question['criteria']['change approach'] = 'The current route is not producing new evidence; a different tool, source or method is needed.'
    key = (ROOT.parent / '.env').read_text(encoding='utf-8').strip()
    return post(DECISIONS, dict(model=JEV, state=state, questions=questions), 60, {'Authorization': 'Bearer ' + key})
def locate(path, description, left, top, width, height):
    answers = decide([f'A {width} by {height} pixel screen region:', image_part(capture(path, region=(left, top, width, height)))],
                     dict(target=dict(type='box', instructions=f'{description}, in the region'), present=dict(type='noul', instructions=f'The region shows {description}.')))['answers']
    box = answers['target']['box']
    return dict(x=round(left + (box['left'] + box['right']) / 2 * width), y=round(top + (box['top'] + box['bottom']) / 2 * height), present=answers['present']['noul'], confidence=answers['target']['confidence'])
def journal(path, text):
    with open(path, 'a', encoding='utf-8') as handle: handle.write(text)
    sys.stdout.write(text); sys.stdout.flush()
def choose_mind(goal, model):
    if model:
        if model not in MINDS: raise SystemExit('unknown mind: ' + model)
        return model
    return next((mind for mind in sorted(MINDS, key=len, reverse=True) if mind in goal.lower()), MIND)
OBSERVER = ("Return one JSON object without Markdown. You are Trident's observer, the judgment of a living organism that works on this computer. The human sentence is the only assignment. Today's date and current facts come from the system clock and the internet, never from memory. "
            "On the first turn return only {\"timing\":{\"deadline\":null,\"source\":null},\"criteria\":[\"...\"]}: a deadline exists only when the human wrote one, as an ISO timestamp with timezone plus the verbatim quote in source; criteria describe the result the human will hold, never procedure, tools, reading or timing. If a criterion differs from the human sentence, or from criteria already written in story.md, say that difference in the message. "
            "On every later turn you are shown the latest screen capture and the actor's reply and tools; inspect real evidence yourself and return {\"decision\":\"act\",\"action\":\"the next useful work\",\"findings\":{\"c1\":{\"passed\":false,\"evidence\":[\"path\"],\"reason\":\"what the evidence shows\"}},\"handover\":null,\"message\":\"prose\"}. "
            "Decisions are act, complete, blocked or handover. Findings carry only criteria whose status changed; a file, a green command or a confident summary is not evidence until you inspected its content. Complete only when every criterion is supported by inspected artifacts. "
            "Your message is the organism's memory: plain prose on what was tried, what was seen, what was learned and what comes next, appended to story.md and read by future runs. Add no restriction, budget, deadline or ban the human did not write; prefer parallel work and fresh routes over repeating one. "
            "Handover is {\"continue\":true,\"fresh\":[\"observer\"],\"instructions\":\"what to carry on\"}: each role named in fresh gets a new empty context before the work continues, so use it to clear your own context; continue false ends the run for a human to take over.")
ACTOR = ("You are Trident's actor, the hands and eyes of a living organism that works on this computer. "
         "Research the internet by default and take current facts from it. Spawn native subagents or Trident copies whenever work is independent and decide their number yourself. Hold the desktop only while you send input. "
         "screen gives the path and geometry of the most recent capture; Read it or capture a fresh region yourself when you need to see, and before a side-effect click locate the target with Jev and look. After input, inspect the effect. "
         "End your turn with plain prose on what actually happened, including failures, unfinished work, output paths, the real IDs and paths of workers and copies, and each generic helper or prompt sentence you added to the body.")
CONTRACT = ("You have every installed tool of this computer and the owner's standing authorization for research, files, applications, Chrome, images, shell and code; availability is proven by use, not assumed. "
            "The body is the file at body; your shell is PowerShell, so to use its helpers write one script file that adds the body's directory to sys.path, imports main, and makes several calls at once rather than one process per call. "
            "The body you edit is the body of your next run: edit it when a limitation is demonstrated, and when you write the same helper twice make it one generic function in the body with real arguments, return values and no task words, then name it in your reply. "
            "Helpers: find_windows(title_suffix)->HWND list; focus(hwnd); client_size(hwnd)->(width,height); to_screen(hwnd,x,y)->physical point from client pixels; pointer(x,y,flags) in physical pixels, flags 0 move 2 left down 4 left up; click(x,y,pause=0.04)->that point; drag(x1,y1,x2,y2,steps=12,pause=0.018)->end point; go(hwnd,url) focuses that window, pastes url into the address bar and presses Enter; chord(*(vk,0 down or 2 up)) types keys, not text; "
            "paste(hwnd,text) puts text on the clipboard and presses Ctrl+V in that window, for any text including Polish letters; stroke(hwnd,points,pause=0.008)->screen points; capture(path,end=None,region=None)->PNG of the whole virtual screen, or of region (left,top,width,height) in physical pixels; "
            "virtual_screen()->(left,top,width,height); lease('desktop',wait=True) is a context manager, hold it for each burst of input and release it after; "
            "decide(state,questions)->the Jev typesafe/jev-1.14 response, with answers (typed, with probabilities, over text and PNG image parts from image_part(path), question types choice, noul, score and box, every choice given the option change approach) and usage.cost; "
            "locate(path,description,left,top,width,height) captures that physical region, asks Jev, and returns {x,y,present,confidence} in physical pixels, so keep each side between 256 and 1568 pixels. If Jev raises, read the error and proceed another way. "
            "Jev is fast and cheap: use it before side-effect clicks, to verify screens and artifacts, to choose between routes and to test your own criteria. Do not repeat a tool or route because it worked once; vary source and method. "
            "Copies: python body --work new_dir --model your_model --sentence subgoal; a copy inherits this workspace's Cursor configuration, owns its own workspace and story, shares the desktop mutex and the account, and dies with this run's job unless detached; collect child results before you finish. "
            "Memory: story.md is append-only prose shared by every run in this workspace, run_*.md is this run's journal, evidence/<run>/ holds captures. SendInput does not prove application effect; inspect.")
class Mission:
    def __init__(self, work, goal, model, run, started):
        self.work, self.end = work, None
        self.work.mkdir(parents=True, exist_ok=True)
        self.run = run or time.strftime('%Y%m%d_%H%M%S') + '_' + str(os.getpid())
        self.journal, self.story, self.evidence = self.work / f'run_{self.run}.md', self.work / 'story.md', self.work / 'evidence' / self.run
        self.evidence.mkdir(parents=True)
        self.story.touch()
        self.state = dict(goal=goal, model=model, run=self.run, started=started, status='observing', sessions={}, runtime={}, timing=None, criteria={}, checks={}, action=None, result=None, handover=None, screen=None, workers=[], failures=[], artifacts={})
        self.narrate('opening', f'Run {self.run} on {model} begins. Goal: {goal}')
        self.record('goal', goal)
    def record(self, kind, value):
        body = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        journal(self.journal, f"## {time.strftime('%H:%M:%S')} {kind}\n\n{body}\n\n")
    def narrate(self, voice, text):
        with open(self.story, 'a', encoding='utf-8') as story: story.write(f"## {utc()} {self.run} {voice}\n\n{text.strip()}\n\n")
    def artifact(self, path):
        path = (self.work / path).resolve()
        info = path.stat()
        self.state['artifacts'][str(path)] = dict(bytes=info.st_size, modified=datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat())
        return str(path)
    def observe(self, name):
        image = capture(self.evidence / f'{name}.png', self.end)
        left, top, width, height = virtual_screen()
        self.state['screen'] = dict(image=self.artifact(image), left=left, top=top, width=width, height=height, observed=utc())
        return image
    def initialize(self, decision):
        timing = decision['timing']
        if timing['deadline'] is not None:
            if not timing['source'] or timing['source'] not in self.state['goal']: raise ValueError('a deadline needs its verbatim quote from the goal')
            deadline = datetime.fromisoformat(timing['deadline'])
            if deadline.tzinfo is None: raise ValueError('a deadline needs a timezone')
            self.end = time.monotonic() + deadline.timestamp() - time.time()
        if not decision['criteria']: raise ValueError('the goal needs acceptance criteria')
        self.state['timing'] = dict(timing, interpreted=utc())
        self.state['criteria'] = {f'c{number}': text for number, text in enumerate(decision['criteria'], 1)}
        self.record('acceptance', decision)
        remaining(self.end)
    def verify(self, pending):
        for key in self.state['criteria']:
            finding = self.state['checks'][key]
            if finding['passed'] is not True or not finding['reason'] or not finding['evidence']: raise ValueError('criterion without inspected evidence: ' + key)
            for path in finding['evidence']: self.artifact(path)
        if pending: raise ValueError('completion with unfinished tools: ' + ', '.join(pending))
        remaining(self.end)
    def initial(self):
        return dict(goal=self.state['goal'], model=self.state['model'], started=self.state['started'], sessions=self.state['sessions'], now=utc(),
                    python=sys.executable, body=str(BODY), work=str(self.work), memory=dict(story=str(self.story), journal=str(self.journal), evidence=str(self.evidence)), recent=self.story.read_text(encoding='utf-8')[-8000:])
class ACP:
    def __init__(self, mission, resources):
        self.mission, self.inbox, self.counter = mission, queue.Queue(), 0
        self.roles, self.text, self.thought, self.tools, self.turn, self.ready = {}, {}, {}, {}, [], set()
        self.active = None
        config, source = mission.work / 'agent/config', ROOT / 'agent/config'
        if source != config and (source / 'cli-config.json').exists() and not (config / 'cli-config.json').exists():
            shutil.copytree(source, config, ignore=shutil.ignore_patterns('acp-sessions', 'statsig-cache.json*'), dirs_exist_ok=True)
        config.mkdir(parents=True, exist_ok=True)
        cli = config / 'cli-config.json'
        settings = json.loads(cli.read_text(encoding='utf-8')) if cli.exists() else dict(version=1)
        settings.update(permissions=dict(allow=[], deny=[]), approvalMode='allowlist', sandbox=dict(mode='disabled'))
        cli.write_text(json.dumps(settings, ensure_ascii=False), encoding='utf-8')
        job = job_object(resources)
        environment = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', CURSOR_CONFIG_DIR=str(config), CURSOR_DATA_DIR=str(mission.work / 'agent/data'))
        agent = Path(environment['LOCALAPPDATA']) / 'cursor-agent/agent.ps1'
        self.process = subprocess.Popen(['powershell.exe', '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(agent), '--trust', '--workspace', str(mission.work), 'acp'],
                                        cwd=mission.work, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW | SUSPENDED)
        try:
            check(AssignProcessToJobObject(job, self.process._handle))
            status = NtResumeProcess(self.process._handle)
            if status: raise OSError(f'NtResumeProcess returned {status:#x}')
        except BaseException:
            self.process.kill(); self.process.communicate(); raise
        resources.callback(self.process.stdin.close)
        resources.callback(self.cancel)
        for pipe, label in ((self.process.stdout, 'stdout'), (self.process.stderr, 'stderr')):
            threading.Thread(target=self.pump, args=(pipe, label), daemon=True).start()
    def pump(self, pipe, label):
        try:
            for line in pipe: self.inbox.put((label, line))
        finally:
            pipe.close(); self.inbox.put((label, None))
    def send(self, message):
        self.process.stdin.write((json.dumps(dict(jsonrpc='2.0', **message)) + '\n').encode('utf-8')); self.process.stdin.flush()
    def rpc(self, method, params):
        self.counter += 1
        identity, started = self.counter, time.monotonic()
        remaining(self.mission.end)
        self.send(dict(id=identity, method=method, params=params))
        while True:
            remaining(self.mission.end)
            try: label, raw = self.inbox.get(timeout=0.2)
            except queue.Empty: continue
            if label == 'stderr':
                if raw: self.mission.record('stderr', raw.decode('utf-8'))
                continue
            if raw is None: raise EOFError(f'Cursor ACP closed before answering {method}')
            message = json.loads(raw)
            if 'method' in message: self.notice(message); continue
            if message['id'] != identity: raise ValueError('unexpected ACP reply: ' + json.dumps(message, ensure_ascii=False))
            if method == 'session/prompt': self.mission.record('prompt-result', dict(time=utc(), request=identity, seconds=round(time.monotonic() - started, 3), response=message))
            if 'error' in message: raise RuntimeError(json.dumps(message['error'], ensure_ascii=False))
            return message['result']
    def notice(self, message):
        method, params = message['method'], message['params']
        if method == 'session/request_permission':
            option = next(choice['optionId'] for choice in params['options'] if choice['kind'] == 'allow_once')
            self.send(dict(id=message['id'], result=dict(outcome=dict(outcome='selected', optionId=option))))
            self.mission.record('permission', dict(session=params['sessionId'], option=option))
        elif method == 'cursor/create_plan':
            self.send(dict(id=message['id'], result=dict(outcome=dict(outcome='accepted'))))
            self.mission.record('plan', params)
        elif method == 'session/update': self.update(params)
        elif method == 'cursor/task':
            self.send(dict(id=message['id'], result=dict(outcome=dict(outcome='completed'))))
            self.mission.record('task', params)
        elif 'id' in message: raise Blocked('unanswerable ACP request: ' + json.dumps(message, ensure_ascii=False))
        else: self.mission.record(method, params)
    def update(self, params):
        update, session = params['update'], params['sessionId']
        kind, role = update['sessionUpdate'], self.roles[session]
        if kind == 'agent_message_chunk':
            if role == 'worker': self.mission.record('worker', update['content']['text'])
            else: self.text[session] += update['content']['text']
        elif kind == 'agent_thought_chunk':
            if role == 'worker': self.mission.record('worker-thought', update['content']['text'])
            else: self.thought[session] += update['content']['text']
        elif kind in ('tool_call', 'tool_call_update'):
            call = update['toolCallId']
            if kind == 'tool_call':
                if self.text.get(session): self.mission.record(role, self.text[session]); self.text[session] = ''
                self.tools[call] = dict(role=role, session=session, **update)
            else: self.tools[call].update(update)
            event = self.tools[call]
            if event['status'] in ('completed', 'failed'):
                event['time'] = utc()
                del self.tools[call]
                self.turn.append(dict(role=role, kind=event['kind'], title=event['title'], status=event['status'], locations=[place['path'] for place in event.get('locations', [])]))
                if event['status'] == 'failed': self.mission.state['failures'].append(dict(time=event['time'], tool=event['title']))
                self.mission.record('tool', event)
        elif kind == 'subagent_spawned':
            self.roles[update['subagentSessionId']] = 'worker'
            self.mission.state['workers'].append(update)
            self.mission.record('worker-spawned', update)
        else: self.mission.record(kind, update)
    def session(self, role):
        result = self.rpc('session/new', dict(cwd=str(self.mission.work), mcpServers=[]))
        session = result['sessionId']
        self.roles[session], self.text[session], self.thought[session] = role, '', ''
        mind = self.mission.state['model']
        for option, value in (('model', GROK), ('reasoning_effort', mind.removeprefix(GROK + '-').removesuffix('-fast')), ('fast', str(mind.endswith('-fast')).lower())):
            configuration = self.rpc('session/set_config_option', dict(sessionId=session, configId=option, value=value))
        self.mission.state['runtime'][session] = {entry['id']: entry['currentValue'] for entry in configuration['configOptions'] if entry['id'] != 'mode'}
        self.rpc('session/set_mode', dict(sessionId=session, modeId='agent'))
        return session
    def prompt(self, role, update, image):
        if role == 'actor': self.mission.state['sessions'][role] = self.session(role)
        session = self.mission.state['sessions'][role]
        self.active, self.text[session], self.thought[session], self.turn = session, '', '', []
        text = json.dumps(update, ensure_ascii=False)
        if session not in self.ready:
            text = '\n'.join(((OBSERVER if role == 'observer' else ACTOR), CONTRACT, json.dumps(self.mission.initial(), ensure_ascii=False), text))
            self.ready.add(session)
        content = [dict(type='text', text=text)]
        if image: content += [dict(type='text', text=str(image)), dict(type='image', mimeType='image/png', data=base64.b64encode(Path(image).read_bytes()).decode())]
        self.mission.record('prompt', dict(time=utc(), role=role, session=session, request=self.counter + 1, bytes=len(text.encode('utf-8')), image=str(image) if image else None, text=text))
        result = self.rpc('session/prompt', dict(sessionId=session, prompt=content))
        self.active = None
        if self.thought[session]: self.mission.record(role + '-thought', self.thought[session])
        if result['stopReason'] != 'end_turn': raise RuntimeError('Cursor stopped the turn: ' + result['stopReason'])
        self.mission.record(role, self.text[session])
        return self.text[session]
    def cancel(self):
        if self.active:
            self.send(dict(method='session/cancel', params=dict(sessionId=self.active)))
            self.mission.record('cancel', dict(session=self.active, time=utc()))
            self.active = None
def run_goal(work, goal, model, run):
    work, started = Path(work).resolve(), utc()
    with lease('work-' + hashlib.sha256(os.path.normcase(str(work)).encode('utf-8')).hexdigest()):
        mission = Mission(work, goal, choose_mind(goal, model), run, started)
        state, body_before = mission.state, hashlib.sha256(BODY.read_bytes()).hexdigest()
        try:
            with ExitStack() as resources:
                client = ACP(mission, resources)
                client.rpc('initialize', dict(protocolVersion=1, clientCapabilities=dict(fs=dict(readTextFile=False, writeTextFile=False), terminal=False, _meta=dict(parameterizedModelPicker=True, subagents=True)), clientInfo=dict(name='trident', version='3')))
                state['sessions']['observer'] = client.session('observer')
                mission.record('sessions', state['sessions'])
                mission.initialize(json.loads(client.prompt('observer', dict(initialize=True), None)))
                witness, step, update = mission.observe('0'), 0, {}
                while True:
                    remaining(mission.end)
                    decision = json.loads(client.prompt('observer', dict(now=utc(), criteria=state['criteria'], timing=state['timing'], checks=state['checks'], handover=state['handover'], screen=state['screen'], **update), witness))
                    state['checks'].update(decision['findings'])
                    state['action'], state['handover'] = decision['action'], decision['handover']
                    mission.narrate('judgment', decision['message'])
                    verdict = decision['decision']
                    if verdict == 'complete': mission.verify(list(client.tools)); state['status'] = 'completed'; break
                    if verdict == 'blocked': state['status'] = 'blocked'; break
                    if verdict == 'handover':
                        handover = state['handover']
                        if not handover or not handover['instructions']: raise ValueError('a handover needs instructions')
                        if not handover['continue']: state['status'] = 'handover'; break
                        for role in handover['fresh']:
                            if role not in ('actor', 'observer'): raise ValueError('a handover refreshes actor or observer, not ' + role)
                            state['sessions'][role] = client.session(role)
                        mission.record('sessions', state['sessions'])
                        state['action'] = handover['instructions']
                    elif verdict != 'act': raise ValueError('unknown observer decision: ' + verdict)
                    step += 1
                    state['status'] = 'acting'
                    state['result'] = result = client.prompt('actor', dict(action=state['action'], findings=decision['findings'], message=decision['message'], handover=state['handover'], screen=state['screen']), None)
                    if result.strip(): mission.narrate('hands', result)
                    witness = mission.observe(str(step))
                    state['status'] = 'observing'
                    update = dict(result=result, tools=client.turn)
            remaining(mission.end)
        except (Deadline, Blocked) as error:
            state['status'] = 'deadline' if isinstance(error, Deadline) else 'blocked'
            state['failures'].append(dict(time=utc(), error=str(error)))
        except BaseException as error:
            state['status'] = 'failed'
            state['failures'].append(dict(time=utc(), error=f'{type(error).__name__}: {error}'))
            raise
        finally:
            body_after = hashlib.sha256(BODY.read_bytes()).hexdigest()
            elapsed = round(time.time() - datetime.fromisoformat(started).timestamp(), 1)
            passed = [key for key, finding in state['checks'].items() if finding['passed'] is True]
            mission.narrate('closing', f"Run {mission.run} on {state['model']} ended {state['status']} after {elapsed} seconds. Goal: {state['goal']} Passed {len(passed)} of {len(state['criteria'])} criteria ({', '.join(passed) or 'none'}). "
                                       f"Workers: {len(state['workers'])}. Failures: {json.dumps(state['failures'], ensure_ascii=False)}. Last action: {json.dumps(state['action'], ensure_ascii=False)}. Handover: {json.dumps(state['handover'], ensure_ascii=False)}. "
                                       + ('The body changed during this run, so the next run executes the new body.' if body_after != body_before else 'The body did not change.'))
            mission.record('final', dict(status=state['status'], elapsed_seconds=elapsed, passed=passed, criteria=len(state['criteria']), failures=state['failures'], workers=len(state['workers']), action=state['action'], handover=state['handover'], body_changed=body_after != body_before))
        return state['status']
PAGE = ("<!doctype html><meta charset=utf-8><title>Trident</title><style>body{margin:0;background:#10141a}pre{white-space:pre-wrap;margin:0;padding:8px 14px;font:14px/1.4 Consolas,monospace}</style><div id=p></div>"
        "<script>const p=document.getElementById('p'),s=new EventSource('/events');s.onopen=()=>p.replaceChildren();"
        "s.onmessage=e=>{const m=JSON.parse(e.data),d=document.createElement('pre');d.style.color=m.color;d.textContent=m.text;p.appendChild(d);scrollTo(0,9e9)}</script>").encode('utf-8')
class Hub(BaseHTTPRequestHandler):
    feed = None
    def log_message(self, *_): return
    def do_GET(self):
        route, feed = self.path.partition('?')[0], type(self).feed
        if route not in ('/', '/events', '/md'): self.send_error(404); return
        if route == '/events':
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.send_header('Cache-Control', 'no-cache'); self.end_headers()
            box = queue.Queue()
            with feed.lock:
                for event in feed.events: box.put(event)
                feed.boxes.append(box)
            try:
                while True: self.wfile.write(b'data: ' + json.dumps(box.get()).encode('utf-8') + b'\n\n'); self.wfile.flush()
            finally:
                with feed.lock: feed.boxes.remove(box)
            return
        with feed.lock: payload = ''.join(event['text'] for event in feed.events).encode('utf-8') if route == '/md' else PAGE
        mime = 'text/markdown; charset=utf-8' if route == '/md' else 'text/html; charset=utf-8'
        self.send_response(200); self.send_header('Content-Type', mime); self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)
class Feed:
    def __init__(self, root):
        self.root, self.lock, self.boxes, self.events, self.colors = root, threading.Lock(), [], [], {}
        self.cursors = {path: [path.stat().st_size, codecs.getincrementaldecoder('utf-8')()] for path in self.sources()}
        Hub.feed = self
    def sources(self):
        return sorted(self.root.glob('run_*.md')) + list(self.root.glob('story.md'))
    def follow(self):
        for path in self.sources():
            cursor = self.cursors.setdefault(path, [0, codecs.getincrementaldecoder('utf-8')()])
            with path.open('rb') as handle:
                handle.seek(cursor[0]); text = cursor[1].decode(handle.read()); cursor[0] = handle.tell()
            if text: self.relay(path.stem, text)
    def relay(self, source, text):
        with self.lock:
            color = self.colors.setdefault(source, PALETTE[len(self.colors) % len(PALETTE)])
            chunk = f"## {time.strftime('%H:%M:%S')} {color} {source}\n\n{text.rstrip()}\n\n"
            sys.stdout.write(chunk); sys.stdout.flush()
            event = dict(color=color, text=chunk)
            self.events.append(event)
            for box in self.boxes: box.put(event)
    def serve(self):
        with FeedServer(('0.0.0.0', HUB_PORT), Hub) as server:
            self.relay('hub', f'http://127.0.0.1:{HUB_PORT}/')
            server.serve_forever(poll_interval=0.2)
class FeedServer(ThreadingHTTPServer):
    def service_actions(self): Hub.feed.follow()
def main(argv):
    if argv == ['serve']: return Feed(ROOT).serve()
    parser = argparse.ArgumentParser(prog='trident')
    goal = parser.add_mutually_exclusive_group()
    goal.add_argument('--sentence'); goal.add_argument('--mind-file')
    for option in ('work', 'model', 'run'): parser.add_argument('--' + option, default='')
    args = parser.parse_args(argv)
    if args.sentence is not None: sentence = args.sentence
    elif args.mind_file is not None: sentence = Path(args.mind_file).read_text(encoding='utf-8-sig')
    else: sentence = INITIATIVE
    if not sentence.strip(): raise ValueError('the goal sentence is empty')
    return dict(completed=0, deadline=2, handover=2, blocked=3)[run_goal(args.work or ROOT, sentence.strip(), args.model, args.run)]
if __name__ == '__main__':
    try: sys.exit(main(sys.argv[1:]))
    except Blocked as error: sys.stderr.write(str(error) + '\n'); sys.exit(3)
    except KeyboardInterrupt: sys.exit(1)
