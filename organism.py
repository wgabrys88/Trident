import argparse, base64, ctypes, hashlib, json, math, os, queue, re, socket, struct, subprocess, sys, threading, time, urllib.request, uuid, zlib
from contextlib import contextmanager
from ctypes import wintypes as W
from pathlib import Path
CODE = Path(__file__).resolve().parent
PYTHON = Path(sys.executable)
NORM, PORT = 1000, 8765
SET, RUN = {}, None
HEAD = re.compile(r'(?m)^## (\d\d:\d\d:\d\d) (\S+)(?:[ \t]+([^\n]*))?$')
WAKE = re.compile(r'(?m)^## wake ')
COMPONENTS = {'eye', 'decide', 'hands', 'capture', 'windows', 'region', 'place', 'story', 'ledger', 'stop', 'measure', 'map'}
BLOCKED = {'eye', 'decide', 'hands', 'capture', 'windows', 'region', 'place', 'measure'}
BRAINS = ('grok-4.7-low', 'grok-4.7-low-fast', 'grok-4.7-medium', 'grok-4.7-medium-fast', 'grok-4.7-high', 'grok-4.7-high-fast', 'grok-4.7-xhigh', 'grok-4.7-xhigh-fast')
ACTIONS = ('left_click', 'right_click', 'double_click', 'triple_click', 'mouse_move', 'left_click_drag', 'key', 'type', 'scroll', 'hscroll', 'wait', 'terminate')
POINTER = ACTIONS[:6]
FARA_ONLY = ('visit_url', 'history_back', 'web_search', 'read_page_answer_question', 'pause_and_memorize_fact', 'ask_user_question')
FARA_SYSTEM = 'You are Fara, a computer use agent (CUA) specialized for web browsers. You are developed by Microsoft AI Frontiers. You assist users with completing and automating tasks that require the use of a web browser.\n\nThe model was trained in the timeframe of January - April 2026. You can effectively perform tasks even beyond this range by accessing the web browser and using the latest information on the live web. But your knowledge cutoff is limited to early 2026, so you may not be aware of events or developments that occurred after that time, without explicitly browsing and searching for latest information on the web.\n\nThis edition of the model was trained using SFT on top of Qwen3.5-4B, using a synthetic data mixture generated and developed by Microsoft AI Frontiers.\n\nA critical point is a situation where we must pause and request information or confirmation from the user before proceeding. There are three types:\n\nCase 1: Missing User Information — The task requires personal information that the user has not provided (e.g., email, phone number, address, payment details). Never fabricate or assume personal information. Fill in only what the user has explicitly provided, then pause and ask for any missing required fields. (e.g., form requires phone number but user only gave name and email -> fill name and email, then ask for phone number.) If the user has provided all required information, proceed without stopping.\n\nCase 2: Underspecified Task — The task description is ambiguous or missing details needed to make a decision at the current step. Pause and ask for clarification. (e.g., user asks to book a flight but doesn\'t specify destination -> ask for destination.) If the user\'s instructions contain all information needed for the current decision, proceed without stopping.\n\nCase 3: Irreversible Action — We are about to perform an action that cannot be undone (e.g., submitting a form, completing a purchase, sending a message, deleting data). If the user explicitly authorized the action (e.g., "submit the form", "complete the purchase", "you have my permission to submit") -> proceed without stopping. If the user did NOT explicitly authorize the action -> stop and ask for confirmation. (e.g., "fill out a form" with no mention of submitting -> fill the form, then ask before submitting; "fill out and submit a form" -> fill and submit without stopping.)\n\nOnly stop at a critical point if (1) required information is missing, (2) the task is ambiguous, OR (3) an irreversible action lacks explicit user authorization. If the user has provided all necessary information AND explicitly authorized the action, proceed without interruption.\n\nYou are provided with function signatures within <tools></tools> XML tags:\n<tools>\n{"type": "function", "function": {"name": "computer_use", "description": "Use a mouse and keyboard to interact with a computer, and take screenshots.\\n* This is an interface to a desktop GUI. You do not have access to a terminal or applications menu. You must click on desktop icons to start applications.\\n* Some applications may take time to start or process actions, so you may need to wait and take successive screenshots to see the results of your actions. E.g. if you click on Firefox and a window doesn\'t open, try wait and taking another screenshot.\\n* The screen\'s resolution is 1000x1000.\\n* Whenever you intend to move the cursor to click on an element like an icon, you should consult a screenshot to determine the coordinates of the element before moving the cursor.\\n* If you tried clicking on a program or link but it failed to load, even after waiting, try adjusting your cursor position so that the tip of the cursor visually falls on the element that you want to click.\\n* Make sure to click any buttons, links, icons, etc with the cursor tip in the center of the element. Don\'t click boxes on their edges.", "parameters": {"properties": {"action": {"description": "The action to perform. The available actions are:\\n* `key`: Performs key down presses on the arguments passed in order, then performs key releases in reverse order. Includes \\"Enter\\", \\"Alt\\", \\"Shift\\", \\"Tab\\", \\"Control\\", \\"Backspace\\", \\"Delete\\", \\"Escape\\", \\"ArrowUp\\", \\"ArrowDown\\", \\"ArrowLeft\\", \\"ArrowRight\\", \\"PageDown\\", \\"PageUp\\", \\"Shift\\", etc.\\n* `type`: Type a string of text on the keyboard.\\n* `mouse_move`: Move the cursor to a specified (x, y) pixel coordinate on the screen.\\n* `left_click`: Click the left mouse button.\\n* `double_click`: Double-click the left mouse button.\\n* `right_click`: Click the right mouse button.\\n* `triple_click`: Triple-click the left mouse button (e.g. to select a line of text).\\n* `left_click_drag`: Click and drag the cursor to a specified (x, y) pixel coordinate on the screen.\\n* `scroll`: Performs a scroll of the mouse scroll wheel.\\n* `hscroll`: Performs a horizontal scroll (mapped to regular scroll).\\n* `visit_url`: Visit a specified URL.\\n* `history_back`: Go back to the previous page in the browser history.\\n* `web_search`: Perform a web search with a specified query.\\n* `read_page_answer_question`: Read the current page content and answer a question about it.\\n* `pause_and_memorize_fact`: Pause and memorize a fact for future reference.\\n* `ask_user_question`: Ask the user a clarifying question and wait for a response.\\n* `wait`: Wait specified seconds for the change to happen.\\n* `terminate`: Terminate the current task and provide the final answer.", "enum": ["key", "type", "mouse_move", "left_click", "left_click_drag", "right_click", "double_click", "triple_click", "scroll", "hscroll", "visit_url", "history_back", "web_search", "read_page_answer_question", "pause_and_memorize_fact", "ask_user_question", "wait", "terminate"], "type": "string"}, "keys": {"description": "Required only by `action=key`.", "type": "array"}, "text": {"description": "Required only by `action=type`.", "type": "string"}, "coordinate": {"description": "(x, y): The x (pixels from the left edge) and y (pixels from the top edge) coordinates to move the mouse to. Required by `action=left_click`, `action=double_click`, `action=right_click`, `action=triple_click`, `action=left_click_drag`, and `action=mouse_move`.", "type": "array"}, "pixels": {"description": "The amount of scrolling to perform. Positive values scroll up, negative values scroll down. Required only by `action=scroll` and `action=hscroll`.", "type": "number"}, "url": {"description": "The URL to visit. Required only by `action=visit_url`.", "type": "string"}, "query": {"description": "The query to search for. Required only by `action=web_search`.", "type": "string"}, "fact": {"description": "The fact to remember for the future. Required only by `action=pause_and_memorize_fact`.", "type": "string"}, "question": {"description": "The question to ask. Required by `action=read_page_answer_question` and `action=ask_user_question`.", "type": "string"}, "time": {"description": "The seconds to wait. Required only by `action=wait`.", "type": "number"}, "answer": {"description": "The final answer for the task. Required only by `action=terminate`.", "type": "string"}}, "required": ["action"], "type": "object"}}}\n</tools>\n\nFor each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:\n<tool_call>\n{"name": <function-name>, "arguments": <args-json-object>}\n</tool_call>'
NEXT = 'Here is the next screenshot. Think about what to do next.'
EYE_STEPS, EYE_WAIT = 20, 25
ALIASES = {'arrowup': 'up', 'arrowdown': 'down', 'arrowleft': 'left', 'arrowright': 'right', 'super': 'win'}
SRCCOPY, CAPTUREBLT, HALFTONE = 0x00CC0020, 0x40000000, 4
MOVE, LEFT_DOWN, LEFT_UP, RIGHT_DOWN, RIGHT_UP, WHEEL, HWHEEL, ABSOLUTE = 0x0001, 0x0002, 0x0004, 0x0008, 0x0010, 0x0800, 0x1000, 0x8000
KEYUP, EXTENDED = 0x0002, 0x0001
EXTENDED_VKS = frozenset((0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E))
VK = {'enter': 0x0D, 'return': 0x0D, 'tab': 0x09, 'escape': 0x1B, 'esc': 0x1B, 'backspace': 0x08, 'delete': 0x2E, 'del': 0x2E, 'insert': 0x2D, 'home': 0x24, 'end': 0x23, 'pageup': 0x21, 'pagedown': 0x22, 'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27, 'ctrl': 0x11, 'control': 0x11, 'alt': 0x12, 'shift': 0x10, 'win': 0x5B, 'windows': 0x5B, 'space': 0x20} | {f'f{i}': 0x6F + i for i in range(1, 13)} | {c: ord(c.upper()) for c in 'abcdefghijklmnopqrstuvwxyz0123456789'}
MIND = '''You are the Trident organism. main.py starts you. organism.py is your body. story.txt is the plan: a short ordered list of subtasks. campaign.txt is the task list. ledger.md is memory. The run markdown is the record. The FARA section is the only desktop view. The sentence at the bottom is the only assignment.
On every chat, read FARA and replan. While her log says open, call --eye-file again with the same sentence; it waits for her and never restarts her. Edit organism.py only when this wake's record shows a defect in it. The running process keeps the loaded body. After terminated with verify YES, pass the next subtask sentence. After verify NO, repeat, steps, blank, or interrupted, she stopped without the result: pass a changed sentence. Only the current sentence goes to --eye-file. Do not open shots/, images, or the screen. Do not call --hands-file, --capture, or --region. Do not invent a point. Coordinates come only from her log.
A terminate answer is her claim; the verify line is her second look at a fresh screen. Do not --stop because she says the task is done. --stop only in the last 5 percent, after the story and one ledger lesson. A line count over 1000 is not a defect.
Write the story and this wake's ledger lesson whenever the plan changes. {budget} Do not claim a result without a record from this wake. Do not move a window the sentence leaves alone. Do not delete agent/, shots/, actions/, or streams/.
On 27 October 2026, other public products named Trident were a security console, a chat application, and a three-role review loop. They are not this process.'''
DOCS = (
'--eye-file PATH. Write the current subtask sentence. While the wake eye is live, wait up to 25 seconds for her to finish, then return her log. Otherwise Fara runs until she terminates or 8 actions.',
'--story-file PATH. Replace story.txt. The next prompt includes it.',
'--ledger-file PATH. Replace this wake lesson, 1-1200 characters.',
'--windows. List titled windows. A minimized window has box null.',
'--region NONE or LEFT,TOP,RIGHT,BOTTOM on a 0-1000 screen.',
'--place HWND LEFT TOP RIGHT BOTTOM. Move a window only when the sentence asks.',
'--learn. Count headings in earlier run markdown files.',
'--stop. Write the stop file. The mind gets up to 60 seconds, or less if the deadline is sooner.',
'--mind-file PATH. Run the sentence until a result, failure, stop, or deadline.',
'--map. Print the line count, the vision server, and the tool flags.',
)
class Fail(Exception):
    pass
def bind(name, table):
    dll = ctypes.WinDLL(name, use_last_error=True)
    for function, restype, *argtypes in table:
        getattr(dll, function).restype, getattr(dll, function).argtypes = restype, argtypes
    return dll
INT, PTR = W.INT, ctypes.POINTER
user32 = bind('user32', [('GetDC', W.HDC, W.HWND), ('ReleaseDC', INT, W.HWND, W.HDC), ('GetSystemMetrics', INT, INT), ('GetCursorPos', W.BOOL, PTR(W.POINT)), ('SendInput', ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, INT), ('VkKeyScanW', ctypes.c_short, W.WCHAR), ('EnumWindows', W.BOOL, ctypes.c_void_p, W.LPARAM), ('GetForegroundWindow', W.HWND), ('GetWindowTextW', INT, W.HWND, W.LPWSTR, INT), ('GetWindowTextLengthW', INT, W.HWND), ('IsWindowVisible', W.BOOL, W.HWND), ('IsIconic', W.BOOL, W.HWND), ('GetWindowRect', W.BOOL, W.HWND, PTR(W.RECT)), ('GetWindowThreadProcessId', W.DWORD, W.HWND, PTR(W.DWORD)), ('GetWindowLongW', W.LONG, W.HWND, INT), ('ShowWindow', W.BOOL, W.HWND, INT), ('SetWindowPos', W.BOOL, W.HWND, ctypes.c_void_p, INT, INT, INT, INT, W.UINT)])
gdi32 = bind('gdi32', [('CreateCompatibleDC', W.HDC, W.HDC), ('CreateDIBSection', W.HBITMAP, W.HDC, ctypes.c_void_p, W.UINT, PTR(ctypes.c_void_p), W.HANDLE, W.DWORD), ('SelectObject', W.HGDIOBJ, W.HDC, W.HGDIOBJ), ('DeleteObject', W.BOOL, W.HGDIOBJ), ('DeleteDC', W.BOOL, W.HDC), ('BitBlt', W.BOOL, W.HDC, INT, INT, INT, INT, W.HDC, INT, INT, W.DWORD), ('StretchBlt', W.BOOL, W.HDC, INT, INT, INT, INT, W.HDC, INT, INT, INT, INT, W.DWORD), ('SetStretchBltMode', INT, W.HDC, INT), ('SetBrushOrgEx', W.BOOL, W.HDC, INT, INT, ctypes.c_void_p)])
kernel32 = bind('kernel32', [('CreateJobObjectW', W.HANDLE, W.LPVOID, W.LPCWSTR), ('OpenJobObjectW', W.HANDLE, W.DWORD, W.BOOL, W.LPCWSTR), ('SetInformationJobObject', W.BOOL, W.HANDLE, INT, W.LPVOID, W.DWORD), ('AssignProcessToJobObject', W.BOOL, W.HANDLE, W.HANDLE), ('CloseHandle', W.BOOL, W.HANDLE), ('GetCurrentProcess', W.HANDLE)])
dwmapi = bind('dwmapi', [('DwmGetWindowAttribute', ctypes.c_long, W.HWND, W.DWORD, ctypes.c_void_p, W.DWORD)])
try:
    ctypes.WinDLL('shcore').SetProcessDpiAwareness(2)
except OSError:
    pass
class BitmapInfoHeader(ctypes.Structure):
    _fields_ = [('biSize', W.DWORD), ('biWidth', W.LONG), ('biHeight', W.LONG), ('biPlanes', W.WORD), ('biBitCount', W.WORD), ('biCompression', W.DWORD), ('biSizeImage', W.DWORD), ('biXPelsPerMeter', W.LONG), ('biYPelsPerMeter', W.LONG), ('biClrUsed', W.DWORD), ('biClrImportant', W.DWORD)]
class MouseInput(ctypes.Structure):
    _fields_ = [('dx', ctypes.c_long), ('dy', ctypes.c_long), ('mouseData', ctypes.c_uint), ('dwFlags', ctypes.c_uint), ('time', ctypes.c_uint), ('dwExtraInfo', ctypes.c_ulonglong)]
class KeyboardInput(ctypes.Structure):
    _fields_ = [('wVk', W.WORD), ('wScan', W.WORD), ('dwFlags', W.DWORD), ('time', W.DWORD), ('dwExtraInfo', ctypes.c_ulonglong)]
class InputData(ctypes.Union):
    _fields_ = [('mi', MouseInput), ('ki', KeyboardInput)]
class Input(ctypes.Structure):
    _anonymous_ = ('data',)
    _fields_ = [('type', ctypes.c_uint), ('data', InputData)]
class JobBasicLimits(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64), ('LimitFlags', W.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t), ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', W.DWORD), ('Affinity', ctypes.c_size_t), ('PriorityClass', W.DWORD), ('SchedulingClass', W.DWORD)]
class JobLimits(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', JobBasicLimits), ('IoInfo', ctypes.c_uint64 * 6), ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]
def boot(name, argv):
    handle = kernel32.OpenJobObjectW(0x0001, False, name)
    if not handle:
        raise RuntimeError('bootstrap could not open job; vendor was not launched')
    try:
        if not kernel32.AssignProcessToJobObject(handle, kernel32.GetCurrentProcess()):
            raise RuntimeError('bootstrap could not join job; vendor was not launched')
    finally:
        kernel32.CloseHandle(handle)
    return subprocess.call(argv, stdin=subprocess.DEVNULL, creationflags=0x08000000 if os.name == 'nt' else 0)
@contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, 'a+b')
    handle.seek(0, 2)
    if handle.tell() == 0:
        handle.write(b'\0')
        handle.flush()
    handle.seek(0)
    import msvcrt
    while True:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            break
        except OSError:
            time.sleep(.01)
    try:
        yield handle
    finally:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()
def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with open(temp, 'wb') as handle:
            handle.write(value if isinstance(value, bytes) else value.encode('utf-8'))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
def json_file(path, value):
    atomic(path, json.dumps(value, ensure_ascii=False, allow_nan=False))
def read_file(path):
    file = Path(path)
    if not file.is_file():
        raise Fail(f'not a file: {path}')
    return file.read_text(encoding='utf-8-sig')
def configure(work, run, minutes='', wake_start='', wake_end='', shot_delay='1.5', vision_url='http://192.168.16.31:1234', vision_model='retina', decide_model='typesafe/jev-1.14-20261014', mind_model='', coordinate_mode='normalized'):
    global RUN
    SET.clear()
    SET.update(work=str(Path(work).resolve()), run=run or time.strftime('%Y%m%d_%H%M%S'), minutes=str(minutes or ''), wake_start=str(wake_start or ''), wake_end=str(wake_end or ''), shot_delay=str(shot_delay or '1.5'), vision_url=str(vision_url).rstrip('/'), vision_model=vision_model, decide_model=decide_model, mind_model=mind_model or '', coordinate_mode=coordinate_mode)
    RUN = Path(SET['work']) / f"run_{SET['run']}.md"
def clock():
    if not SET.get('wake_start'):
        return {'clock': {'used_seconds': None, 'remaining_seconds': None, 'timing': 'unknown'}}
    now, start = time.time(), float(SET['wake_start'])
    body = {'now': time.strftime('%H:%M:%S'), 'used': f'{(now - start) / 60:.1f} min', 'used_seconds': round(now - start, 3), 'remaining_seconds': None}
    if SET.get('wake_end'):
        body['left'] = f'{(float(SET["wake_end"]) - now) / 60:.1f} min'
        body['remaining_seconds'] = round(float(SET['wake_end']) - now, 3)
        body['deadline'] = time.strftime('%H:%M:%S', time.localtime(float(SET['wake_end'])))
    return {'clock': body}
def command():
    bits = ["'" + str(PYTHON).replace("'", "''") + "'", "'" + str(CODE / 'main.py').replace("'", "''") + "'", '--work', "'" + SET['work'].replace("'", "''") + "'", '--run', "'" + str(SET['run']).replace("'", "''") + "'"]
    for key in ('minutes', 'wake_start', 'wake_end', 'shot_delay', 'vision_url', 'vision_model', 'decide_model', 'mind_model', 'coordinate_mode'):
        if SET.get(key):
            bits += ['--' + key.replace('_', '-'), "'" + str(SET[key]).replace("'", "''") + "'"]
    return '& ' + ' '.join(bits)
def journal_ready():
    try:
        with socket.create_connection(('127.0.0.1', PORT), 0.2):
            pass
    except OSError:
        return False
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/identity', timeout=.5) as response:
            return json.load(response).get('service') == 'trident-journal'
    except (OSError, ValueError):
        return None
def point_journal():
    atomic(CODE / 'journal.path', str(RUN))
    legacy = CODE / 'v2' / 'journal.path'
    if legacy.exists():
        atomic(legacy, str(RUN))
def ensure_journal():
    if SET.get('dry') or os.environ.get('TRIDENT_JOURNAL') == '1':
        return
    state = journal_ready()
    if state is True:
        return
    if state is None:
        raise Fail(f'port {PORT} belongs to an unrelated listener')
    subprocess.Popen([str(PYTHON), '-B', str(CODE / 'main.py'), '--journal', '--work', SET['work'], '--run', SET['run']], cwd=SET['work'], env=os.environ | {'TRIDENT_JOURNAL': '1'}, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
def heading(kind, text=''):
    if SET.get('dry') or RUN is None:
        return
    if os.environ.get('TRIDENT_JOURNAL') != '1':
        try:
            point_journal()
            ensure_journal()
        except (OSError, Fail) as error:
            print(str(error), file=sys.stderr)
    body = '' if text is None else str(text)
    block = f"## {time.strftime('%H:%M:%S')} {kind}\n\n{body}\n\n" if body else f"## {time.strftime('%H:%M:%S')} {kind}\n\n"
    RUN.parent.mkdir(parents=True, exist_ok=True)
    with locked(RUN.with_suffix('.lock')):
        with open(RUN, 'a', encoding='utf-8') as handle:
            handle.write(block)
def brief_result(kind, body):
    if kind == 'windows':
        rows = body.get('windows') or []
        return f"count={len(rows)} " + '; '.join(f"{row.get('hwnd')}|{row.get('title')}|{row.get('box')}" for row in rows)
    if kind in ('capture', 'hands'):
        return f"id={body.get('id')} size={body.get('size')} cursor={body.get('cursor')}"
    if kind == 'eye':
        return body.get('log') or f"action={body.get('action')} coordinate={body.get('coordinate')} program={body.get('program')} answer={body.get('answer')}"
    if kind == 'map':
        return f"organism_lines={body.get('organism_lines')} total_python_lines={body.get('total_python_lines')} under_1000={body.get('under_1000')} vision={body.get('vision')}"
    return json.dumps({key: value for key, value in body.items() if key != 'clock'}, ensure_ascii=False)
def emit(kind, payload):
    body = dict(payload)
    body.update(clock())
    line = brief_result(kind, body)
    heading(kind, line)
    print(line, flush=True)
    return body
def serve_journal():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    page = b'<!DOCTYPE html><meta charset=utf-8><title>trident</title><body style="background:#000;color:#fff;margin:0"><pre id=t style="white-space:pre-wrap;margin:0;font:14px monospace"></pre><script>var t=document.getElementById("t"),es=new EventSource("/events");function z(){scrollTo(0,1e9)}es.addEventListener("reset",function(e){t.textContent=e.data;z()});es.addEventListener("more",function(e){t.textContent+=e.data;z()})</script>'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): return
        def _send(self, code, ctype, body=b'', stream=False):
            self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Cache-Control', 'no-store'); self.end_headers()
            if body and not stream: self.wfile.write(body)
        def _event(self, name, payload):
            self.wfile.write(('event: ' + name + '\n' + ''.join(f'data: {line}\n' for line in payload.decode('utf-8', 'replace').split('\n')) + '\n').encode()); self.wfile.flush()
        def _target(self):
            pointer = CODE / 'journal.path'
            text = pointer.read_text(encoding='utf-8').strip() if pointer.exists() else ''
            return Path(text) if text else RUN
        def do_GET(self):
            path = self.path.split('?', 1)[0]
            if path == '/identity':
                self._send(200, 'application/json', json.dumps({'service': 'trident-journal', 'code': str(CODE)}).encode())
            elif path == '/md':
                target = self._target()
                self._send(200, 'text/plain; charset=utf-8', target.read_bytes() if target and target.exists() else b'')
            elif path == '/events':
                self._send(200, 'text/event-stream; charset=utf-8', stream=True)
                seen = current = None
                try:
                    while True:
                        target = self._target()
                        data = target.read_bytes() if target and target.exists() else b''
                        key = str(target)
                        if key != current or seen is None or len(data) < seen:
                            current, seen = key, len(data); self._event('reset', data)
                        elif len(data) > seen:
                            self._event('more', data[seen:]); seen = len(data)
                        time.sleep(0.4)
                except OSError:
                    return
            elif path in ('/', '/index.html'):
                self._send(200, 'text/html; charset=utf-8', page)
            else:
                self.send_error(404)
    ThreadingHTTPServer.allow_reuse_address = True
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
def screen_size():
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
def deliver(packet):
    if user32.SendInput(1, ctypes.byref(packet), ctypes.sizeof(packet)) != 1:
        raise Fail(f'SendInput did not deliver input (Win32 error {ctypes.get_last_error()})')
def send(flags, px=0, py=0, data=0, absolute=False):
    width, height = screen_size()
    packet = Input()
    packet.type = 0
    packet.mi = MouseInput(int(px * 65535 / max(1, width - 1)) if absolute else 0, int(py * 65535 / max(1, height - 1)) if absolute else 0, int(data) & 0xFFFFFFFF, flags | (ABSOLUTE if absolute else 0))
    deliver(packet)
def swap_red_blue(pixels):
    out = bytearray(pixels)
    count = len(pixels) // 4
    out[0::4], out[2::4], out[3::4] = pixels[2::4], pixels[0::4], b'\xff' * count
    return bytes(out)
def png(image):
    pixels, width, height = image
    rgba, stride = swap_red_blue(pixels), width * 4
    rows = b''.join(b'\0' + rgba[y * stride:(y + 1) * stride] for y in range(height))
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows, 6)) + chunk(b'IEND', b'')
def unpng(data):
    (width, height), (size,) = struct.unpack('>II', data[16:24]), struct.unpack('>I', data[33:37])
    rows, stride = zlib.decompress(data[41:41 + size]), width * 4 + 1
    return swap_red_blue(b''.join(rows[y * stride + 1:(y + 1) * stride] for y in range(height))), width, height
