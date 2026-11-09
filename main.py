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
IsZoomed = signature(user32, 'IsZoomed', wintypes.BOOL, wintypes.HWND)
SetWindowPos = signature(user32, 'SetWindowPos', wintypes.BOOL, wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
SetForegroundWindow = signature(user32, 'SetForegroundWindow', wintypes.BOOL, wintypes.HWND)
GetForegroundWindow = signature(user32, 'GetForegroundWindow', wintypes.HWND)
GetClientRect = signature(user32, 'GetClientRect', wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
ClientToScreen = signature(user32, 'ClientToScreen', wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.POINT))
GetWindowRect = signature(user32, 'GetWindowRect', wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
GetClassNameW = signature(user32, 'GetClassNameW', ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
GetWindowThreadProcessId = signature(user32, 'GetWindowThreadProcessId', wintypes.DWORD, wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
OpenProcess = signature(kernel32, 'OpenProcess', wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
QueryFullProcessImageNameW = signature(kernel32, 'QueryFullProcessImageNameW', wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
GetSystemMetrics = signature(user32, 'GetSystemMetrics', ctypes.c_int, ctypes.c_int)
CreateJobObjectW = signature(kernel32, 'CreateJobObjectW', wintypes.HANDLE, wintypes.LPVOID, wintypes.LPCWSTR)
SetInformationJobObject = signature(kernel32, 'SetInformationJobObject', wintypes.BOOL, wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
AssignProcessToJobObject = signature(kernel32, 'AssignProcessToJobObject', wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE)
CloseHandle = signature(kernel32, 'CloseHandle', wintypes.BOOL, wintypes.HANDLE)
CreateMutexW = signature(kernel32, 'CreateMutexW', wintypes.HANDLE, wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
WaitForSingleObject = signature(kernel32, 'WaitForSingleObject', wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD)
ReleaseMutex = signature(kernel32, 'ReleaseMutex', wintypes.BOOL, wintypes.HANDLE)
NtResumeProcess = signature(ntdll, 'NtResumeProcess', ctypes.c_long, wintypes.HANDLE)
GetStdHandle = signature(kernel32, 'GetStdHandle', wintypes.HANDLE, wintypes.DWORD)
GetConsoleMode = signature(kernel32, 'GetConsoleMode', wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
SetConsoleMode = signature(kernel32, 'SetConsoleMode', wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD)
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
def wheel(x, y, notches):
    left, top, width, height = virtual_screen()
    send(INPUT(type=0, mi=MOUSEINPUT(round((x - left) * 65535 / (width - 1)), round((y - top) * 65535 / (height - 1)), (int(notches) * 120) & 0xFFFFFFFF, 0x0800 | 0xC001, 0, 0)))
    return int(x), int(y)
def click(x, y, pause=0.04):
    point = (int(round(x)), int(round(y)))
    pointer(*point, 0)
    time.sleep(pause)
    try:
        pointer(*point, 2)
        time.sleep(pause)
    finally:
        pointer(*point, 4)
    time.sleep(pause)
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
def bend(start, control, end, amplitude, samples, rng, jitter=2.0):
    ax, ay = float(start[0]), float(start[1])
    bx, by = float(control[0]), float(control[1])
    cx, cy = float(end[0]), float(end[1])
    count = int(samples)
    if count < 1: raise ValueError('samples')
    phase = rng.uniform(0, math.tau)
    first = rng.uniform(1.4, 3.1)
    second = rng.uniform(4.0, 7.2)
    points = []
    for index in range(count + 1):
        t = index / count
        u = 1.0 - t
        x = u * u * ax + 2 * u * t * bx + t * t * cx
        y = u * u * ay + 2 * u * t * by + t * t * cy
        dx = 2 * u * (bx - ax) + 2 * t * (cx - bx)
        dy = 2 * u * (by - ay) + 2 * t * (cy - by)
        length = math.hypot(dx, dy) or 1.0
        env = math.sin(math.pi * t) ** 0.75
        wave = 0.7 * math.sin(first * math.tau * t + phase) + 0.3 * math.sin(second * math.tau * t + phase * 1.35)
        side = amplitude * env * wave + rng.uniform(-0.25, 0.25) * amplitude * env
        along = rng.uniform(-jitter, jitter)
        points.append((x + (-dy / length) * side + (dx / length) * along, y + (dx / length) * side + (dy / length) * along))
    return points
def hook(start, end, bow, amplitude, samples, rng):
    dx, dy = float(end[0]) - float(start[0]), float(end[1]) - float(start[1])
    length = math.hypot(dx, dy) or 1.0
    bulge = min(max(0.5 + rng.uniform(-0.08, 0.08), 0.28), 0.74)
    px, py = -dy / length, dx / length
    side = bow * rng.uniform(0.85, 1.15)
    control = (float(start[0]) + dx * bulge + px * side, float(start[1]) + dy * bulge + py * side)
    points = bend(start, control, end, amplitude, samples, rng, jitter=rng.uniform(1.4, 2.6))
    if rng.random() < 0.5 and len(points) > 5:
        x1, y1 = points[-1]
        x0, y0 = points[-2]
        points.append((x1 + (x1 - x0) * rng.uniform(0.2, 0.65), y1 + (y1 - y0) * rng.uniform(0.2, 0.65)))
    return points
def chord(*keys):
    send(*(INPUT(type=1, ki=KEYBDINPUT(code, 0, transition, 0, 0)) for code, transition in keys))
def place(hwnd, left, top, width, height):
    if IsIconic(hwnd) or IsZoomed(hwnd): ShowWindow(hwnd, 9)
    check(SetWindowPos(hwnd, None, int(left), int(top), int(width), int(height), 0x0044))
    return frame(hwnd)
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
def frame(hwnd):
    rect = wintypes.RECT()
    check(GetWindowRect(hwnd, ctypes.byref(rect)))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
def window_info(hwnd):
    title, class_name = ctypes.create_unicode_buffer(512), ctypes.create_unicode_buffer(256)
    GetWindowTextW(hwnd, title, 512)
    GetClassNameW(hwnd, class_name, 256)
    pid = wintypes.DWORD()
    GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    image, handle = '', OpenProcess(0x1000, False, pid.value)
    if handle:
        buf, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
        if QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)): image = buf.value
        CloseHandle(handle)
    return dict(hwnd=int(hwnd), title=title.value, class_name=class_name.value, pid=int(pid.value), image=image, frame=frame(hwnd), iconic=bool(IsIconic(hwnd)))
def stroke(hwnd, points, pause=0.008, focus_window=True):
    if focus_window: focus(hwnd)
    path = [to_screen(hwnd, x, y) for x, y in points]
    pointer(*path[0], 0); pointer(*path[0], 2)
    try:
        for x, y in path[1:-1]: pointer(x, y, 0); time.sleep(pause)
        pointer(*path[-1], 0)
    finally:
        pointer(*path[-1], 4)
    time.sleep(max(pause, 0.02))
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
def elements(hwnd):
    raw = powershell("$ErrorActionPreference='Stop';[Threading.Thread]::CurrentThread.CurrentCulture=[Globalization.CultureInfo]::InvariantCulture;Add-Type -AssemblyName UIAutomationClient;"
                     f"$root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]{int(hwnd)});"
                     "$all=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition);"
                     "$list=New-Object System.Collections.Generic.List[object];"
                     "foreach($e in $all){$c=$e.Current;$r=$c.BoundingRectangle;if($c.IsOffscreen -or $r.Width -lt 2 -or $r.Height -lt 2){continue};"
                     "$selected=$null;$value=$null;$minimum=$null;$maximum=$null;"
                     "try{$selected=$e.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Current.IsSelected}catch{};"
                     "try{if($null -eq $selected){$selected=($e.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Current.ToggleState -eq [System.Windows.Automation.ToggleState]::On)}}catch{};"
                     "try{$range=$e.GetCurrentPattern([System.Windows.Automation.RangeValuePattern]::Pattern).Current;$value=$range.Value;$minimum=$range.Minimum;$maximum=$range.Maximum}catch{};"
                     "$list.Add([ordered]@{name=[string]$c.Name;type=[string]$c.ControlType.ProgrammaticName;automation_id=[string]$c.AutomationId;class_name=[string]$c.ClassName;"
                     "left=[int][Math]::Round($r.X);top=[int][Math]::Round($r.Y);width=[int][Math]::Round($r.Width);height=[int][Math]::Round($r.Height);access_key=[string]$c.AccessKey;"
                     "selected=$selected;value=$value;minimum=$minimum;maximum=$maximum})};"
                     "if($list.Count){$list|ConvertTo-Json -Compress -Depth 4}").strip()
    data = json.loads(raw) if raw else []
    return [data] if isinstance(data, dict) else data
def locate(path, description, left, top, width, height):
    answers = decide([f'A {width} by {height} pixel screen region:', image_part(capture(path, region=(left, top, width, height)))],
                     dict(target=dict(type='box', instructions=f'{description}, in the region'), present=dict(type='noul', instructions=f'The region shows {description}.')))['answers']
    box = answers['target']['box']
    return dict(x=round(left + (box['left'] + box['right']) / 2 * width), y=round(top + (box['top'] + box['bottom']) / 2 * height), present=answers['present']['noul'], confidence=answers['target']['confidence'])
def journal(path, text):
    with open(path, 'a', encoding='utf-8', newline='') as handle: handle.write(text)
def console_vt():
    handle = GetStdHandle(wintypes.DWORD(0xFFFFFFF5))
    mode = wintypes.DWORD()
    if not handle or not GetConsoleMode(handle, ctypes.byref(mode)): return False
    return bool(SetConsoleMode(handle, mode.value | 0x0004))
def wrap_text(text, cols):
    cols = max(1, int(cols))
    lines = []
    for raw in text.split('\n'):
        if raw == '': lines.append('')
        else: lines.extend(raw[i:i + cols] for i in range(0, len(raw), cols))
    return lines
def pipe_body(text):
    return ''.join('|' + line + '\n' for line in text.split('\n'))
def unpipe(lines):
    return '\n'.join(lines)
def indent_value(value, indent=1):
    pad = '  ' * indent
    if isinstance(value, str): return '\n'.join(pad + line if line else pad for line in value.splitlines()) or pad
    if isinstance(value, dict):
        rows = []
        for key, item in value.items():
            if isinstance(item, (dict, list)): rows.append(f'{pad}{key}:'); rows.append(indent_value(item, indent + 1))
            else: rows.append(f'{pad}{key}: {item}')
        return '\n'.join(rows)
    if isinstance(value, list): return '\n'.join(indent_value(item, indent) for item in value)
    return f'{pad}{value}'
def tool_text(event):
    lines = []
    for key, label in (('toolCallId', 'id'), ('kind', 'kind'), ('title', 'title'), ('status', 'status'), ('time', 'time')):
        if event.get(key) is not None: lines.append(label + ': ' + str(event[key]))
    for place in event.get('locations') or []:
        lines.append('location:'); lines.append(indent_value(place))
    if event.get('rawInput') is not None:
        lines.append('input:'); lines.append(indent_value(event['rawInput']))
    if event.get('rawOutput') is not None:
        lines.append('rawOutput:'); lines.append(indent_value(event['rawOutput']))
    if event.get('content') is not None:
        lines.append('content:'); lines.append(indent_value(event['content']))
    if event.get('error') is not None:
        lines.append('error:'); lines.append(indent_value(event['error']))
    return '\n'.join(lines)
def prompt_usage(message):
    usage = (message.get('result') or {}).get('usage') if isinstance(message, dict) else None
    if not isinstance(usage, dict): return None
    if not all(isinstance(usage.get(key), (int, float)) and not isinstance(usage.get(key), bool) for key in ('inputTokens', 'outputTokens', 'totalTokens')): return None
    text = f"in={usage['inputTokens']},out={usage['outputTokens']},total={usage['totalTokens']}"
    for key, label in (('thoughtTokens', 'thought'), ('cachedReadTokens', 'cache_read'), ('cachedWriteTokens', 'cache_write')):
        if isinstance(usage.get(key), (int, float)) and not isinstance(usage.get(key), bool): text += f',{label}={usage[key]}'
    return text
def worker_notice(outer, update):
    child = update['subagentSessionId'] if 'subagentSessionId' in update else None
    lines = ['source: ' + str(outer)]
    if child is not None: lines.append('target: ' + str(child))
    for key in ('name', 'task', 'state'):
        if update.get(key) is not None: lines.append(key + ': ' + str(update[key]))
    return child, '\n'.join(lines)
def memory_attachment(acknowledged, exists, mtime):
    if not exists: return None
    if acknowledged is None or mtime > acknowledged: return mtime
    return None
class Journal:
    def __init__(self, path=None, writing=True):
        self.path, self.writing = path, writing
        self.messages, self.index, self.open, self.usage = [], {}, {}, {}
        self.shown, self.focus, self.next_seq, self.drawn = [], None, 1, 0
        self.fixed_rows = self.fixed_cols = None
        self.tty, self._vt, self.sink = None, None, None
        self.dirty, self._raw, self._body = set(), '', None
        self.legacy, self._grammar = False, None
    def dimensions(self):
        if self.fixed_rows and self.fixed_cols: return self.fixed_rows, self.fixed_cols
        size = shutil.get_terminal_size(fallback=(80, 24))
        return size.lines, size.columns
    def use_tty(self):
        if self.tty is False: return False
        if self.tty is True: return True
        if self._vt is None: self._vt = bool(sys.stdout.isatty() and console_vt())
        return self._vt
    def header(self, msg):
        request = msg.get('request', 'unknown')
        if msg['role'] == 'run': context, cost = 'unknown', None
        else:
            info = self.usage.get(msg['session'], {})
            context, cost = info.get('context', 'unknown'), info.get('cost')
        line = f"## {msg['seq']} {msg['role']} {msg['channel']} {msg['session']} request={request} context={context}"
        if cost: line += f" cost={cost}"
        return line
    def display_cols(self, cols):
        cols = int(cols)
        if cols >= 2: return cols - 1
        return max(1, cols)
    def _fits(self, text, width, rows):
        return rows >= 1 and len(wrap_text(text, width)) <= rows
    def _marked_suffix(self, msg, width, rows):
        session, prefix = str(msg['session']), f"## {msg['seq']} ..."
        if not self._fits(prefix, width, rows): return None
        best, low, high = 0, 1, len(session)
        while low <= high:
            mid = (low + high) // 2
            if self._fits(prefix + session[-mid:], width, rows): best, low = mid, mid + 1
            else: high = mid - 1
        if best < 1: return None
        suffix = session[-best:]
        for other in {item['session'] for item in self.messages}:
            if other != session and other.endswith(suffix): return None
        return prefix + suffix
    def _sequence_head(self, seq, width, rows):
        plain = f"## {seq}"
        if self._fits(plain, width, rows): return plain
        budget = max(0, rows) * width
        if budget >= 3: return plain[:budget - 3] + '...'
        return '...'[:budget]
    def _identity(self, msg, width, rows, session_forms):
        if session_forms:
            if self._fits(self.header(msg), width, rows): return self.header(msg)
            session, seq, role, channel = str(msg['session']), msg['seq'], msg['role'], msg['channel']
            for form in (f"## {seq} {role} {channel} {session}", f"## {seq} {role} {session}", f"## {seq} {session}"):
                if self._fits(form, width, rows): return form
            marked = self._marked_suffix(msg, width, rows)
            if marked is not None: return marked
            clipped = f"## {seq} clipped"
            if self._fits(clipped, width, rows): return clipped
        return self._sequence_head(msg['seq'], width, rows)
    def fitted(self, msg, cols, limit):
        view, width = max(1, int(limit)), self.display_cols(cols)
        body, reserve = msg['body'], msg['body'] != '' and max(1, int(limit)) >= 2
        head_rows = view - 1 if reserve else view
        session_forms = int(cols) >= 2 and not (body != '' and view < 2)
        lines = wrap_text(self._identity(msg, width, head_rows, session_forms), width)[:head_rows]
        if reserve:
            tail_rows = view - len(lines)
            if tail_rows < 1:
                lines, tail_rows = lines[:max(0, view - 1)], 1
            if tail_rows > 0: lines.extend(wrap_text(body, width)[-tail_rows:])
        return lines[:view]
    def sync_window(self):
        if self.legacy and not self.messages:
            rows, cols = self.dimensions()
            self.shown = []
            self.window = wrap_text('this earlier form is not projected', self.display_cols(cols))[:max(1, rows - 1)]
            return self.window
        rows, cols = self.dimensions()
        limit = max(1, rows - 1)
        blocks = []
        for seq in list(self.shown):
            msg = self.index.get(seq)
            if msg is not None: blocks.append((seq, self.fitted(msg, cols, limit)))
        while len(blocks) > 1 and sum(len(lines) for _, lines in blocks) > limit: blocks.pop(0)
        if self.focus is not None and self.focus in self.index and all(seq != self.focus for seq, _ in blocks):
            blocks = [(self.focus, self.fitted(self.index[self.focus], cols, limit))]
        self.shown = [seq for seq, _ in blocks]
        self.window = [line for _, lines in blocks for line in lines]
        return self.window
    def touch(self, seq):
        if seq not in self.shown: self.shown.append(seq)
        self.focus = seq
        self.dirty.add(seq)
        self.sync_window()
    def write(self, record):
        if self.path is not None and self.writing: journal(self.path, record)
        if self.writing and not self.use_tty(): self._out(record)
    def _out(self, text):
        if self.sink is not None: self.sink.append(text)
        else: sys.stdout.write(text); sys.stdout.flush()
    def paint(self):
        if not self.use_tty(): return
        lines = self.sync_window()
        limit = max(1, self.dimensions()[0] - 1)
        up = min(self.drawn, limit) if self.drawn else 0
        text = (f'\x1b[{up}A\x1b[J' if up else '') + ('\n'.join(lines) + '\n' if lines else '')
        self.drawn = len(lines)
        self._out(text)
    def insert(self, seq, role, channel, session, body, request, hold):
        msg = dict(seq=seq, role=role, channel=channel, session=str(session), body=body, request=request)
        self.messages.append(msg); self.index[seq] = msg
        self.next_seq = max(self.next_seq, seq + 1)
        if hold: self.open[(str(session), channel)] = seq
        self.touch(seq)
        return msg
    def opening_record(self, msg):
        return self.header(msg) + '\n' + pipe_body(msg['body']) + '\n'
    def say(self, role, channel, session, body, request='unknown'):
        msg = self.insert(self.next_seq, role, channel, session, body, request, False)
        self.write(self.opening_record(msg)); self.paint(); return msg
    def chunk(self, role, channel, session, text):
        if not text: return
        session = str(session)
        seq = self.open.get((session, channel))
        if seq is None:
            msg = self.insert(self.next_seq, role, channel, session, text, 'unknown', True)
            self.write(self.opening_record(msg))
        else:
            self.index[seq]['body'] += text
            self.write(f'.. {seq}\n' + pipe_body(text) + '\n')
            self.touch(seq)
        self.paint()
    def close(self, session, channels=('text', 'thought')):
        session = str(session)
        for channel in channels: self.open.pop((session, channel), None)
    def set_usage(self, session, context=None, cost=None, cost_op='keep'):
        info = self.usage.setdefault(str(session), {'context': 'unknown', 'cost': None})
        if context is not None: info['context'] = context
        if cost_op == 'set': info['cost'] = cost
        elif cost_op == 'clear': info['cost'] = None
        line = f"@@ {session} context={info['context']}"
        if cost_op == 'clear': line += ' cost='
        elif info.get('cost'): line += f" cost={info['cost']}"
        self.write(line + '\n')
        for msg in self.messages:
            if msg['session'] == str(session) and msg['role'] != 'run': self.dirty.add(msg['seq'])
        self.paint()
    def event(self, seq):
        msg = self.index[seq]
        color = {'observer': PALETTE[0], 'actor': PALETTE[1], 'worker': PALETTE[2], 'run': PALETTE[3]}.get(msg['role'], PALETTE[4])
        return dict(seq=seq, role=msg['role'], color=color, text=self.header(msg) + '\n' + msg['body'])
    def ingest(self, text):
        self._raw = getattr(self, '_raw', '') + text
        while '\n' in self._raw:
            line, self._raw = self._raw.split('\n', 1)
            self._consume(line)
    def _consume(self, line):
        if line.endswith('\r'): line = line[:-1]
        if self.legacy: return
        if self._body is not None:
            if line.startswith('|'): self._body.append(line[1:]); return
            self._flush_body()
        if line == '' or line.startswith('@@ '):
            if line.startswith('@@ '): self._apply_usage(line)
            return
        if line.startswith('## '):
            if self._grammar is None:
                if ' request=' not in line or ' context=' not in line:
                    self.legacy = True
                    return
                self._grammar = True
            self._begin(True, line); return
        if line.startswith('.. '): self._begin(False, line)
    def _begin(self, opening, line):
        self._body, self._opening = [], opening
        if opening:
            left, context = line.rsplit(' context=', 1)
            left, request = left.rsplit(' request=', 1)
            seq, role, channel, session = left[3:].split(' ', 3)
            self._meta = (int(seq), role, channel, session, request)
        else:
            self._meta = int(line[3:].strip())
    def _flush_body(self):
        body, opening, meta = unpipe(self._body), self._opening, self._meta
        self._body = self._opening = self._meta = None
        if opening:
            seq, role, channel, session, request = meta
            self.insert(seq, role, channel, session, body, request, channel in ('text', 'thought'))
        else:
            self.index[meta]['body'] += body
            self.touch(meta)
    def _apply_usage(self, line):
        rest = line[3:]
        session, rest = rest.split(' ', 1)
        context = rest[len('context='):] if rest.startswith('context=') else 'unknown'
        cost, op = None, 'keep'
        if ' cost=' in context:
            context, cost = context.rsplit(' cost=', 1)
            op = 'clear' if cost == '' else 'set'
        info = self.usage.setdefault(str(session), {'context': 'unknown', 'cost': None})
        info['context'] = context
        if op == 'set': info['cost'] = cost
        elif op == 'clear': info['cost'] = None
        for msg in self.messages:
            if msg['session'] == str(session) and msg['role'] != 'run': self.dirty.add(msg['seq'])
        self.sync_window()
def choose_mind(goal, model):
    if model:
        if model not in MINDS: raise SystemExit('unknown mind: ' + model)
        return model
    return next((mind for mind in sorted(MINDS, key=len, reverse=True) if mind in goal.lower()), MIND)
OBSERVER = ("Return one JSON object without Markdown. You are Trident's observer, the judgment of a living organism that works on this computer. The human sentence is the only assignment. Today's date and current facts come from the system clock and the internet, never from memory. "
            "On the first turn return only {\"timing\":{\"deadline\":null,\"source\":null},\"criteria\":[\"...\"]}: a deadline exists only when the human wrote one, as an ISO timestamp with timezone plus the verbatim quote in source; criteria describe the result the human will hold, never procedure, tools, reading or timing. "
            "On every later turn you are shown the latest screen capture and the actor's reply and tools; inspect real evidence yourself and return {\"decision\":\"act\",\"action\":\"the next useful work\",\"findings\":{\"c1\":{\"passed\":false,\"evidence\":[\"path\"],\"reason\":\"what the evidence shows\"}},\"handover\":null,\"message\":\"prose\"}. "
            "An action is one stage whose result the next look can judge; work that can proceed together is one stage. "
            "Decisions are act, complete, blocked or handover. Findings carry only criteria whose status changed; a file, a green command or a confident summary is not evidence until you inspected its content. Do not mark a criterion passed in a decision whose action still changes that result. Complete only when every criterion is supported by inspected artifacts. "
            "Your message is the organism's memory: plain prose on what was tried, what was seen, what was learned, appended to story.md. Add no restriction, budget, deadline or ban the human did not write; prefer parallel work and fresh routes over repeating one. "
            "At each judgment redraw memory.png in place, one image of 1568 by 1568 pixels, with text, shapes, timelines and pasted crops, so a glance shows what is done, in progress and next. Condense or erase parts of it when it gets crowded. "
            "Handover is {\"continue\":true,\"fresh\":[\"observer\"],\"instructions\":\"what to carry on\"}: each role named in fresh gets a new empty context before the work continues, so use it to clear your own context; continue false ends the run for a human to take over.")
ACTOR = ("You are Trident's actor, the hands and eyes of a living organism that works on this computer. "
         "Research the internet by default and take current facts from it. Spawn native subagents or Trident copies whenever work is independent and decide their number yourself. Hold the desktop only while you send input. "
         "screen gives the path and geometry of the most recent capture; Read it or capture a fresh region yourself when you need to see, and before a side-effect click locate the target with Jev and look. After input, inspect the effect. "
         "End your turn when the action is done or cannot be done, with plain prose on what actually happened, including failures, unfinished work, output paths, the real IDs and paths of workers and copies, and each generic helper or prompt sentence you added to the body.")
CONTRACT = ("You have every installed tool of this computer and the owner's standing authorization for research, files, applications, Chrome, images, shell and code; availability is proven by use, not assumed. "
            "The body is the file at body; your shell is PowerShell, so to use its helpers write one script file that adds the body's directory to sys.path, imports main, and makes several calls at once rather than one process per call. "
            "The body you edit is the body of your next run: edit it when a limitation is demonstrated, and when you write the same helper twice make it one generic function in the body with real arguments, return values and no task words, then name it in your reply. "
            "Helpers: find_windows(title_suffix)->HWND list; focus(hwnd); client_size(hwnd)->(width,height); to_screen(hwnd,x,y)->physical point from client pixels; pointer(x,y,flags) in physical pixels, flags 0 move 2 left down 4 left up; click(x,y,pause=0.04)->that point; drag(x1,y1,x2,y2,steps=12,pause=0.018)->end point; go(hwnd,url) focuses that window, pastes url into the address bar and presses Enter; chord(*(vk,0 down or 2 up)) types keys, not text; "
            "paste(hwnd,text) puts text on the clipboard and presses Ctrl+V in that window, for any text including Polish letters; stroke(hwnd,points,pause=0.008)->screen points; capture(path,end=None,region=None)->PNG of the whole virtual screen, or of region (left,top,width,height) in physical pixels; "
            "virtual_screen()->(left,top,width,height); lease('desktop',wait=True) is a context manager, hold it for each burst of input and release it after; "
            "decide(state,questions)->the Jev typesafe/jev-1.14 response, with answers (typed, with probabilities, over text and PNG image parts from image_part(path), every choice given the option change approach) and usage.cost; questions map ids to {type, instructions, criteria}; noul criteria are optional {true, false}; choice criteria are {option: description}; score criteria are an ordered array of 2 to 10 levels, from low to high; box needs an image in state and takes no criteria; several images in one state, each image side 280 to 1568 pixels; "
            "locate(path,description,left,top,width,height) captures that physical region, asks Jev, and returns {x,y,present,confidence} in physical pixels. "
            "wheel(x,y,notches) scrolls at that point; frame(hwnd)->(left,top,width,height) of the window; elements(hwnd)->visible controls with name, type, bounds, selection and value. "
            "Do not repeat a tool or route because it worked once; vary source and method. "
            "Copies: python body --work new_dir --model your_model --sentence subgoal; a copy inherits this workspace's Cursor configuration, owns its own workspace and story, shares the desktop mutex and the account, and dies with this run's job unless detached; collect child results before you finish. "
            "Memory: story.md is append-only prose shared by every run in this workspace, run_*.md is this run's journal, evidence/<run>/ holds captures. A new session is shown memory.png instead of a story tail. The observer alone redraws that image. The screen capture outranks it. SendInput does not prove application effect; inspect.")
class Mission:
    def __init__(self, work, goal, model, run, started):
        self.work, self.end = work, None
        self.work.mkdir(parents=True, exist_ok=True)
        self.run = run or time.strftime('%Y%m%d_%H%M%S') + '_' + str(os.getpid())
        self.journal, self.story, self.evidence = self.work / f'run_{self.run}.md', self.work / 'story.md', self.work / 'evidence' / self.run
        self.evidence.mkdir(parents=True)
        self.story.touch()
        self.stream = Journal(self.journal)
        self.state = dict(goal=goal, model=model, run=self.run, started=started, status='observing', sessions={}, runtime={}, timing=None, criteria={}, checks={}, action=None, result=None, handover=None, screen=None, workers=[], failures=[], artifacts={})
        self.narrate('opening', f'Run {self.run} on {model} begins. Goal: {goal}')
        self.record('goal', goal)
    def record(self, kind, value, request='unknown'):
        body = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        self.stream.say('run', kind, self.run, body, request)
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
                    python=sys.executable, body=str(BODY), work=str(self.work), memory=dict(story=str(self.story), journal=str(self.journal), evidence=str(self.evidence)))
class ACP:
    def __init__(self, mission, resources):
        self.mission, self.inbox, self.counter = mission, queue.Queue(), 0
        self.roles, self.text, self.thought, self.tools, self.turn, self.ready = {}, {}, {}, {}, [], set()
        self.memory_ack, self.active = {}, None
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
            if method == 'session/prompt': self.mission.record('prompt-result', dict(time=utc(), request=identity, seconds=round(time.monotonic() - started, 3), response=message), prompt_usage(message) or 'unknown')
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
        kind = update['sessionUpdate']
        if kind == 'subagent_spawned':
            child, body = worker_notice(session, update)
            self.mission.state['workers'].append(update)
            if child is not None:
                self.roles[child], self.text[child], self.thought[child] = 'worker', '', ''
                self.mission.stream.say('worker', 'spawn', child, body)
            else: self.mission.stream.say('worker', 'spawn', session, body)
            return
        if kind == 'subagent_state_update':
            child, body = worker_notice(session, update)
            if child is not None:
                self.roles[child] = 'worker'
                self.mission.stream.say('worker', 'state', child, body)
                if update.get('state') in ('completed', 'failed', 'cancelled', 'disconnected'): self.mission.stream.close(child)
            else: self.mission.stream.say('worker', 'state', session, body)
            return
        if kind == 'usage_update':
            used, size, cost = update.get('used'), update.get('size'), update.get('cost', None)
            context = f'{used}/{size} tokens' if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (used, size)) else None
            if 'cost' not in update: self.mission.stream.set_usage(session, context=context)
            elif cost is None: self.mission.stream.set_usage(session, context=context, cost=None, cost_op='clear')
            elif isinstance(cost, dict) and 'amount' in cost and 'currency' in cost: self.mission.stream.set_usage(session, context=context, cost=f"{cost['amount']} {cost['currency']}", cost_op='set')
            else: self.mission.stream.set_usage(session, context=context)
            return
        role = self.roles.get(session, 'run')
        if kind == 'agent_message_chunk':
            text = (update.get('content') or {}).get('text') or ''
            if role != 'worker': self.text[session] = self.text.get(session, '') + text
            self.mission.stream.chunk(role, 'text', session, text)
        elif kind == 'agent_thought_chunk':
            text = (update.get('content') or {}).get('text') or ''
            if role != 'worker': self.thought[session] = self.thought.get(session, '') + text
            self.mission.stream.chunk(role, 'thought', session, text)
        elif kind in ('tool_call', 'tool_call_update'):
            call = update['toolCallId']
            if kind == 'tool_call':
                self.mission.stream.close(session)
                self.thought[session], self.text[session] = '', ''
                self.tools[call] = dict(role=role, session=session, **update)
            else: self.tools[call].update(update)
            event = self.tools[call]
            if event['status'] in ('completed', 'failed'):
                event['time'] = utc()
                del self.tools[call]
                self.turn.append(dict(role=role, kind=event['kind'], title=event['title'], status=event['status'], locations=[place['path'] for place in event.get('locations', [])]))
                if event['status'] == 'failed': self.mission.state['failures'].append(dict(time=event['time'], tool=event['title']))
                self.mission.stream.say(role, 'tool', session, tool_text(event))
        else: self.mission.stream.say(role, kind, session, json.dumps(update, ensure_ascii=False))
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
        fresh = session not in self.ready
        if fresh:
            text = '\n'.join(((OBSERVER if role == 'observer' else ACTOR), CONTRACT, json.dumps(self.mission.initial(), ensure_ascii=False), text))
            self.ready.add(session)
        content = [dict(type='text', text=text)]
        picture = self.mission.work / 'memory.png'
        exists = picture.is_file()
        attached = memory_attachment(self.memory_ack.get(session), exists, picture.stat().st_mtime if exists else None)
        if attached is not None: content += [dict(type='text', text=str(picture)), dict(type='image', mimeType='image/png', data=base64.b64encode(picture.read_bytes()).decode())]
        if image: content += [dict(type='text', text=str(image)), dict(type='image', mimeType='image/png', data=base64.b64encode(Path(image).read_bytes()).decode())]
        self.mission.record('prompt', dict(time=utc(), role=role, session=session, request=self.counter + 1, bytes=len(text.encode('utf-8')), image=str(image) if image else None, text=text))
        try: result = self.rpc('session/prompt', dict(sessionId=session, prompt=content))
        except BaseException:
            self.mission.stream.close(session)
            raise
        self.active = None
        self.mission.stream.close(session)
        if attached is not None: self.memory_ack[session] = attached
        if result['stopReason'] != 'end_turn': raise RuntimeError('Cursor stopped the turn: ' + result['stopReason'])
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
                    state['result'] = result = client.prompt('actor', dict(action=state['action'], handover=state['handover'], screen=state['screen']), None)
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
        "<script>const p=document.getElementById('p'),s=new EventSource('/events'),els=new Map();let g=0;"
        "s.onopen=()=>{p.replaceChildren();els.clear();g=0};"
        "s.onmessage=e=>{const m=JSON.parse(e.data);if(m.gen<g)return;if(m.gen>g){p.replaceChildren();els.clear();g=m.gen}if(m.reset)return;"
        "const k=m.legacy?'legacy':m.seq;let d=els.get(k);if(!d){d=document.createElement('pre');if(m.color)d.style.color=m.color;p.appendChild(d);els.set(k,d)}"
        "d.textContent=m.text;scrollTo(0,9e9)}</script>").encode('utf-8')
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
        with feed.lock: payload = ''.join(event['text'] for event in feed.events if not event.get('legacy')).encode('utf-8') if route == '/md' else PAGE
        mime = 'text/markdown; charset=utf-8' if route == '/md' else 'text/html; charset=utf-8'
        self.send_response(200); self.send_header('Content-Type', mime); self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)
class Feed:
    def __init__(self, root):
        self.root, self.lock, self.boxes, self.events = root, threading.Lock(), [], []
        self.view, self.current, self.offset = Journal(writing=False), None, 0
        self.decoder = codecs.getincrementaldecoder('utf-8')()
        self.gen, self.legacy_noted = 0, False
        Hub.feed = self
    def newest(self):
        runs = sorted(self.root.glob('run_*.md'))
        return runs[-1] if runs else None
    def upsert(self, event):
        shown = dict(event, gen=self.gen, run=self.current.name)
        for index, old in enumerate(self.events):
            if not old.get('legacy') and old.get('seq') == shown.get('seq'):
                self.events[index] = shown
                break
        else: self.events.append(shown)
        for box in self.boxes: box.put(dict(shown))
    def _switch(self, path):
        drawn = self.view.drawn
        self.gen += 1
        self.current, self.offset, self.events = path, 0, []
        self.view, self.decoder = Journal(writing=False), codecs.getincrementaldecoder('utf-8')()
        self.view.drawn = drawn
        self.legacy_noted = False
        reset = dict(gen=self.gen, reset=True, run=path.name)
        for box in self.boxes: box.put(dict(reset))
    def _legacy(self, path):
        self.legacy_noted = True
        notice = dict(legacy=True, gen=self.gen, run=path.name, text='this earlier form is not projected')
        self.events.append(dict(notice))
        for box in self.boxes: box.put(dict(notice))
    def follow(self):
        path = self.newest()
        if path is None: return
        with self.lock:
            if path != self.current: self._switch(path)
            if self.legacy_noted: return
            with path.open('rb') as handle:
                handle.seek(self.offset); data = handle.read(); self.offset = handle.tell()
            if not data: return
            text = self.decoder.decode(data)
            self.view.ingest(text)
            if self.view.legacy:
                self._legacy(path)
                if self.view.use_tty(): self.view.paint()
                else: sys.stdout.write('this earlier form is not projected\n'); sys.stdout.flush()
                return
            if self.view.use_tty(): self.view.paint()
            else: sys.stdout.write(text); sys.stdout.flush()
            for seq in sorted(self.view.dirty): self.upsert(self.view.event(seq))
            self.view.dirty.clear()
    def serve(self):
        with FeedServer(('0.0.0.0', HUB_PORT), Hub) as server:
            sys.stderr.write(f'http://127.0.0.1:{HUB_PORT}/\n')
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