class Desk:
    def __init__(self, work):
        self.work = Path(work)
    def region(self):
        path = self.work / 'region.txt'
        return (path.read_text(encoding='utf-8').strip() if path.exists() else 'NONE') or 'NONE'
    def _box(self):
        if getattr(self, 'action_box', None) is not None:
            return tuple(self.action_box)
        raw, (width, height) = self.region(), screen_size()
        box = (0, 0, NORM, NORM) if raw == 'NONE' else tuple(int(part) for part in raw.split(','))
        return [(v * (height if i % 2 else width) + NORM // 2) // NORM for i, v in enumerate(box)]
    def point(self, x, y):
        x1, y1, x2, y2 = self._box()
        return x1 + round(max(0, min(NORM, float(x))) * (x2 - x1 - 1) / NORM), y1 + round(max(0, min(NORM, float(y))) * (y2 - y1 - 1) / NORM)
    def cursor_pixel(self):
        at = W.POINT()
        if not user32.GetCursorPos(ctypes.byref(at)): raise Fail('cannot read the cursor')
        return [at.x, at.y]
    def move(self, x, y):
        send(MOVE, *self.point(x, y), absolute=True); time.sleep(0.03)
    def click(self, x, y, right=False, times=1):
        px, py = self.point(x, y)
        send(MOVE, px, py, absolute=True)
        time.sleep(0.03)
        down, up = (RIGHT_DOWN, RIGHT_UP) if right else (LEFT_DOWN, LEFT_UP)
        for _ in range(max(1, times)):
            try:
                send(down, px, py, absolute=True)
                time.sleep(0.03)
            finally:
                send(up, px, py, absolute=True)
            time.sleep(0.05 if times > 1 else 0)
    def drag(self, points):
        if len(points) < 2:
            raise Fail('drag needs two points')
        pixels = [self.point(x, y) for x, y in points]
        send(LEFT_UP)
        send(MOVE, *pixels[0], absolute=True)
        time.sleep(0.03)
        try:
            send(LEFT_DOWN, *pixels[0], absolute=True)
            time.sleep(0.03)
            for (x0, y0), (x1, y1) in zip(pixels, pixels[1:]):
                for step in range(1, 13):
                    send(MOVE, x0 + (x1 - x0) * step // 12, y0 + (y1 - y0) * step // 12, absolute=True)
                    time.sleep(0.012)
        finally:
            send(LEFT_UP)
    def wheel(self, amount, horizontal=False):
        if amount: send(HWHEEL if horizontal else WHEEL, data=int(amount))
    def _key(self, vk, up=False):
        packet = Input()
        packet.type = 1
        packet.ki = KeyboardInput(vk, 0, (KEYUP if up else 0) | (EXTENDED if vk in EXTENDED_VKS else 0), 0, 0)
        deliver(packet)
    def key(self, combo):
        names = [part.strip().lower() for part in str(combo).replace(',', '+').replace(' ', '+').split('+') if part.strip()]
        if not names:
            raise Fail(f'no key in {combo!r}')
        codes = []
        for name in names:
            if name in VK:
                codes.append(VK[name])
            elif len(name) == 1:
                code = user32.VkKeyScanW(name)
                if code == -1:
                    raise Fail(f'unmappable character: {name!r}')
                codes.append(code & 0xFF)
            else:
                raise Fail(f'unknown key: {name}')
        held = []
        try:
            for vk in codes:
                held.append(vk)
                self._key(vk)
                time.sleep(0.02)
        finally:
            for vk in reversed(held):
                self._key(vk, up=True)
    def type_text(self, value):
        subprocess.run(['powershell.exe', '-NoProfile', '-Command', 'Set-Clipboard -Value ([Console]::In.ReadToEnd())'], input=str(value).encode(), capture_output=True, check=True, creationflags=0x08000000)
        self.key('ctrl+v')
    def grab(self):
        width, height = screen_size()
        screen = user32.GetDC(0)
        try:
            header, bits = BitmapInfoHeader(ctypes.sizeof(BitmapInfoHeader), width, -height, 1, 32), ctypes.c_void_p()
            bitmap, dc = gdi32.CreateDIBSection(screen, ctypes.byref(header), 0, ctypes.byref(bits), None, 0), gdi32.CreateCompatibleDC(screen)
            try:
                gdi32.SelectObject(dc, bitmap)
                if not gdi32.BitBlt(dc, 0, 0, width, height, screen, 0, 0, SRCCOPY | CAPTUREBLT):
                    raise Fail('capture failed')
                return ctypes.string_at(bits, width * height * 4), width, height
            finally:
                gdi32.DeleteDC(dc)
                gdi32.DeleteObject(bitmap)
        finally:
            user32.ReleaseDC(0, screen)
    def stretch(self, image, box, width, height):
        pixels, source_width, source_height = image
        x1, y1, x2, y2 = box
        screen = user32.GetDC(0)
        try:
            source_header, source_bits = BitmapInfoHeader(ctypes.sizeof(BitmapInfoHeader), source_width, -source_height, 1, 32), ctypes.c_void_p()
            source_bitmap, source = gdi32.CreateDIBSection(screen, ctypes.byref(source_header), 0, ctypes.byref(source_bits), None, 0), gdi32.CreateCompatibleDC(screen)
            header, bits = BitmapInfoHeader(ctypes.sizeof(BitmapInfoHeader), width, -height, 1, 32), ctypes.c_void_p()
            bitmap, dc = gdi32.CreateDIBSection(screen, ctypes.byref(header), 0, ctypes.byref(bits), None, 0), gdi32.CreateCompatibleDC(screen)
            try:
                gdi32.SelectObject(source, source_bitmap)
                ctypes.memmove(source_bits, pixels, len(pixels))
                gdi32.SelectObject(dc, bitmap)
                gdi32.SetStretchBltMode(dc, HALFTONE)
                gdi32.SetBrushOrgEx(dc, 0, 0, None)
                gdi32.StretchBlt(dc, 0, 0, width, height, source, x1, y1, max(1, x2 - x1), max(1, y2 - y1), SRCCOPY)
                return ctypes.string_at(bits, width * height * 4), width, height
            finally:
                gdi32.DeleteDC(dc)
                gdi32.DeleteObject(bitmap)
                gdi32.DeleteDC(source)
                gdi32.DeleteObject(source_bitmap)
        finally:
            user32.ReleaseDC(0, screen)
    def windows(self):
        found, front, (width, height) = [], user32.GetForegroundWindow(), screen_size()
        @ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        def visit(hwnd, _):
            cloaked, length = W.DWORD(), user32.GetWindowTextLengthW(hwnd)
            dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            if user32.IsWindowVisible(hwnd) and not cloaked.value and length:
                title, rect, pid = ctypes.create_unicode_buffer(length + 1), W.RECT(), W.DWORD()
                user32.GetWindowTextW(hwnd, title, length + 1)
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                minimized = bool(user32.IsIconic(hwnd))
                box = None if minimized else [rect.left * NORM // width, rect.top * NORM // height, rect.right * NORM // width, rect.bottom * NORM // height]
                found.append({'hwnd': int(hwnd), 'title': title.value, 'box': box, 'pixel_box': None if minimized else [rect.left, rect.top, rect.right, rect.bottom], 'pid': int(pid.value), 'minimized': minimized, 'topmost': bool(user32.GetWindowLongW(hwnd, -20) & 8), 'foreground': hwnd == front})
            return True
        user32.EnumWindows(visit, 0)
        return found
    def capture(self):
        with locked(self.work / 'capture.lock'):
            region, size, windows = self.region(), list(screen_size()), self.windows()
            x1, y1, x2, y2 = self._box()
            image = self.stretch(self.grab(), (x1, y1, x2, y2), max(1, x2 - x1), max(1, y2 - y1))
            if region != self.region() or size != list(screen_size()) or windows != self.windows():
                raise Fail('desktop geometry changed during capture')
            shots = self.work / 'shots'
            shots.mkdir(exist_ok=True)
            sid = time.strftime('%H%M%S') + '-' + uuid.uuid4().hex
            data = png(image)
            atomic(shots / f'{sid}.png', data)
            body = {'id': sid, 'image': str(shots / f'{sid}.png'), 'size': list(image[1:]), 'region': region, 'box': [x1, y1, x2, y2], 'screen_size': size, 'windows': windows, 'cursor_pixel': self.cursor_pixel(), 'at': time.time(), 'sha256': hashlib.sha256(data).hexdigest()}
            body['cursor'] = [(value - start) * NORM / max(1, length - 1) for value, start, length in zip(body['cursor_pixel'], (x1, y1), image[1:])]
            zero = bytes(len(image[0]) // 4)
            if image[0][0::4] == zero and image[0][1::4] == zero and image[0][2::4] == zero:
                body['blank'] = True
            json_file(shots / f'{sid}.json', body)
            json_file(self.work / 'screen.json', body)
            return body
    def place(self, hwnd, left, top, right, bottom):
        width, height = screen_size()
        left, top, right, bottom = (int(float(v)) for v in (left, top, right, bottom))
        if not (0 <= left < right <= NORM and 0 <= top < bottom <= NORM):
            raise Fail('box is left top right bottom on 0-1000')
        user32.ShowWindow(int(hwnd), 9)
        return {'hwnd': int(hwnd), 'box': [left, top, right, bottom], 'moved': bool(user32.SetWindowPos(int(hwnd), ctypes.c_void_p(-2), left * width // NORM, top * height // NORM, max((right - left) * width // NORM, 1), max((bottom - top) * height // NORM, 1), 0x0040))}
    def open_job(self, name=None):
        job, limits = kernel32.CreateJobObjectW(None, name), JobLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000
        if not (job and kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))):
            self.close_job(job)
            raise Fail('the wake could not open a job')
        return job
    def close_job(self, job):
        if job: kernel32.CloseHandle(job)
def fit_size(width, height):
    if min(width, height) <= 0 or max(width, height) / min(width, height) > 200:
        raise Fail('the capture has a bad shape')
    scale = min(1.0, 1440 / max(width, height))
    return max(32, round(width * scale / 32) * 32), max(32, round(height * scale / 32) * 32)
def system_text(width, height):
    return FARA_SYSTEM if SET.get('coordinate_mode', 'normalized') == 'normalized' else FARA_SYSTEM.replace('1000x1000', f'{width}x{height}')
def validate(arguments, width=1000, height=1000):
    action = arguments.get('action')
    if action in FARA_ONLY:
        return arguments
    if action not in ACTIONS:
        raise Fail(f'unsupported eye action: {action!r}')
    required = ('coordinate',) if action in POINTER else {'key': ('keys',), 'type': ('text',), 'scroll': ('pixels',), 'hscroll': ('pixels',), 'wait': ('time',), 'terminate': ('answer',)}[action]
    if not all(key in arguments for key in required):
        raise Fail(f'{action} has the wrong arguments')
    if action in POINTER:
        xy = arguments['coordinate']
        if not isinstance(xy, list) or len(xy) != 2 or not all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= bound for v, bound in zip(xy, (width, height))):
            raise Fail('the eye returned a point outside the coordinate space')
    elif action == 'key':
        keys = arguments['keys']
        if not isinstance(keys, list) or not keys or not all(isinstance(key, str) and ALIASES.get(key.lower(), key.lower()) in VK for key in keys):
            raise Fail('unsupported key combination')
    elif action in ('type', 'terminate') and (not isinstance(arguments[required[0]], str) or (action == 'terminate' and not arguments['answer'].strip())):
        raise Fail(f'{action} requires text')
    elif action in ('scroll', 'hscroll') and (type(arguments['pixels']) not in (int, float) or abs(arguments['pixels']) > 10000):
        raise Fail('scroll is too large')
    elif action == 'wait' and not (type(arguments['time']) in (int, float) and 0 <= arguments['time'] <= 60):
        raise Fail('wait must be 0-60 seconds')
    return arguments
def parse_call(raw, width=1000, height=1000):
    matches = list(re.finditer(r'<tool_call>(.*?)</tool_call>', raw, re.S))
    if len(matches) != 1:
        raise Fail('the eye must return exactly one computer_use call')
    try:
        call = json.loads(matches[0][1], object_pairs_hook=lambda pairs: dict(pairs) if len(pairs) == len({key for key, _ in pairs}) else (_ for _ in ()).throw(ValueError('duplicate')))
    except ValueError as error:
        raise Fail('the eye returned invalid JSON') from error
    if not isinstance(call, dict) or call.get('name') != 'computer_use' or not isinstance(call.get('arguments'), dict):
        raise Fail('the eye must call computer_use')
    return validate(call['arguments'], width, height)
def load_capture(work):
    with locked(Path(work) / 'capture.lock'):
        path = Path(work) / 'screen.json'
        if not path.exists():
            raise Fail('there is no capture yet')
        screen = json.loads(path.read_text(encoding='utf-8-sig'))
        data = Path(screen['image']).read_bytes()
        image = unpng(data)
        digest = hashlib.sha256(data).hexdigest()
        if list(image[1:]) != screen.get('size') or (screen.get('sha256') and screen['sha256'] != digest):
            raise Fail('capture does not match its metadata')
        return screen | {'sha256': digest}, image
def ask(app, image, screen, turns):
    width, height = fit_size(*image[1:])
    kept = [turn for turn in turns if turn.get('shot')][-3:]
    messages = [{'role': 'system', 'content': system_text(width, height)}]
    for index, turn in enumerate(turns):
        if turn['role'] == 'assistant':
            messages.append({'role': 'assistant', 'content': turn.get('text') or ''}); continue
        raw = image if turn.get('shot') == screen.get('id') else (unpng((app.work / 'shots' / (turn['shot'] + '.png')).read_bytes()) if turn in kept else None)
        fit = None if raw is None else raw if raw[1:] == (width, height) else app.desk.stretch(raw, (0, 0, raw[1], raw[2]), width, height)
        bits = [] if fit is None else [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(png(fit)).decode()}}]
        said = turn.get('text') or ''
        said = said if index == 0 else (said + '\n\n' if said and said != 'observe' else '') + NEXT
        messages.append({'role': 'user', 'content': bits + [{'type': 'text', 'text': said}]})
    payload = {'model': SET['vision_model'], 'temperature': 0, 'max_tokens': 4096, 'messages': messages}
    request = urllib.request.Request(SET['vision_url'] + '/v1/chat/completions', json.dumps(payload).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=180) as response:
        body = json.load(response)
    reply = body['choices'][0]['message']
    content = (reply.get('content') or '').strip()
    if not content: raise Fail('the eye returned no content')
    mode = SET.get('coordinate_mode', 'normalized')
    try:
        arguments = parse_call(content, *((width - 1, height - 1) if mode == 'pixels' else (NORM, NORM)))
    except Fail as error:
        error.raw = content; raise
    point = arguments.get('coordinate')
    if point is not None and mode == 'pixels':
        point = [point[0] * NORM / max(1, width - 1), point[1] * NORM / max(1, height - 1)]
    scroll = None
    if arguments['action'] in ('scroll', 'hscroll'):
        axis = 0 if arguments['action'] == 'hscroll' else 1
        scroll = arguments['pixels'] * image[axis + 1] / (NORM if mode == 'normalized' else (width, height)[axis])
    return {'goal': turns[-1].get('text'), 'capture': screen['id'], 'action': arguments['action'], 'arguments': arguments, 'coordinate': point, 'answer': arguments.get('answer', ''), 'scroll_distance_pixels': scroll, 'reported_model': body.get('model'), 'program': None, 'thought': ' '.join(part for part in ((reply.get('reasoning_content') or content.split('</think>')[0].split('<think>')[-1] * ('</think>' in content)).strip()[-600:], content.split('<tool_call>', 1)[0].split('</think>')[-1].strip()) if part), 'raw': content}
def point_pixel(point, size):
    return [round(value * (length - 1) / NORM) for value, length in zip(point, size)]
def patch_of(image, point):
    pixels, width, height = image
    x, y = point_pixel(point, (width, height))
    box = [max(0, x - 2), max(0, y - 2), min(width, x + 3), min(height, y + 3)]
    sample = b''.join(pixels[(row * width + box[0]) * 4:(row * width + box[2]) * 4] for row in range(box[1], box[3]))
    return {'box': box, 'bgra': sample.hex()}
def target_window(screen, point):
    x, y = point_pixel(point, screen['size'])
    left, top = screen['box'][:2]
    for window in screen['windows']:
        box = window.get('pixel_box')
        if box and box[0] <= x + left < box[2] and box[1] <= y + top < box[3]:
            return {key: window[key] for key in ('hwnd', 'pid', 'pixel_box')}
    return None
def foreground_of(screen):
    return next(({key: window[key] for key in ('hwnd', 'pid', 'pixel_box')} for window in screen['windows'] if window.get('foreground')), None)
def action_path(work, action_id):
    if not re.fullmatch(r'[0-9a-f]{32}', str(action_id)):
        raise Fail('invalid action id')
    return Path(work) / 'actions' / (action_id + '.json')
def store_action(app, screen, image, seen):
    point = seen.get('coordinate') if seen.get('coordinate') is not None else screen['cursor']
    if not all(0 <= value <= NORM for value in point):
        raise Fail('the point lies outside the capture')
    action_id = uuid.uuid4().hex
    record = {'id': action_id, 'run': SET['run'], 'created': time.time(), 'state': 'ready', 'screen': screen, 'result': seen, 'point': point, 'patch': patch_of(image, point), 'target_window': target_window(screen, point), 'foreground': foreground_of(screen)}
    if seen['action'] == 'left_click_drag':
        record['start_patch'] = patch_of(image, screen['cursor'])
    json_file(action_path(app.work, action_id), record)
    return {'action_id': action_id, 'program': 'fara ' + action_id}
def execute(desk, action_id):
    with locked(desk.work / 'hand.lock'):
        path = action_path(desk.work, action_id)
        if not path.exists():
            raise Fail('unknown action')
        record = json.loads(path.read_text(encoding='utf-8'))
        if record.get('state') != 'ready' or record.get('run') != SET['run']:
            raise Fail('stale or consumed action')
        if (desk.work / 'stop').exists() or (SET.get('wake_end') and time.time() >= float(SET['wake_end'])):
            raise Fail('wake stopped or deadline reached')
        if desk.capture().get('blank'): raise Fail('blank capture')
        fresh, image = load_capture(desk.work)
        original, result = record['screen'], record['result']
        args, action = result['arguments'], result['action']
        if any(fresh.get(key) != original.get(key) for key in ('region', 'box', 'size', 'screen_size')):
            raise Fail('geometry changed')
        if target_window(fresh, record['point']) != record.get('target_window') or patch_of(image, record['point']) != record.get('patch'):
            raise Fail('target changed')
        if action in ('key', 'type', 'scroll', 'hscroll') and foreground_of(fresh) != record.get('foreground'):
            raise Fail('foreground window changed')
        if action in ('left_click_drag', 'scroll', 'hscroll') and fresh.get('cursor_pixel') != original.get('cursor_pixel'):
            raise Fail('cursor moved')
        if action == 'left_click_drag' and patch_of(image, original['cursor']) != record.get('start_patch'):
            raise Fail('drag start changed')
        record.update(state='consumed', consumed_at=time.time(), fresh_capture=fresh['id'])
        json_file(path, record)
        desk.action_box = original['box']
        try:
            point = result.get('coordinate')
            if action in ('left_click', 'right_click', 'double_click', 'triple_click'):
                desk.click(*point, right=action == 'right_click', times={'double_click': 2, 'triple_click': 3}.get(action, 1))
            elif action == 'mouse_move':
                desk.move(*point)
            elif action == 'left_click_drag':
                desk.drag([original['cursor'], point])
            elif action == 'key':
                desk.key('+'.join(ALIASES.get(key.lower(), key.lower()) for key in args['keys']))
            elif action == 'type':
                desk.type_text(args['text'])
            elif action in ('scroll', 'hscroll'):
                desk.wheel(round(float(result['scroll_distance_pixels']) * 120 / 40), horizontal=action == 'hscroll')
            elif action == 'wait':
                time.sleep(float(args['time']))
            else:
                raise Fail('unsupported action')
        finally:
            if hasattr(desk, 'action_box'):
                del desk.action_box
def eye_log(data):
    rows = [f"action={t.get('action')} coordinate={t.get('coordinate')}{' input=' + json.dumps(t['input']) if t.get('input') else ''} answer={t.get('answer')} thought={t.get('thought')}" for t in (data.get('turns') or []) if t.get('role') == 'assistant']
    state = f"open, no new turn for {int(time.time() - max(t.get('t', 0) for t in data['turns']))} s" if data.get('open') and data.get('turns') and data['turns'][-1].get('t') else 'open' if data.get('open') else data.get('stop') or ('terminated' if rows else 'none this wake')
    return state + '\n' + '\n'.join(rows) + (f"\nverify={data['verify']}" if data.get('verify') else '')
def eye_verify(app, text, claim):
    shot = app.desk.capture()
    if shot.get('blank'): return 'NO blank capture'
    screen, image = load_capture(app.work)
    ask_text = f'Do not act. Only look at this screenshot. The task was: "{text}". The claim was: "{claim}". Is the task done on the screen now? Terminate with an answer that starts with YES or NO, then say what you see.'
    try: seen = ask(app, image, screen, [{'role': 'user', 'text': ask_text, 'shot': shot['id']}])
    except Fail as error: return f'NO verdict: {error}'
    return seen['answer'] if seen['action'] == 'terminate' else f"NO verdict: she chose {seen['action']} instead of looking"
def eye_halted(app, end):
    return (app.work / 'stop').exists() or bool(end and time.time() >= end)
def eye_steps(app, text, end, limit, fresh):
    file = app.work / 'fara_traj.json'
    if fresh: data = {'open': True, 'turns': [], 'run': SET['run']}
    else:
        data = json.loads(file.read_text(encoding='utf-8')) if file.exists() else {'open': False, 'turns': []}
        data = data if data.get('open') else {'open': True, 'turns': [], 'run': SET['run']}
    def put(role, **fields):
        data['turns'].append({'role': role, 't': time.time(), **fields}); json_file(file, data)
    put('user', text=text)
    steps, live, limit = 0, limit is None, limit or EYE_STEPS
    def stop(reason):
        data['open'], data['stop'] = False, reason; json_file(file, data); heading('eye', 'stop=' + reason); return reason
    while not eye_halted(app, end) and steps < limit:
        if live and not (app.work / 'eye.live').exists(): break
        steps += 1
        last = data['turns'][-1]
        if last.get('role') != 'user' or not last.get('shot'):
            shot = app.desk.capture()
            if shot.get('blank'):
                put('user', text='blank capture', shot=shot.get('id')); return stop('blank')
            last.update(shot=shot['id']) if last.get('role') == 'user' else data['turns'].append({'role': 'user', 'text': 'observe', 'shot': shot['id']})
            json_file(file, data)
        screen, image = load_capture(app.work)
        try: seen = ask(app, image, screen, data['turns'])
        except OSError: return stop('interrupted')
        except Fail as error:
            raw = str(getattr(error, 'raw', None) or error)
            put('assistant', text=raw, action='unparsed', coordinate=None, answer=raw, thought=raw)
            heading('eye', 'action=unparsed coordinate= answer=' + raw)
            put('user', text=str(error)); continue
        raw, coord, thought = seen.pop('raw'), seen.get('coordinate'), str(seen.get('thought') or '')
        heading('eye', f"action={seen['action']} coordinate={coord} answer={seen.get('answer')} thought={thought}")
        put('assistant', text=raw, thought=thought, action=seen['action'], coordinate=coord, input=seen['arguments'].get('text') or seen['arguments'].get('keys'), answer=seen.get('answer') or '')
        if seen['action'] in ('terminate', 'ask_user_question'):
            claim = seen.get('answer') or 'Question: ' + str(seen['arguments'].get('question') or '')
            data['verify'] = eye_verify(app, text, claim) if seen['action'] == 'terminate' else 'NO she asked a question'
            heading('eye', 'verify=' + data['verify'])
            data['open'] = False; json_file(file, data); return 'terminated'
        if live and not (app.work / 'eye.live').exists(): break
        if seen['action'] in FARA_ONLY:
            put('user', text='Done. Fact kept.' if seen['action'] == 'pause_and_memorize_fact' else f"Not done. {seen['action']} is not available. This is a Windows desktop, not a browser."); continue
        done = app.__dict__.setdefault('done', [])
        key = (seen['action'], str(coord), str(seen['arguments'].get('text') or seen['arguments'].get('keys') or ''), screen.get('sha256'))
        repeats = done.count(key)
        done.append(key)
        if repeats >= 2: return stop('repeat')
        if repeats:
            put('user', text=f"Not done. {seen['action']} at {coord} already happened. Choose another action or another coordinate."); continue
        try:
            seen['goal'] = text
            execute(app.desk, store_action(app, screen, image, seen)['program'].split()[-1])
            time.sleep(float(SET.get('shot_delay') or 1.5))
            put('user', text=f"Done. action={seen['action']} coordinate={coord}")
        except Fail as error:
            if 'blank capture' in str(error): return stop('blank')
            halted = 'stopped' in str(error) or 'deadline' in str(error)
            put('user', text=f'Not done. {error}', **({} if halted else {'shot': app.desk.capture().get('id')}))
            if halted: break
    return stop('steps') if steps >= limit and data.get('open') and not eye_halted(app, end) else 'halted'
def eye_data(app):
    file = app.work / 'fara_traj.json'
    return json.loads(file.read_text(encoding='utf-8')) if file.exists() else {'open': False, 'turns': []}
def eye_watch(app, end):
    path, finished, born = app.work / 'eye_now.txt', None, time.time()
    while (app.work / 'eye.live').exists() and not eye_halted(app, end):
        try: stamp, sentence = path.stat().st_mtime, path.read_text(encoding='utf-8').strip()
        except OSError: stamp, sentence = 0, ''
        if not sentence or stamp < born or sentence == finished:
            time.sleep(0.4); continue
        eye_steps(app, sentence, end, None, True); finished = sentence
def eye_run(app, path, dry=False):
    text = read_file(path).strip()
    if not text: raise Fail('the eye file is empty')
    if dry: return {'log': 'dry'}
    atomic(app.work / 'eye_now.txt', text)
    if (app.work / 'eye.live').exists():
        deadline = time.time() + EYE_WAIT
        while time.time() < deadline:
            data = eye_data(app)
            turns = data.get('turns') or []
            if not data.get('open') and turns and turns[0].get('text') == text: break
            time.sleep(0.5)
        return {'log': eye_log(eye_data(app))}
    end = float(SET['wake_end']) if SET.get('wake_end') else None
    eye_steps(app, text, end, 8, False)
    return {'log': eye_log(eye_data(app))}
def windows_run(app, _arg, dry=False): return {'windows': app.desk.windows()}
def region_run(app, value, dry=False):
    value = str(value or 'NONE').strip()
    nums = [int(part) for part in value.split(',')] if value.count(',') == 3 else []
    if value != 'NONE' and not (len(nums) == 4 and 0 <= nums[0] < nums[2] <= 1000 and 0 <= nums[1] < nums[3] <= 1000):
        raise Fail('region is NONE or left,top,right,bottom on 0-1000')
    with locked(app.work / 'capture.lock'):
        atomic(app.work / 'region.txt', value)
    return {'region': value}
def place_run(app, values, dry=False):
    return app.desk.place(*values)
def measured(path):
    path = Path(path)
    text = path.read_text(encoding='utf-8', errors='replace') if path.exists() else ''
    heads = list(HEAD.finditer(text))
    components, first = {}, None
    for head in heads:
        kind = head.group(2)
        if kind in COMPONENTS:
            components[kind] = components.get(kind, 0) + 1
            first = first or head.group(1)
    errors = sum(head.group(2) == 'err' for head in heads)
    return {'run': path.stem.removeprefix('run_'), 'source': str(path), 'first_event': heads[0].group(1) if heads else None, 'last_event': heads[-1].group(1) if heads else None, 'first_component': first, 'component_actions': components, 'recorded_errors': errors, 'harness_count_basis': 'markdown headings'}
def journals(work, run):
    work = Path(work)
    paths = sorted(set(work.glob('run_*.md')) | {work / f'run_{run}.md'})
    rows = [measured(path) for path in paths]
    return next(row for row in rows if row['run'] == run), [row for row in rows if row['run'] != run]
def learned_line(rows, ledger):
    calls = sum(sum(row['component_actions'].values()) for row in rows)
    errors = sum(row.get('recorded_errors') or 0 for row in rows)
    advice = 'Compare the newest ledger line with the run markdown.' if str(ledger or '').strip() else 'There is no ledger lesson yet.'
    names = ', '.join(row['run'] for row in rows)
    return f'{len(rows)} earlier wakes ({names}), {errors} recorded errors, {calls} component actions. {advice}'
def snapshot(work):
    work = Path(work)
    with locked(work / 'memory.lock'):
        story = (work / 'story.txt').read_text(encoding='utf-8') if (work / 'story.txt').exists() else ''
        ledger = (work / 'ledger.md').read_text(encoding='utf-8') if (work / 'ledger.md').exists() else ''
        try:
            meta = json.loads((work / 'story.meta.json').read_text(encoding='utf-8')) if (work / 'story.meta.json').exists() else {}
        except (ValueError, TypeError):
            meta = {}
        if meta.get('sha256') != hashlib.sha256(story.encode()).hexdigest():
            meta = {'run': None, 'status': 'unverified story provenance'}
        return story, ledger, meta
def newest(ledger):
    parts = re.split(r'(?m)^## wake ', ledger or '')
    return 'There is no ledger lesson yet.' if len(parts) < 2 else '## wake ' + parts[-1].strip()
def _enter(work, lesson, state):
    lesson = WAKE.sub('', str(lesson)).strip() or 'No lesson was written.'
    if len(lesson) > 1200:
        raise Fail('a lesson has 1-1200 characters')
    path = Path(work) / 'ledger.md'
    previous = path.read_text(encoding='utf-8') if path.exists() else ''
    parts = WAKE.split(previous)[1:]
    kept = [part for part in parts if part.split(None, 1)[0] != SET['run']]
    start = float(SET['wake_start']) if SET.get('wake_start') else None
    minutes = SET.get('minutes') or ''
    stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start)) if start is not None else 'start unknown'
    used = f'{(time.time() - start) / 60:.1f}' if start is not None else 'unknown'
    budget = f' of {float(minutes):g}' if minutes else ''
    kept.append(f"{SET['run']} {stamp} used {used}{budget} min, {state}\n{lesson}\n")
    atomic(path, ''.join('## wake ' + part for part in kept))
    return {'wakes': len(kept), 'lesson_len': len(lesson)}
def current_lesson(work):
    path = Path(work) / 'ledger.md'
    for part in WAKE.split(path.read_text(encoding='utf-8') if path.exists() else '')[1:]:
        if part.split(None, 1)[0] == SET['run']:
            return part.split('\n', 1)[1].strip()
    return ''
def enter(work, lesson, state):
    with locked(Path(work) / 'memory.lock'):
        return _enter(work, lesson, state)
def story_run(app, path, dry=False):
    text = read_file(path)
    meta = {'run': SET['run'], 'chat': os.environ.get('TRIDENT_CHAT_ID'), 'at': time.time(), 'sha256': hashlib.sha256(text.encode()).hexdigest()}
    with locked(app.work / 'memory.lock'):
        atomic(app.work / 'story.txt', text)
        json_file(app.work / 'story.meta.json', meta)
    return {'story_len': len(text)}
def ledger_run(app, path, dry=False):
    text = read_file(path).strip()
    if not text or len(text) > 1200:
        raise Fail('a lesson has 1-1200 characters')
    return enter(app.work, text, 'open')
def learn_run(app, _arg, dry=False):
    _current, earlier = journals(app.work, SET['run'])
    ledger = (app.work / 'ledger.md').read_text(encoding='utf-8') if (app.work / 'ledger.md').exists() else ''
    return {'text': learned_line(earlier, ledger)}
def stop_run(app, _arg, dry=False):
    if not dry:
        (app.work / 'stop').write_bytes(b'')
    return {'stopped': not dry}
def map_run(app, _arg, dry=False):
    found = {}
    for path in CODE.rglob('*.py'):
        if set(path.parts) & {'agent', 'shots', 'actions', 'streams', '__pycache__', '.git'}:
            continue
        found[str(path.relative_to(CODE))] = len(path.read_text(encoding='utf-8').splitlines())
    organism_lines = found.get('organism.py', 0)
    return {'work': str(app.work), 'file': 'organism.py', 'organism_lines': organism_lines, 'under_1000': organism_lines < 1000 and sum(found.values()) < 1000, 'total_python_lines': sum(found.values()), 'python_files': found, 'vision': SET.get('vision_url'), 'journal_port': PORT, 'tools': [flag for _name, flag, _fn, _kind in TOOLS]}
def brain(sentence):
    if SET.get('mind_model'):
        return SET['mind_model']
    said = str(sentence).lower()
    return next((slug for slug in sorted(BRAINS, key=len, reverse=True) if slug in said), 'grok-4.7-medium-fast')
def render_prompt(app, sentence, minutes, end):
    story, ledger, meta = snapshot(app.work)
    campaign_path = app.work / 'campaign.txt'
    campaign = campaign_path.read_text(encoding='utf-8') if campaign_path.exists() else ''
    current, earlier = journals(app.work, SET['run'])
    status = 'current wake' if meta.get('run') == SET['run'] else 'inherited; not current progress'
    budget = f'The wake has {float(minutes):g} minutes and ends at {time.strftime("%H:%M:%S", time.localtime(end))}. Under 5 percent remaining, stop new work, write the story and one ledger lesson, and stop. A terminate answer is her claim, not a reason to stop early.' if minutes else 'No end time was given.'
    packed = {'current': current, 'historical': earlier, 'lesson_status': 'Counts come from run markdown headings.'}
    tools = command() + '\nEach tool below is that command plus the flag on its first line.\n' + '\n'.join(DOCS)
    traj = json.loads((app.work / 'fara_traj.json').read_text(encoding='utf-8')) if (app.work / 'fara_traj.json').exists() else {}
    fara = eye_log(traj) if traj.get('run') == SET['run'] else 'none this wake' + (f" (the last wake's eye ended {traj.get('stop') or 'terminated'})" if traj else '')
    return '\n\n'.join((MIND.format(budget=budget), 'STORY', json.dumps({'status': status, 'provenance': meta}) + '\n' + (story or '(empty)'), 'CAMPAIGN', campaign or '(empty)', 'FARA', fara, 'LEDGER', newest(ledger), 'MEASURED', json.dumps(packed, ensure_ascii=False), 'LEARNED', learned_line(earlier, ledger), 'BRAIN', brain(sentence), 'TOOLS', tools, 'SENTENCE', sentence.strip()))
def text_of(message):
    content = (message or {}).get('content') if isinstance(message, dict) else message
    if isinstance(content, list):
        return ''.join(part.get('text', '') if isinstance(part, dict) else str(part) for part in content)
    return '' if content is None else str(content)
def lean(node, keep=None):
    if isinstance(node, list): return [lean(item, keep) for item in node]
    if not isinstance(node, dict): return node
    if keep is None: keep = next((value['args'].get('command') for value in node.values() if isinstance(value, dict) and isinstance(value.get('args'), dict)), None)
    drop = {'parsingResult', 'hookAdditionalContexts'}
    return {key: lean(value, keep) if isinstance(value, (dict, list)) else value for key, value in node.items() if key not in drop and not (key == 'interleavedOutput' and value == node.get('stdout')) and not (key == 'command' and value == keep and 'stdout' in node)}
def spawn(app, model, prompt_path, job_name):
    vendor, profile = app.work / 'agent', app.work / 'agent' / 'config'
    profile.mkdir(parents=True, exist_ok=True)
    (vendor / 'data').mkdir(parents=True, exist_ok=True)
    temp = CODE.parent / 'temp'
    temp.mkdir(exist_ok=True)
    (profile / 'cli-config.json').write_text(json.dumps({'version': 1, 'permissions': {'allow': ['Read(**)', 'Write(**)', 'Shell(*)', 'WebFetch(*)', 'Mcp(*:*)'], 'deny': ['Read(shots/**)', 'Read(**/*.png)']}, 'sandbox': {'mode': 'disabled'}, 'approvalMode': 'unrestricted', 'autoAcceptWebSearch': True}), encoding='utf-8')
    versions = Path(os.environ['LOCALAPPDATA']) / 'cursor-agent' / 'versions'
    folders = [path for path in versions.iterdir() if path.name[:1].isdigit() and (path / 'index.js').exists()] if versions.exists() else []
    if not folders:
        raise Fail('the agent runtime is not installed')
    folder = max(folders)
    env = os.environ | {'CURSOR_CONFIG_DIR': str(profile), 'CURSOR_DATA_DIR': str(vendor / 'data'), 'TEMP': str(temp), 'TMP': str(temp), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'}
    env.pop('OPENROUTER_API_KEY', None)
    argv = [str(folder / 'node.exe'), '-e', "process.argv.push(require('node:fs').readFileSync(process.argv.pop(),'utf8'));require(process.argv[1]);", str(folder / 'index.js'), '--trust', '--force', '-p', '--model', model, '--workspace', str(app.work), '--output-format', 'stream-json', str(prompt_path)]
    return subprocess.Popen([str(PYTHON), '-B', str(CODE / 'main.py'), '--boot', job_name, *argv], cwd=str(app.work), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=0x08000000)
def read_agent(proc, app, end):
    calls = running = 0
    thought = ''
    last, stop_at, result_at, reason = '', None, None, None
    inbox = queue.Queue()
    streams = app.work / 'streams'
    streams.mkdir(exist_ok=True)
    stream_path = streams / (uuid.uuid4().hex + '.stream')
    def pump():
        try:
            with open(stream_path, 'wb') as saved:
                for raw in proc.stdout:
                    saved.write(raw if isinstance(raw, bytes) else raw.encode())
                    saved.flush()
                    inbox.put(raw)
        except OSError as error:
            inbox.put(error)
        finally:
            inbox.put(None)
    threading.Thread(target=pump, daemon=True).start()
    def finish(why):
        if thought: heading('think', thought)
        if proc.poll() is None: proc.kill()
        return why, last
    while True:
        now = time.time()
        if end and now >= end:
            return finish('deadline')
        if (app.work / 'stop').exists():
            stop_at = stop_at or now + 60
            if now >= stop_at:
                return finish('stopped')
        if result_at is not None and now >= result_at + .5:
            return finish('failed_agent' if proc.poll() not in (None, 0) else reason)
        try:
            raw = inbox.get(timeout=.02)
        except queue.Empty:
            continue
        if isinstance(raw, Exception):
            heading('err', str(raw))
            return finish('failed_stream')
        if raw is None:
            try:
                code = proc.wait(timeout=.05)
            except subprocess.TimeoutExpired:
                code = None
            return finish('failed_agent' if code not in (None, 0) else (reason or ('stopped' if stop_at else 'failed_eof')))
        line = raw.decode('utf-8', 'replace').rstrip('\r\n') if isinstance(raw, bytes) else str(raw).rstrip('\r\n')
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        kind, sub = item.get('type'), item.get('subtype')
        if kind == 'thinking' and sub == 'delta':
            thought += item.get('text') or ''; continue
        if thought:
            heading('think', thought); thought = ''
        if kind == 'assistant' and text_of(item.get('message')):
            last = text_of(item.get('message'))
            heading('say', last)
        elif kind == 'tool_call' and sub == 'started':
            running += 1
        elif kind == 'tool_call' and sub == 'completed':
            running = max(0, running - 1)
            calls += 1
            heading('tool', json.dumps(lean(item.get('tool_call') or {}), ensure_ascii=False))
            if calls >= 42 and running == 0 and not (app.work / 'stop').exists() and not (end and time.time() >= end):
                return finish('rotate')
        elif kind == 'system' and sub == 'init':
            heading('start', str(item.get('model') or ''))
        elif kind == 'result':
            failed = item.get('is_error') or item.get('error') or str(sub or '').startswith('error')
            reason = 'failed_agent' if failed else ('stopped' if stop_at else 'completed')
            last = last or str(item.get('result') or '')
            if failed:
                return finish(reason)
            result_at = now
def drive(app, sentence, minutes):
    start = time.time()
    end = start + minutes * 60 if minutes else None
    SET['wake_start'], SET['wake_end'] = str(start), str(end or '')
    (app.work / 'stop').unlink(missing_ok=True)
    atomic(app.work / 'region.txt', 'NONE')
    if sentence:
        atomic(app.work / 'goal.txt', sentence)
    outcome, said, proc, job, previous, chat_index = 'failed', '', None, None, os.environ.get('TRIDENT_CHAT_ID'), 0
    app.mirror = mirror = threading.Event()
    def follow(seen=0):
        while True:
            try: data = RUN.read_bytes() if RUN.exists() else b''
            except OSError: data = b''
            chunk = data if len(data) < seen else data[seen:]
            if chunk: sys.stdout.buffer.write(chunk); sys.stdout.buffer.flush(); seen = len(data)
            if mirror.is_set(): return
            mirror.wait(0.4)
    app.mirror_thread = threading.Thread(target=follow, daemon=True); app.mirror_thread.start()
    try:
        heading('wake', f'minutes={minutes or ""}')
        traj = app.work / 'fara_traj.json'
        if traj.exists():
            prior = json.loads(traj.read_text(encoding='utf-8'))
            if prior.get('open'):
                prior['open'], prior['stop'] = False, 'interrupted'; json_file(traj, prior)
        atomic(app.work / 'eye.live', '1')
        app.eye_thread = threading.Thread(target=eye_watch, args=(app, end), daemon=True); app.eye_thread.start()
        while not (end and time.time() >= end) and not (app.work / 'stop').exists():
            chat_index += 1
            os.environ['TRIDENT_CHAT_ID'] = f"{SET['run']}:{chat_index}"
            atomic(app.work / 'prompt.txt', render_prompt(app, sentence, minutes, end))
            heading('chat', brain(sentence))
            job_name = 'Trident-' + uuid.uuid4().hex
            job = app.desk.open_job(name=job_name)
            try:
                proc = spawn(app, brain(sentence), app.work / 'prompt.txt', job_name)
                outcome, said = read_agent(proc, app, end)
            finally:
                app.desk.close_job(job)
                job = None
                if proc is not None and proc.poll() is None:
                    proc.kill()
            if proc is not None:
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                heading('agent-exit', outcome)
                proc = None
            if outcome != 'rotate': break
        else:
            outcome = 'deadline' if end and time.time() >= end else 'stopped'
        if said:
            heading('report', said)
        return {'state': outcome, 'minutes': round((time.time() - start) / 60, 1)}
    finally:
        (app.work / 'eye.live').unlink(missing_ok=True)
        if getattr(app, 'eye_thread', None): app.eye_thread.join(5)
        app.desk.close_job(job)
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        enter(app.work, current_lesson(app.work), outcome)
        if previous is None: os.environ.pop('TRIDENT_CHAT_ID', None)
        else: os.environ['TRIDENT_CHAT_ID'] = previous
def mind_run(app, path, dry=False):
    sentence = read_file(path).strip()
    minutes = float(SET['minutes']) if SET.get('minutes') else None
    return drive(app, sentence, minutes)
TOOLS = (
    ('eye', 'eye_file', eye_run, 'text'), ('story', 'story_file', story_run, 'text'), ('ledger', 'ledger_file', ledger_run, 'text'),
    ('windows', 'windows', windows_run, 'bool'), ('region', 'region', region_run, 'text'), ('place', 'place', place_run, 'place'),
    ('learn', 'learn', learn_run, 'bool'), ('stop', 'stop', stop_run, 'bool'), ('mind', 'mind_file', mind_run, 'text'), ('map', 'map', map_run, 'bool'),
)
class App:
    def __init__(self, work):
        self.work = Path(work).resolve()
        self.work.mkdir(parents=True, exist_ok=True)
        self.desk = Desk(self.work)
def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    for flag in ('work', 'run', 'minutes', 'wake-start', 'wake-end', 'shot-delay', 'vision-url', 'vision-model', 'decide-model', 'mind-model'):
        parser.add_argument('--' + flag, default='')
    parser.add_argument('--coordinate-mode', choices=('normalized', 'pixels'), default='normalized')
    parser.add_argument('--fresh', action='store_true')
    parser.add_argument('--dry', action='store_true')
    parser.add_argument('--journal', action='store_true')
    for _name, flag, _fn, kind in TOOLS:
        parser.add_argument('--' + flag.replace('_', '-'), **({'action': 'store_true'} if kind == 'bool' else {'nargs': 5 if kind == 'place' else None}))
    args = parser.parse_args(argv)
    configure(args.work or str(CODE), args.run, args.minutes, getattr(args, 'wake_start'), getattr(args, 'wake_end'), args.shot_delay or '1.5', args.vision_url or 'http://192.168.16.31:1234', args.vision_model or 'retina', args.decide_model or 'typesafe/jev-1.14-20261014', args.mind_model, args.coordinate_mode)
    SET['dry'] = bool(args.dry)
    if args.journal:
        serve_journal()
        return
    app = App(args.work or str(CODE))
    picks = []
    for name, flag, fn, kind in TOOLS:
        value = getattr(args, flag.replace('-', '_'))
        if kind == 'bool' and value:
            picks.append((name, fn, True))
        elif kind != 'bool' and value not in (None, ''):
            picks.append((name, fn, value))
    if len(picks) != 1:
        raise Fail('choose one tool flag')
    name, fn, value = picks[0]
    if (app.work / 'stop').exists() and name in BLOCKED:
        raise Fail('stopped')
    try:
        result = fn(app, value, args.dry)
    except Exception as error:
        heading('err', f'{name} {error}')
        raise
    if isinstance(result, dict):
        emit(name, result)
    if getattr(app, 'mirror', None): app.mirror.set(); app.mirror_thread.join(2)
if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--boot':
        try:
            sys.exit(boot(sys.argv[2], sys.argv[3:]))
        except Exception as error:
            print(str(error), file=sys.stderr)
            sys.exit(1)
    try:
        main()
    except Fail as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
