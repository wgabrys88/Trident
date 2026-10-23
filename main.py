#!/usr/bin/env python3
"""Trident, a living program that wakes on one owner sentence.

The mind is Grok run by the Cursor CLI (main.py --mind). It acts only by calling this file's tools:
Retina (Fara on llama.cpp) is the eye, Jev (OpenRouter Decisions) is the judge, win32.py is the hand,
scan.py is the pixel watch that gates the hand. story.txt and ledger.md carry it across wakes,
skills/*.py grow its tool set, and it may rewrite this file. See README.md.
"""

import argparse, ast, atexit, base64, ctypes, importlib.util, io, json, math, os, re, signal, socket, subprocess, sys, threading, time, urllib.parse, urllib.request, uuid, win32job
from collections import Counter
from contextlib import ExitStack, redirect_stdout
from ctypes import wintypes
from dataclasses import InitVar, dataclass
from functools import cached_property
from pathlib import Path
from PIL import Image

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parent
PYTHON = Path(sys.executable)
E = os.environ.get

NVIDIA_HOST = '192.168.16.31'
DECISION_MODEL = E('TRIDENT_JEV_MODEL', 'typesafe/jev-1.14-20261014')
DECISION_URL = 'https://openrouter.ai/api/alpha/decisions'
VISION_URL = E('TRIDENT_VISION_URL', f'http://{NVIDIA_HOST}:1234')
VISION_MODEL = E('TRIDENT_VISION_MODEL', 'retina')

RUN = ROOT / ('run_' + os.environ.setdefault('TRIDENT_RUN', time.strftime('%Y%m%d_%H%M%S')) + '.md')
if sys.stdout.isatty():
    os.environ.setdefault('TRIDENT_CONSOLE', str(os.getpid()))

SCREENSHOT_DELAY = float(E('TRIDENT_SHOT_DELAY', '4'))
NORM = 1000
PAIR = 2
DRAG_MIN_POINTS = 2
SCORE_MIN_LEVELS = 2
DOUBLE_CLICKS = 2
SCROLL_UP = 1
DRAG_DURATION = 0.2
RETINA_TEMPERATURE = 0
FARA_PATCH, FARA_MERGE = 16, 2
FARA_FACTOR = FARA_PATCH * FARA_MERGE
FARA_MIN_TOKENS = 256
FARA_MIN_PIXELS = FARA_MIN_TOKENS * FARA_FACTOR ** 2
FARA_MAX_PIXELS = 4096 * FARA_FACTOR ** 2
FARA_TARGET_WIDTH = int(E('TRIDENT_FARA_WIDTH', '1440'))
FARA_MAX_TOKENS = 512
FARA_TIMEOUT = 180
CAPTURE_SCALE = 1.0
CAPTURE_NATIVE = 0
DPI_AWARENESS = 2
JOURNAL_HOST = '0.0.0.0'
JOURNAL_PORT = 8765
JOURNAL_TICK = 0.4
JOURNAL_RULE = 72
DETACHED = 0x00000008 | 0x00000200 | 0x08000000
HALT_POLL = 0.1
CLOCK_PLACES = 3
SHOT_MS = 1000
DECIMAL_PLACES = 2
INTERRUPTED_EXIT = 130
EXIT_SECONDS = 1
SCAN_PAD = 30
SCAN_STALE = 1.5
SCAN_EXPECT = 8
SCAN_STOP_WAIT = 3
SCAN_KEYS = ('kind', 'grid', 'samples', 'rhythmic', 'alerts', 'at', 'pid', 'skipped')
JEV_RATIO = 2
JOURNAL_TEXT = 8000
MINUTES = E('TRIDENT_MINUTES', '')
LEDGER_WAKES, LEDGER_CHARS, LEDGER_LESSON = 12, 8000, 1200
RECALL_RUNS, RECALL_CHARS = 5, 800
WAKE, WAKE_ID = re.compile(r'(?m)^## wake '), os.environ['TRIDENT_RUN']
GENERIC_READ_WRITE = 0xC0000000
FILE_SHARE_ALL = 7
OPEN_ALWAYS = 4
FILE_ATTRIBUTE_NORMAL = 0x80
LOCK_EXCLUSIVE = 2
LOCK_ALL_LOW = 0xFFFFFFFF
LOCK_ALL_HIGH = 0x7FFFFFFF
GENERIC_WRITE = 0x40000000
SHARE_READ_WRITE = 3
OPEN_EXISTING = 3

FARA_IDENTITY = '''You are Fara, a computer use agent (CUA) specialized for web browsers. You are developed by Microsoft AI Frontiers. You assist users with completing and automating tasks that require the use of a web browser.

The model was trained in the timeframe of January - April 2026. You can effectively perform tasks even beyond this range by accessing the web browser and using the latest information on the live web. But your knowledge cutoff is limited to early 2026, so you may not be aware of events or developments that occurred after that time, without explicitly browsing and searching for latest information on the web.

This edition of the model was trained using SFT on top of Qwen3.5-9B, using a synthetic data mixture generated and developed by Microsoft AI Frontiers.'''

FARA_CRITICAL = '''A critical point is a situation where we must pause and request information or confirmation from the user before proceeding. There are three types:

Case 1: Missing User Information — The task requires personal information that the user has not provided (e.g., email, phone number, address, payment details). Never fabricate or assume personal information. Fill in only what the user has explicitly provided, then pause and ask for any missing required fields. (e.g., form requires phone number but user only gave name and email -> fill name and email, then ask for phone number.) If the user has provided all required information, proceed without stopping.

Case 2: Underspecified Task — The task description is ambiguous or missing details needed to make a decision at the current step. Pause and ask for clarification. (e.g., user asks to book a flight but doesn't specify destination -> ask for destination.) If the user's instructions contain all information needed for the current decision, proceed without stopping.

Case 3: Irreversible Action — We are about to perform an action that cannot be undone (e.g., submitting a form, completing a purchase, sending a message, deleting data). If the user explicitly authorized the action (e.g., "submit the form", "complete the purchase", "you have my permission to submit") -> proceed without stopping. If the user did NOT explicitly authorize the action -> stop and ask for confirmation. (e.g., "fill out a form" with no mention of submitting -> fill the form, then ask before submitting; "fill out and submit a form" -> fill and submit without stopping.)

Only stop at a critical point if (1) required information is missing, (2) the task is ambiguous, OR (3) an irreversible action lacks explicit user authorization. If the user has provided all necessary information AND explicitly authorized the action, proceed without interruption.'''

FARA_TOOL = dict(
    name='computer_use',
    description=f'''Use a mouse and keyboard to interact with a computer, and take screenshots.
* This is an interface to a desktop GUI. You do not have access to a terminal or applications menu. You must click on desktop icons to start applications.
* Some applications may take time to start or process actions, so you may need to wait and take successive screenshots to see the results of your actions. E.g. if you click on Firefox and a window doesn't open, try wait and taking another screenshot.
* The screen's resolution is {NORM}x{NORM}.
* Whenever you intend to move the cursor to click on an element like an icon, you should consult a screenshot to determine the coordinates of the element before moving the cursor.
* If you tried clicking on a program or link but it failed to load, even after waiting, try adjusting your cursor position so that the tip of the cursor visually falls on the element that you want to click.
* Make sure to click any buttons, links, icons, etc with the cursor tip in the center of the element. Don't click boxes on their edges.''',
    parameters=dict(
        properties=dict(
            action=dict(type='string', description='The action to perform. The available actions are:\n* `key`: Performs key down presses on the arguments passed in order, then performs key releases in reverse order. Includes "Enter", "Alt", "Shift", "Tab", "Control", "Backspace", "Delete", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "PageDown", "PageUp", "Shift", etc.\n* `type`: Type a string of text on the keyboard.\n* `mouse_move`: Move the cursor to a specified (x, y) pixel coordinate on the screen.\n* `left_click`: Click the left mouse button.\n* `double_click`: Double-click the left mouse button.\n* `right_click`: Click the right mouse button.\n* `triple_click`: Triple-click the left mouse button (e.g. to select a line of text).\n* `left_click_drag`: Click and drag the cursor to a specified (x, y) pixel coordinate on the screen.\n* `scroll`: Performs a scroll of the mouse scroll wheel.\n* `hscroll`: Performs a horizontal scroll (mapped to regular scroll).\n* `visit_url`: Visit a specified URL.\n* `history_back`: Go back to the previous page in the browser history.\n* `web_search`: Perform a web search with a specified query.\n* `read_page_answer_question`: Read the current page content and answer a question about it.\n* `pause_and_memorize_fact`: Pause and memorize a fact for future reference.\n* `ask_user_question`: Ask the user a clarifying question and wait for a response.\n* `wait`: Wait specified seconds for the change to happen.\n* `terminate`: Terminate the current task and provide the final answer.', enum=['key','type','mouse_move','left_click','left_click_drag','right_click','double_click','triple_click','scroll','hscroll','visit_url','history_back','web_search','read_page_answer_question','pause_and_memorize_fact','ask_user_question','wait','terminate']),
            keys=dict(type='array', description='Required only by `action=key`.'),
            text=dict(type='string', description='Required only by `action=type`.'),
            coordinate=dict(type='array', description='(x, y): The x (pixels from the left edge) and y (pixels from the top edge) coordinates to move the mouse to. Required by `action=left_click`, `action=double_click`, `action=right_click`, `action=triple_click`, `action=left_click_drag`, and `action=mouse_move`.'),
            pixels=dict(type='number', description='The amount of scrolling to perform. Positive values scroll up, negative values scroll down. Required only by `action=scroll` and `action=hscroll`.'),
            url=dict(type='string', description='The URL to visit. Required only by `action=visit_url`.'),
            query=dict(type='string', description='The query to search for. Required only by `action=web_search`.'),
            fact=dict(type='string', description='The fact to remember for the future. Required only by `action=pause_and_memorize_fact`.'),
            question=dict(type='string', description='The question to ask. Required by `action=read_page_answer_question` and `action=ask_user_question`.'),
            time=dict(type='number', description='The seconds to wait. Required only by `action=wait`.'),
            answer=dict(type='string', description='The final answer for the task. Required only by `action=terminate`.')
        ),
        required=['action'],
        type='object'
    )
)
FARA_SYSTEM = FARA_IDENTITY + '\n\n' + FARA_CRITICAL + '\n\n' + 'You are provided with function signatures within <tools></tools> XML tags:\n<tools>\n' + json.dumps(dict(type='function', function=FARA_TOOL), ensure_ascii=False) + '\n</tools>\n\nFor each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:\n<tool_call>\n{"name": <function-name>, "arguments": <args-json-object>}\n</tool_call>'

JEV_USAGE = '--jev "NARRATIVE" "QUESTION" ["QUESTION" ...], each QUESTION one of: "name?Claim" (yes/no), "name?Q|true=Yes|false=No", "name?Q|a=Option|b=Option" (choice), "name#Q|Level1|Level2" (score), "name@target in the current capture" (box)'
CHANGE_APPROACH = 'Keep the goal. This way is not working. Switch to a different strategy and continue.'
POWERSHELL = ['powershell.exe', '-NoProfile', '-Command']
GESTURES = frozenset(('click', 'rclick', 'dclick', 'tclick', 'move', 'drag', 'scroll', 'wheel', 'hwheel', 'key', 'wait', 'type', 'paste', 'expect'))
FARA_LOOK = 'Name every window, dialog and overlay you see, then answer: '
FARA_ACTIONS = FARA_TOOL['parameters']['properties']['action']['enum']
CLI_CONFIG = dict(version=1, permissions=dict(allow=['Read(**)', 'Write(**)', 'Shell(*)', 'WebFetch(*)', 'Mcp(*:*)'], deny=[]), sandbox=dict(mode='disabled'), approvalMode='unrestricted', autoAcceptWebSearch=True)

ULONG_PTR = ctypes.c_size_t
class OVERLAPPED(ctypes.Structure):
    _fields_ = [('Internal', ULONG_PTR), ('InternalHigh', ULONG_PTR), ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]
K32 = ctypes.WinDLL('kernel32', use_last_error=True)
K32.CreateFileW.restype = wintypes.HANDLE
for _fn, _args in dict(
    GetFileSizeEx=[wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong)],
    SetFilePointerEx=[wintypes.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD],
    LockFileEx=[wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(OVERLAPPED)],
    UnlockFileEx=[wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(OVERLAPPED)],
    WriteFile=[wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p],
    AttachConsole=[wintypes.DWORD], FreeConsole=[], CloseHandle=[wintypes.HANDLE]
).items():
    fn = getattr(K32, _fn); fn.argtypes, fn.restype = _args, wintypes.BOOL

def text(p, v):
    """Write v to p atomically (temp file, then replace) and journal it."""
    p = Path(p)
    t = p.with_name(p.name + '.' + uuid.uuid4().hex + '.pending')
    t.write_bytes(v.encode('utf-8'))
    t.replace(p)
    log('file', dict(path=str(p), text=v) if len(v) <= JOURNAL_TEXT else dict(path=str(p), size=len(v)))

def read(p): return json.loads(Path(p).read_text('utf-8-sig'))
def write(p, v): text(p, json.dumps(v, ensure_ascii=False))
def scan_state(work):
    """The watch's last frame from work/scan.json, or {} when there is none."""
    try: return json.loads((Path(work) / 'scan.json').read_text('utf-8'))
    except (OSError, json.JSONDecodeError): return {}
def clock():
    """Inside a wake: {clock: now, used, and left when the wake has a budget}. Outside: {}."""
    if not E('TRIDENT_START'): return {}
    now = time.time()
    left = dict(left=f"{(float(E('TRIDENT_END')) - now) / 60:.1f} min") if E('TRIDENT_END') else {}
    return dict(clock=dict(now=time.strftime('%H:%M:%S'), used=f"{(now - float(E('TRIDENT_START'))) / 60:.1f} min") | left)
def inside(point, box, pad=0):
    return box[0] - pad <= point[0] <= box[2] + pad and box[1] - pad <= point[1] <= box[3] + pad
def b64(im):
    buf = io.BytesIO(); im.save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()

META = frozenset('beforeFullFileContent afterFullFileContent session_id hookAdditionalContexts apiKeySource permissionMode cwd adminCommandDenylist closeStdin conversationId fileOutputThresholdBytes hardTimeout hasInputRedirect hasOutputRedirect isBackground parsingResult requestId simpleCommands skipApproval timeout timeoutBehavior relatedCursorRulePaths relatedCursorRules isEmpty exceededLimit totalLines fileSize clientTruncated ripgrepTruncated contentTruncated isContextLine executionTime signal interleavedOutput localExecutionTime system_fingerprint usage prompt_tokens completion_tokens total_tokens finish_reason index object created id pid shellId msToWait backgroundReason workingDirectory readRange outputFilePath localExecutionTimeMs outputLength regexRequested runtimeMs totalFiles'.split())
THOUGHT = []

def clip(value):
    if isinstance(value, dict): return {k: clip(v) for k, v in value.items() if k not in META and not (k=='exitCode' and type(v) is int and v==0)}
    if isinstance(value, list): return [clip(v) for v in value]
    return value
def words(message):
    content = (message or {}).get('content') if isinstance(message, dict) else message
    if isinstance(content, str): return content
    if isinstance(content, list): return ''.join(part.get('text','') if isinstance(part,dict) else str(part) for part in content)
    return '' if content is None else json.dumps(content, ensure_ascii=False)
def row(kind, **fields): return dict(t=time.strftime('%H:%M:%S'), kind=kind, **fields)
def take_thought():
    if not THOUGHT: return None
    text = ''.join(THOUGHT); THOUGHT.clear()
    return row('think', text=text) if text else None

def result_fields(result, name):
    result = clip(result)
    if isinstance(result, dict) and isinstance(result.get('success'), dict):
        done = result['success']
        fields = dict(text=done.pop('content'), **done) if isinstance(done.get('content'), str) else dict(done)
    elif isinstance(result, dict) and 'failure' in result: fields = dict(failure=result['failure'])
    else: fields = result if isinstance(result, dict) else dict(text='' if result is None else str(result))
    if name == 'shell' and 'stdout' in fields: del fields['command']
    if isinstance(fields.get('failure'), dict): fields['failure'].pop('command', None)
    return fields
def resp_fields(body):
    if not isinstance(body, dict): return dict(text='' if body is None else str(body))
    choices = body.get('choices')
    if isinstance(choices, list) and choices:
        return dict(text=words((choices[0] or {}).get('message')))
    if 'answers' in body: return dict(answers=clip(body['answers']))
    return clip(body)
def narrate(kind, data):
    rows = []
    if kind == 'agent':
        try: event = json.loads(data)
        except json.JSONDecodeError: return [row('agent', text=data)]
        typ, sub = event.get('type'), event.get('subtype')
        if typ == 'thinking' and sub == 'delta':
            THOUGHT.append(event.get('text') or ''); return []
        thought = take_thought()
        if thought: rows.append(thought)
        if typ == 'thinking': return rows
        if typ == 'system' and sub == 'init': rows.append(row('start', text=event.get('model') or ''))
        elif typ == 'user': rows.append(row('user', text=words(event.get('message'))))
        elif typ == 'assistant':
            text = words(event.get('message'))
            if text: rows.append(row('say', text=text))
        elif typ == 'tool_call':
            call = event.get('tool_call') or {}
            name = next((key[:-8] for key in call if key.endswith('ToolCall')), 'tool')
            body = call.get(name + 'ToolCall') or {}
            if sub == 'started': rows.append(row('call', name=name, **clip(body.get('args') or {})))
            else: rows.append(row('result', name=name, **result_fields(body.get('result'), name)))
        else: rows.append(row(typ or 'agent', **clip({k: v for k, v in event.items() if k not in ('type', 'subtype')})))
        return rows
    thought = take_thought()
    if thought: rows.append(thought)
    if kind == 'resp': rows.append(row('resp', url=data.get('url'), **resp_fields(data.get('body'))))
    elif isinstance(data, dict): rows.append(row(kind, **clip(data)))
    else: rows.append(row(kind, text='' if data is None else str(data)))
    return rows
def unfold(v):
    if not isinstance(v, str): return v
    v = v.replace('\r\n', '\n').replace('\r', '\n'); lines = v.splitlines() or ['']
    if len(lines) > 1 and all(not s.strip() or s.lstrip()[:1] in '{[' for s in lines):
        got = []
        for s in lines:
            if not s.strip(): continue
            try: got.append(json.loads(s.strip()))
            except json.JSONDecodeError: return v
        return got
    t = v.strip()
    if t[:1] not in '{[': return v
    try: return json.loads(t)
    except json.JSONDecodeError: return v
def plain(v, n=0):
    pad = '  ' * n
    if v is None: return pad + 'null'
    if isinstance(v, str): return pad + v.replace('\r\n', '\n').replace('\r', '\n')
    if isinstance(v, dict): return '\n'.join(f'{pad}{k}: {plain(x)}' if not isinstance(x, (dict, list)) else f'{pad}{k}:\n{plain(x, n + 1)}' for k, x in v.items() if x != '')
    if isinstance(v, list):
        if not v: return pad + '(none)'
        return '\n'.join(f'{pad}-\n{plain(x, n + 1)}' if isinstance(x, (dict, list)) else f'{pad}- {x}' for x in v)
    return pad + str(v)
def show(k, v):
    v = unfold(v)
    if k == 'text' and isinstance(v, str): return v
    if isinstance(v, str): return f'{k}:\n{v}' if '\n' in v else f'{k}: {v}'
    if isinstance(v, (dict, list)):
        shown = plain(v, 1); return shown if k == 'text' else f'{k}:\n{shown}'
    return f'{k}: {v}'
LABEL = dict(call='request', result='response', req='request', resp='response')
def render(item):
    kind = LABEL.get(item['kind'], item['kind'])
    title = f"## {item['t']} {kind}" + (f" {item['name']}" if item.get('name') else '')
    keys = [k for k in item if k not in ('t', 'kind', 'name') and item[k] not in ('', None, [], {})]
    body = show('text', item['text']) if keys == ['text'] else '\n\n'.join(show(k, item[k]) for k in keys)
    rule = '=' * JOURNAL_RULE if kind == 'request' else '-' * JOURNAL_RULE if kind == 'response' else ''
    return ((rule + '\n\n' if rule else '') + title + '\n\n' + body + '\n\n').encode('utf-8')
def _xfer(h, buf):
    n, off = wintypes.DWORD(), 0
    while off < len(buf):
        chunk = (ctypes.c_char * (len(buf) - off)).from_buffer_copy(buf[off:])
        if not K32.WriteFile(h, chunk, len(chunk), ctypes.byref(n), None) or not n.value: raise ctypes.WinError()
        off += n.value
def append(blob):
    h = K32.CreateFileW(str(RUN), GENERIC_READ_WRITE, FILE_SHARE_ALL, None, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, None)
    if h == wintypes.HANDLE(-1).value: raise ctypes.WinError()
    ov, locked = OVERLAPPED(), False
    try:
        if not K32.LockFileEx(h, LOCK_EXCLUSIVE, 0, LOCK_ALL_LOW, LOCK_ALL_HIGH, ctypes.byref(ov)): raise ctypes.WinError()
        locked = True
        size, moved = ctypes.c_longlong(), ctypes.c_longlong()
        if not K32.GetFileSizeEx(h, ctypes.byref(size)): raise ctypes.WinError()
        if not K32.SetFilePointerEx(h, size.value, ctypes.byref(moved), 0): raise ctypes.WinError()
        _xfer(h, blob)
        return blob
    finally:
        if locked: K32.UnlockFileEx(h, 0, LOCK_ALL_LOW, LOCK_ALL_HIGH, ctypes.byref(ov))
        K32.CloseHandle(h)
def mirror(blob):
    if sys.stdout.isatty(): sys.stdout.buffer.write(blob); sys.stdout.buffer.flush(); return
    K32.FreeConsole()
    if 'TRIDENT_CONSOLE' not in os.environ or not K32.AttachConsole(int(os.environ['TRIDENT_CONSOLE'])): return
    try:
        h = K32.CreateFileW('CONOUT$', GENERIC_WRITE, SHARE_READ_WRITE, None, OPEN_EXISTING, 0, None)
        if h == wintypes.HANDLE(-1).value: return
        try: _xfer(h, blob)
        finally: K32.CloseHandle(h)
    finally: K32.FreeConsole()
def _journal_target():
    pointer = ROOT / 'journal.path'
    try:
        target = Path(pointer.read_text('utf-8').strip())
        if str(target): return target
    except OSError:
        pass
    return RUN

def _port_open():
    try:
        with socket.create_connection(('127.0.0.1', JOURNAL_PORT), 0.3): return True
    except OSError:
        return False

def serve():
    (ROOT / 'journal.path').write_text(str(RUN), encoding='utf-8')
    if _port_open(): return
    try:
        subprocess.Popen([str(PYTHON), '-u', '-B', '-X', 'utf8', str(SCRIPT), '--journal'], cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=DETACHED, close_fds=True)
    except OSError:
        return

def journal_serve():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    page = b'<!DOCTYPE html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>trident</title><body style="background:#000;color:#fff;margin:0"><pre id=t style="white-space:pre-wrap;margin:0;font-family:monospace"></pre><script>var t=document.getElementById("t"),es=new EventSource("/events");function z(){scrollTo(0,1e9)}es.addEventListener("reset",function(e){t.textContent=e.data;z()});es.addEventListener("more",function(e){t.textContent+=e.data;z()})</script>'
    class H(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def _head(self, code, ctype, length=None):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Access-Control-Allow-Origin', '*')
            if length is not None: self.send_header('Content-Length', str(length))
            self.end_headers()
        def _event(self, name, payload):
            text = payload.decode('utf-8', 'replace') if isinstance(payload, (bytes, bytearray)) else payload
            self.wfile.write(('event: %s\n' % name + ''.join('data: %s\n' % line for line in text.split('\n')) + '\n').encode())
            self.wfile.flush()
        def do_GET(self):
            path = self.path.split('?', 1)[0]
            if path == '/md':
                try: body = _journal_target().read_bytes()
                except OSError: body = b''
                self._head(200, 'text/plain; charset=utf-8', len(body)); self.wfile.write(body); return
            if path == '/events':
                self._head(200, 'text/event-stream; charset=utf-8')
                seen, current = -1, None
                try:
                    while True:
                        target = _journal_target()
                        try: size = target.stat().st_size
                        except OSError: size = 0
                        key = str(target)
                        if key != current or size < seen or seen < 0:
                            try: data = target.read_bytes()
                            except OSError: data = b''
                            current, seen = key, 0
                            self._event('reset', data)
                            seen = len(data)
                        elif size > seen:
                            try: data = target.read_bytes()
                            except OSError: data = b''
                            self._event('more', data[seen:]); seen = len(data)
                        time.sleep(JOURNAL_TICK)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    return
            if path in ('/', '/index.html'):
                self._head(200, 'text/html; charset=utf-8', len(page)); self.wfile.write(page); return
            self.send_error(404)
    class Server(ThreadingHTTPServer):
        allow_reuse_address = True
    try: httpd = Server((JOURNAL_HOST, JOURNAL_PORT), H)
    except OSError: return
    httpd.serve_forever()
def log(kind, data):
    """Append one event to this wake's journal run_<id>.md and its live stream."""
    serve()
    for item in narrate(kind, data): mirror(append(render(item)))

def _by_factor(number, factor, how):
    return int(how(number / factor) * factor)

def fara_fit(im):
    """Resize to Fara's grid: width at most FARA_TARGET_WIDTH, both edges multiples of 32, within the token bounds."""
    height, width = im.height, im.width
    if max(height, width) / min(height, width) > 200: raise ValueError('bad image')
    if FARA_TARGET_WIDTH and width > FARA_TARGET_WIDTH:
        height, width = height * FARA_TARGET_WIDTH / width, FARA_TARGET_WIDTH
    h_bar = max(FARA_FACTOR, _by_factor(height, FARA_FACTOR, round))
    w_bar = max(FARA_FACTOR, _by_factor(width, FARA_FACTOR, round))
    if h_bar * w_bar > FARA_MAX_PIXELS:
        beta = math.sqrt((height * width) / FARA_MAX_PIXELS)
        h_bar = max(FARA_FACTOR, _by_factor(height / beta, FARA_FACTOR, math.floor))
        w_bar = max(FARA_FACTOR, _by_factor(width / beta, FARA_FACTOR, math.floor))
    elif h_bar * w_bar < FARA_MIN_PIXELS:
        beta = math.sqrt(FARA_MIN_PIXELS / (height * width))
        h_bar = _by_factor(height * beta, FARA_FACTOR, math.ceil)
        w_bar = _by_factor(width * beta, FARA_FACTOR, math.ceil)
    return im if (im.width, im.height) == (w_bar, h_bar) else im.resize((w_bar, h_bar), Image.Resampling.LANCZOS)

def jev_fit(im):
    """Pad im black to at most JEV_RATIO:1, then fara_fit. Returns (image, sx, sy) to map Jev's box fractions back."""
    width, height = max(im.width, math.ceil(im.height / JEV_RATIO)), max(im.height, math.ceil(im.width / JEV_RATIO))
    canvas = Image.new('RGB', (width, height)); canvas.paste(im, (0, 0))
    return fara_fit(canvas), width / im.width, height / im.height

def fara_call(raw):
    """Split a Fara reply into (thoughts, computer_use call), or raise ValueError."""
    parts = raw.split('<tool_call>', 1)
    if len(parts) != 2: raise ValueError('Fara tool_call expected')
    thoughts, action_text = parts[0].strip(), parts[1].split('</tool_call>', 1)[0].strip()
    try: call = json.loads(action_text)
    except json.JSONDecodeError: call = ast.literal_eval(action_text)
    if not isinstance(call, dict) or call.get('name') != 'computer_use' or not isinstance(call.get('arguments'), dict): raise ValueError('Fara tool_call expected')
    return thoughts, call

def fara_point(arguments):
    if 'coordinate' not in arguments: return None
    xy = arguments['coordinate']
    if not (isinstance(xy, (list, tuple)) and len(xy) == PAIR): raise ValueError('bad box')
    x, y = int(xy[0]), int(xy[1])
    if not (0 <= x <= NORM and 0 <= y <= NORM): raise ValueError('box range')
    return x, y

FARA_KEY = dict(control='ctrl', ctrl='ctrl', shift='shift', alt='alt', meta='win', win='win', windows='win', enter='enter', escape='esc', esc='esc', tab='tab', backspace='backspace', delete='del', arrowup='up', arrowdown='down', arrowleft='left', arrowright='right', up='up', down='down', left='left', right='right', pageup='pageup', pagedown='pagedown', space='space', home='home', end='end', insert='insert')
FARA_KEY['return'] = 'enter'
FARA_KEY['del'] = 'del'

def fara_keys(names):
    out = []
    for name in names:
        key = FARA_KEY.get(str(name).strip().lower())
        if key is None and len(str(name)) == 1: key = str(name).lower()
        if key is None: raise ValueError(f'unknown {name}')
        out.append(key)
    if not out: raise ValueError('unknown key')
    return 'key ' + '+'.join(out)

def fara_wheel(pixels, span):
    pixels, span = int(pixels or 0), int(span or 0)
    return 0 if not pixels or span <= 0 else int(round(pixels * span / 1000 / 120.0)) * 120

def fara_open(url):
    url = str(url or '')
    if url.startswith(('https://', 'http://', 'file://', 'about:')): target = url
    elif ' ' in url: target = 'https://www.bing.com/search?q=' + urllib.parse.quote_plus(url) + '&FORM=QBLH'
    else: target = 'https://' + url
    return 'key ctrl+l\ntype ' + target + '\nkey enter'

def fara_program(arguments, size=(0, 0)):
    """Translate Fara's proposed action into a --hands program ('' when there is nothing to do)."""
    action = arguments.get('action')
    point = fara_point(arguments)
    if action in ('left_click', 'double_click', 'right_click', 'triple_click', 'mouse_move', 'left_click_drag') and point is None: raise ValueError('bad box')
    if action == 'left_click': return f'click {point[0]} {point[1]}'
    if action == 'double_click': return f'dclick {point[0]} {point[1]}'
    if action == 'right_click': return f'rclick {point[0]} {point[1]}'
    if action == 'triple_click': return f'tclick {point[0]} {point[1]}'
    if action == 'mouse_move': return f'move {point[0]} {point[1]}'
    if action == 'left_click_drag': return f"x, y = desktop.position()\ndesktop.drag([(x, y), ({point[0]}, {point[1]})])"
    if action == 'type': return 'type ' + str(arguments.get('text', ''))
    if action == 'key': return fara_keys(arguments.get('keys') or [])
    width, height = (list(size) + [0, 0])[:2]
    if action == 'scroll':
        amount = fara_wheel(arguments.get('pixels'), height)
        if not amount: return ''
        return f'wheel {amount}' + (f' {point[0]} {point[1]}' if point else '')
    if action == 'hscroll':
        amount = fara_wheel(arguments.get('pixels'), width)
        if not amount: return ''
        return f'hwheel {amount}' + (f' {point[0]} {point[1]}' if point else '')
    if action == 'wait': return f'wait {arguments.get("time") or 0}'
    if action == 'history_back': return 'key alt+left'
    if action == 'visit_url': return fara_open(arguments.get('url'))
    if action == 'web_search': return fara_open('https://www.bing.com/search?q=' + urllib.parse.quote_plus(str(arguments.get('query') or '')) + '&FORM=QBLH')
    return ''

def questions(blocks):
    """Parse --jev question blocks (see JEV_USAGE) into Jev's typed questions; every choice gains change_approach."""
    q = {}
    for i, block in enumerate(blocks):
        m = re.fullmatch(r'\s*([\w-]*)\s*([?#@])\s*(.+)', block, re.S)
        if not m: raise ValueError(JEV_USAGE)
        qid, kind, body = m.groups(); qid = qid or f'q{i+1}'
        ask, *opts = [s.strip() for s in body.split('|')]
        if not ask or (kind=='#' and len(opts)<SCORE_MIN_LEVELS) or (kind=='?' and len(opts)==1) or (kind=='@' and opts): raise ValueError(JEV_USAGE)
        if kind=='@': q[qid] = dict(type='box', instructions=ask)
        elif kind=='#': q[qid] = dict(type='score', instructions=ask, criteria=opts)
        elif opts:
            criteria = dict(o.split('=',1) if '=' in o else (f'opt{j+1}', o) for j,o in enumerate(opts))
            if kind=='?' and set(map(str.lower, criteria))=={'true','false'}:
                q[qid] = dict(type='noul', instructions=ask, criteria={k.lower():v for k,v in criteria.items()})
            else:
                q[qid] = dict(type='choice', instructions=ask, criteria=criteria | ({'change_approach': CHANGE_APPROACH} if 'change_approach' not in criteria else {}))
        else: q[qid] = dict(type='noul', instructions=ask)
    if not q: raise ValueError(JEV_USAGE)
    return q

def module(name, path):
    """Import a Python file as a module."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

def HAND():
    """The win32 hand module, loaded and bound once."""
    mod = getattr(HAND, 'mod', None)
    if mod: return mod
    mod = module('trident_hand', ROOT / 'win32.py'); mod._setup_bindings()
    HAND.mod = mod; return mod

def digest(path):
    """A compact summary of one journal: run, time span, sentence, tool calls, errors, start of the final message."""
    s = path.read_text('utf-8', 'replace')
    heads = re.findall(r'(?m)^## (\d\d:\d\d:\d\d) (\S+)(?: (\S+))?$', s)
    said = re.findall(r'(?ms)^## \S+ say\n\n(.*?)(?=^## |\Z)', s)
    sentence = re.search(r'(?ms)^SENTENCE\n\n(.*?)(?=^## |\Z)', s)
    return dict(run=path.stem, span=[heads[0][0], heads[-1][0]] if heads else [], sentence=sentence.group(1).strip()[:RECALL_CHARS] if sentence else '', calls=dict(Counter(n for _, k, n in heads if k == 'request' and n)), errors=sum(k == 'err' for _, k, _ in heads), final=said[-1].strip()[:RECALL_CHARS] if said else '')

@dataclass(frozen=True)
class Oracle:
    """A JSON POST endpoint whose requests, responses and errors are journaled."""
    url: str
    timeout: float | None = None
    def post(self, payload, shown):
        log('req', dict(url=self.url) | shown)
        try:
            req = urllib.request.Request(self.url, json.dumps(payload).encode(), {'Content-Type':'application/json'} | self.headers)
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                response = json.load(r)
        except Exception as e:
            log('err', dict(url=self.url, error=repr(e)))
            raise
        log('resp', dict(url=self.url, body=response))
        return response

class Vision(Oracle):
    """Retina: Fara on llama.cpp's OpenAI-compatible chat endpoint."""
    headers = {}
    def ask(self, text, image, origin):
        """One fresh question about one image; returns Fara's raw reply (thought, then one tool call)."""
        messages = [dict(role='system', content=FARA_SYSTEM), dict(role='user', content=[dict(type='image_url', image_url=dict(url=b64(image))), dict(type='text', text=text)])]
        response = self.post(dict(model=VISION_MODEL, temperature=RETINA_TEMPERATURE, max_tokens=FARA_MAX_TOKENS, chat_template_kwargs=dict(enable_thinking=False), messages=messages), dict(question=text, image=origin, sent_size=list(image.size)))
        choice = response['choices'][0]; message = choice['message']
        log('eye', dict(timings=response.get('timings'), finish=choice.get('finish_reason'), sent_size=list(image.size)))
        if choice.get('finish_reason') == 'length': raise ValueError(f'reply cut at {FARA_MAX_TOKENS} tokens')
        content = (message.get('content') or '').strip()
        reasoning = (message.get('reasoning_content') or '').strip()
        if reasoning and reasoning not in content: content = f'{reasoning}\n{content}'.strip()
        if not content: raise ValueError('Fara tool_call expected')
        return content

class Decision(Oracle):
    """Jev on OpenRouter's Decisions API; the key is read from ../.env."""
    headers = property(lambda self: {'Authorization': 'Bearer ' + (ROOT.parent / '.env').read_text('utf-8-sig').strip().strip('"\'')})
    def ask(self, state, questions, shown):
        return self.post(dict(model=DECISION_MODEL, state=state, questions=questions), dict(state=shown, questions=questions))

class Interrupted(BaseException):
    """Raised by Hand.check to stop a hand program; a BaseException so a bare except in a program cannot swallow it."""

class Hand:
    """The desktop API of --hands. Points are 0-1000 of the region; every act first calls check()."""
    def __init__(self, work): self.work = work; self.arm(0.0)
    region = property(lambda self: (self.work / 'region.txt').read_text('utf-8').strip())
    w = property(lambda self: HAND())
    def arm(self, since):
        """Start a program: alerts at or before since (the last capture) count as seen."""
        self.since, self.acked, self.expects, self.seen = since, set(), [], []
    def full(self, x, y):
        """Region point to full-screen 0-1000, the scale of the watch's alert boxes."""
        l, t, r, b = (0, 0, NORM, NORM) if self.region == 'NONE' else map(int, self.region.split(','))
        return l + float(x) * (r - l) / NORM, t + float(y) * (b - t) / NORM
    def expect(self, l, t, r, b, seconds=SCAN_EXPECT):
        """Declare that your next acts will change this region box for the next seconds."""
        now, box = math.floor(time.time() * 1000) / 1000, [*self.full(l, t), *self.full(r, b)]
        self.expects.append(dict(box=box, at=now, until=now + float(seconds))); return dict(expect=box)
    def check(self, x=None, y=None):
        """Raise Interrupted for an unseen alert unless it is expected or contains the act's point."""
        point = None if x is None else self.full(x, y)
        for alert in scan_state(self.work).get('alerts') or []:
            if alert['at'] <= self.since or alert['at'] in self.acked: continue
            middle = ((alert['box'][0] + alert['box'][2]) / 2, (alert['box'][1] + alert['box'][3]) / 2)
            how = 'expected' if any(e['at'] <= alert['at'] <= e['until'] and inside(middle, e['box'], SCAN_PAD) for e in self.expects) else 'inside' if point and inside(point, alert['box'], SCAN_PAD) else None
            if not how: raise Interrupted(alert)
            self.acked.add(alert['at']); self.seen.append(alert | dict(cleared=how))
    def _pair(self, x, y): return f'{int(round(float(x)))},{int(round(float(y)))}'
    def position(self): return tuple(map(int, self.w._do_cursor_pos(self.region).split(',')))
    def moveTo(self, x, y, duration=0):
        self.check(x, y)
        px, py = self.w._resolve_screen_pos(*map(int, self._pair(x,y).split(',')), self.region)
        self.w._move_cursor(px, py); return dict(at=[int(round(float(x))), int(round(float(y)))])
    def click(self, x, y, button='left', clicks=1):
        self.check(x, y)
        pair = self._pair(x, y)
        if button=='right': self.w._do_right_click(pair, self.region)
        elif clicks==DOUBLE_CLICKS: self.w._do_double_click(pair, self.region)
        elif clicks>DOUBLE_CLICKS:
            for _ in range(clicks): self.w._do_click(pair, self.region)
        else: self.w._do_click(pair, self.region)
        return dict(clicked=[int(round(float(x))), int(round(float(y)))], button=button, clicks=clicks)
    def rightClick(self, x, y): return self.click(x, y, 'right')
    def doubleClick(self, x, y): return self.click(x, y, clicks=DOUBLE_CLICKS)
    def drag(self, points, duration=DRAG_DURATION):
        if len(points)<DRAG_MIN_POINTS: raise ValueError('drag needs >=2 points')
        self.check(*points[0])
        self.w._do_stroke([self._pair(*p) for p in points], self.region)
        return dict(dragged=len(points))
    def key(self, combo):
        self.check()
        self.w._do_hotkey(str(combo)); return dict(keys=str(combo).replace(' ','').lower().split('+'))
    def type_text(self, text): self.check(); self.w._do_type_text(str(text)); return dict(typed=len(str(text)))
    def set_clipboard(self, text):
        subprocess.run(POWERSHELL + ['[Console]::InputEncoding=[Text.UTF8Encoding]::new(); Set-Clipboard -Value ([Console]::In.ReadToEnd())'], input=str(text).encode('utf-8'), capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        return dict(clipboard=len(str(text)))
    def paste(self, text): self.set_clipboard(text); return self.key('ctrl+v')
    def scroll(self, clicks, x=None, y=None):
        if x is None: x, y = self.position()
        self.check(x, y)
        n = int(clicks); self.w._do_scroll(self._pair(x,y), self.region, SCROLL_UP if n>0 else -SCROLL_UP, abs(n)); return dict(scrolled=n)
    def wheel(self, amount, x=None, y=None, horizontal=False):
        if x is None: x, y = self.position()
        self.check(x, y)
        self.w._do_wheel(self._pair(x, y), self.region, int(amount), horizontal); return dict(wheel=int(amount), horizontal=horizontal)

@dataclass
class Engine:
    """Trident's tools. Every public method is a --flag and its docstring is shown to the mind."""
    work: Path
    fresh: InitVar[bool] = False
    vision = Vision(VISION_URL + '/v1/chat/completions', FARA_TIMEOUT)
    decision = Decision(DECISION_URL)
    story_path = property(lambda self: self.work / 'story.txt')
    ledger_path = property(lambda self: self.work / 'ledger.md')
    edit_log = property(lambda self: self.work / 'edit_log.txt')
    goal = property(lambda self: self.work / 'goal.txt')
    result = property(lambda self: self.work / 'result.json')
    sentinel = property(lambda self: self.work / 'stop')
    screen = property(lambda self: read('screen.json'))

    def __post_init__(self, fresh):
        self.work.mkdir(parents=True, exist_ok=True)
        os.chdir(self.work)
        if fresh:
            for f in (self.story_path, self.ledger_path, self.edit_log, self.goal, self.result, self.sentinel, Path('look.json'), Path('region.txt')):
                f.unlink(missing_ok=True)
        self.story_path.touch()
        self.ledger_path.touch()
        self.edit_log.touch()
        self.goal.touch()
        self.result.exists() or write(self.result, {})
        Path('look.json').exists() or write('look.json', None)
        Path('region.txt').exists() or text('region.txt', 'NONE')

    desktop = cached_property(lambda self: Hand(self.work))

    def _log_edit(self, path, note=''):
        with open(self.edit_log, 'a', encoding='utf-8') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {path} {note}\n")

    def region(self, value=''):
        if not value:
            value, code = HAND()._do_select_region()
            if code: raise SystemExit(code)
        elif value != 'NONE':
            box = list(map(int, value.split(','))) if re.fullmatch(r'\d+,\d+,\d+,\d+', value) else []
            if not (len(box) == 4 and box[0] < box[2] <= NORM and box[1] < box[3] <= NORM): raise SystemExit(f'region is NONE or left,top,right,bottom on 0-{NORM}')
        text(self.work / 'region.txt', value)
        return dict(region=value)

    def capture(self, *_):
        region = (self.work / 'region.txt').read_text('utf-8').strip()
        png = HAND()._do_capture(region, CAPTURE_NATIVE, CAPTURE_NATIVE, CAPTURE_SCALE)
        shots = self.work / 'shots'; shots.mkdir(exist_ok=True)
        sid = time.strftime('%H%M%S') + f'{time.time_ns() // SHOT_MS % SHOT_MS:03d}'
        path = shots / f'{sid}.png'; path.write_bytes(png)
        with Image.open(io.BytesIO(png)) as im: size = list(im.size)
        v = dict(id=sid, image=str(path), size=size, region=region, at=round(time.time(), CLOCK_PLACES))
        write('screen.json', v); return v

    def scan(self, *_):
        py = self.work / '.venv' / 'Scripts' / 'pythonw.exe'
        if not py.is_file(): raise SystemExit('scan needs .venv')
        state, err = scan_state(self.work), self.work / 'scan.err'
        crash = dict(err=err.read_text('utf-8')[-2000:]) if err.exists() else {}
        if time.time() - float(state.get('at') or 0) > SCAN_STALE:
            (self.work / 'scan.stop').unlink(missing_ok=True)
            subprocess.Popen([str(py), '-B', str(self.work / 'scan.py')], cwd=str(self.work), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=DETACHED, close_fds=True)
            for _ in range(40):
                time.sleep(0.25)
                state = scan_state(self.work)
                if time.time() - float(state.get('at') or 0) <= SCAN_STALE: break
        return {k: state.get(k) for k in SCAN_KEYS} | crash

    def _unscan(self):
        pid = self.work / 'scan.pid'
        if not pid.exists(): return
        (self.work / 'scan.stop').touch()
        deadline = time.time() + SCAN_STOP_WAIT
        while pid.exists() and time.time() < deadline: time.sleep(HALT_POLL)
        log('scan', dict(stopped=not pid.exists()))

    def windows(self, *_):
        return dict(windows=[dict(zip(('hwnd', 'title', 'box', 'pid', 'minimized', 'topmost', 'foreground'), item)) for item in HAND()._do_window_list()])

    def _shot(self):
        screen = self.screen
        with Image.open(screen['image']) as im: return screen, fara_fit(im.convert('RGB'))

    def _eye(self, screen, image, text):
        raw = self.vision.ask(text, image, screen['image'])
        thoughts, call = fara_call(raw)
        arguments = call['arguments']
        if arguments.get('action') not in FARA_ACTIONS: raise ValueError('unknown action')
        point = fara_point(arguments)
        program = fara_program(arguments, screen.get('size') or (0, 0))
        if thoughts and 'thoughts' not in arguments: arguments['thoughts'] = thoughts
        return dict(capture=screen['id'], region=screen['region'], thoughts=thoughts, action=arguments.get('action'), arguments=arguments, coordinate=list(point) if point else None, program=program)

    def retina(self, question=''):
        if not str(question).strip(): raise ValueError('retina needs a question')
        screen, image = self._shot()
        v = dict(task=str(question).strip()) | self._eye(screen, image, FARA_LOOK + str(question).strip())
        write('look.json', v); return v

    def jev(self, narrative, *blocks):
        if not narrative.strip() or not blocks: raise ValueError(JEV_USAGE)
        q = questions(blocks)
        state = ['STORY: ' + self.story_path.read_text('utf-8'), 'LEDGER: ' + self.ledger_path.read_text('utf-8'), 'WHY: ' + narrative, 'GOAL: ' + self.goal.read_text('utf-8')]
        shown = list(state)
        sx = sy = 1
        if any(item['type']=='box' for item in q.values()):
            screen = self.screen
            with Image.open(screen['image']) as im: image, sx, sy = jev_fit(im.convert('RGB'))
            state.append(dict(type='image_url', image_url=dict(url=b64(image))))
            shown.append(screen['image'])
        r = self.decision.ask(state, q, shown)
        told = []
        for name, a in r.get('answers', {}).items():
            if a['type']=='box': a['box'] = {k: min(1, v * (sx if k in ('left', 'right') else sy)) for k, v in a['box'].items()}
            if a['type']=='choice': told.append(f"{name}: {a['choice']} ({', '.join(f'{k} {v:.{DECIMAL_PLACES}f}' for k,v in a['probabilities'].items())}; conf {a['confidence']:.{DECIMAL_PLACES}f})")
            elif a['type']=='noul': told.append(f"{name}: noul {a['noul']:.{DECIMAL_PLACES}f}")
            elif a['type']=='box':
                b = a['box']
                told.append(f"{name}: box {int(round(b['left']*NORM))} {int(round(b['top']*NORM))} {int(round(b['right']*NORM))} {int(round(b['bottom']*NORM))} conf {a['confidence']:.{DECIMAL_PLACES}f}")
            else: told.append(f"{name}: score {a['score']:.{DECIMAL_PLACES}f}")
        return r | dict(by='jev', summary='\n'.join(told))

    def _gestures(self, src):
        d = self.desktop
        for line in filter(None, map(str.strip, src.splitlines())):
            verb, _, rest = line.partition(' ')
            if verb in ('type','paste'):
                (d.type_text if verb=='type' else d.paste)(rest); continue
            for cmd in filter(None, map(str.strip, line.split(';'))):
                verb, _, rest = cmd.partition(' ')
                if verb=='key': d.key(rest)
                elif verb=='wait': time.sleep(max(0, float(rest)))
                elif verb=='expect': d.expect(*map(float, rest.split()))
                elif verb=='scroll':
                    n, *at = rest.split(); d.scroll(int(n), *map(float, at))
                elif verb in ('wheel', 'hwheel'):
                    n, *at = rest.split(); d.wheel(int(n), *map(float, at), horizontal=verb=='hwheel')
                elif verb=='tclick':
                    nums = list(map(float, rest.split()))
                    if len(nums)!=PAIR: raise ValueError(f'{verb} needs 0-{NORM} pairs')
                    d.click(*nums, clicks=3)
                elif verb in ('click','rclick','dclick','move','drag'):
                    nums = list(map(float, rest.split()))
                    if len(nums)%PAIR or (len(nums)<DRAG_MIN_POINTS*PAIR if verb=='drag' else len(nums)!=PAIR): raise ValueError(f'{verb} needs 0-{NORM} pairs')
                    pts = list(zip(nums[::2], nums[1::2]))
                    d.drag(pts) if verb=='drag' else getattr(d, dict(click='click', rclick='rightClick', dclick='doubleClick', move='moveTo')[verb])(*pts[0])
                else: raise ValueError(f'unknown {verb}')

    def hands(self, src):
        d, stopped = self.desktop, None
        state = scan_state(self.work)
        watch = 'off' if not state else 'live' if time.time() - float(state.get('at') or 0) <= SCAN_STALE else 'stale'
        d.arm(float(read('screen.json').get('at') or 0) if watch == 'live' and Path('screen.json').exists() else 0.0 if watch == 'live' else time.time())
        with redirect_stdout(io.StringIO()) as out:
            head = src.split(None, 1)[0] if src.strip() else ''
            try:
                if head in GESTURES: self._gestures(src)
                elif head: exec(compile(src, '<hands>', 'exec'), dict(desktop=d, read=read, time=time, json=json, Path=Path))
            except Interrupted as e: stopped = e.args[0]
        time.sleep(SCREENSHOT_DELAY)
        v = self.capture() | dict(stdout=out.getvalue(), watch=watch, cleared=d.seen)
        if stopped: v['interrupted'] = stopped
        write('screen.json', v); return v

    def story(self, narrative):
        text(self.story_path, narrative)
        self._log_edit('story.txt', 'rewrite')
        return dict(story_len=len(narrative))

    def ledger(self, lesson):
        if not E('TRIDENT_START'): raise SystemExit('the ledger is written inside a wake')
        lesson = WAKE.sub('', str(lesson)).strip()
        if not 0 < len(lesson) <= LEDGER_LESSON: raise ValueError(f'a lesson has 1-{LEDGER_LESSON} characters; write it shorter')
        start = float(E('TRIDENT_START'))
        self._enter(start, (float(E('TRIDENT_END')) - start) / 60 if E('TRIDENT_END') else '', 'open', lesson)
        return dict(wakes=len(self._wakes()), lesson_len=len(lesson))

    def _wakes(self): return WAKE.split(self.ledger_path.read_text('utf-8'))[1:]

    def _enter(self, start, minutes, state, lesson):
        wakes = [w for w in self._wakes() if w.split(None, 1)[0] != WAKE_ID]
        budget = f' of {float(minutes):g}' if minutes else ''
        wakes.append(f"{WAKE_ID} {time.strftime('%Y-%m-%d %H:%M', time.localtime(start))} used {(time.time() - start) / 60:.0f}{budget} min, {state}\n{lesson}\n")
        while len(wakes) > LEDGER_WAKES or sum(map(len, wakes)) > LEDGER_CHARS: del wakes[0]
        text(self.ledger_path, ''.join('## wake ' + w for w in wakes))

    def _seal(self, start, minutes):
        lesson = next((w.split('\n', 1)[1].strip() for w in self._wakes() if w.split(None, 1)[0] == WAKE_ID), '') or 'No lesson was written.'
        self._enter(start, minutes, HALT.get('reason') or ('stopped' if self.sentinel.exists() else 'ended'), lesson)

    def recall(self, *_):
        return dict(wakes=[digest(p) for p in sorted(ROOT.glob('run_*.md')) if p != RUN][-RECALL_RUNS:])

    def stop(self, *_):
        self.sentinel.touch()
        log('file', dict(path=str(self.sentinel), text=''))
        sys.exit(0)

    def _emit(self, name, v):
        v = v | clock()
        write(self.result, v)
        if sys.stdout.isatty(): log(name, v)
        else: print(json.dumps(v, ensure_ascii=False), flush=True)

    def _mind_prompt(self, sentence, minutes, end):
        call = f"& '{PYTHON}' '{SCRIPT}' --work '{self.work}'"
        docs = [f.__doc__.replace('$CALL', call) for n, f in tools().items() if n != 'mind' and f.__doc__]
        budget = f"The wake has {minutes} minutes and ends at {time.strftime('%H:%M', time.localtime(end))}." if minutes else 'No budget was given here; if the sentence names one, keep it.'
        return '\n\n'.join((MIND.format(budget=budget, norm=NORM), 'LEDGER', self.ledger_path.read_text('utf-8').strip() or 'This is the first wake.', 'TOOLS', *docs, 'SENTENCE', str(sentence)))

    def mind(self, sentence='', minutes=MINUTES):
        start = time.time(); end = start + float(minutes) * 60 if minutes else None
        self.sentinel.unlink(missing_ok=True)
        if str(sentence).strip(): text(self.goal, str(sentence).strip())
        prompt_file = self.work / 'prompt.txt'
        text(prompt_file, self._mind_prompt(sentence, minutes, end))
        vendor = self.work / 'agent'
        profile = vendor / 'home/.cursor'
        profile.mkdir(parents=True, exist_ok=True)
        for kind in ('Local', 'Roaming'): (vendor / 'home/AppData' / kind).mkdir(parents=True, exist_ok=True)
        temp = ROOT.parent / 'temp'
        temp.mkdir(exist_ok=True)
        env = os.environ | dict(HOME=str(vendor / 'home'), USERPROFILE=str(vendor / 'home'), CURSOR_CONFIG_DIR=str(profile), CURSOR_DATA_DIR=str(vendor / 'data'), TEMP=str(temp), TMP=str(temp), PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', TRIDENT_START=str(start), TRIDENT_END=str(end or ''))
        env.pop('OPENROUTER_API_KEY', None)
        write(profile / 'cli-config.json', CLI_CONFIG)
        folder = max(p for p in (Path(os.environ['LOCALAPPDATA']) / 'cursor-agent/versions').iterdir() if p.name[:1].isdigit() and (p / 'index.js').exists())
        boot = "process.argv.push(require('node:fs').readFileSync(process.argv.pop(),'utf8'));require(process.argv[1]);"
        with ExitStack() as stack:
            stack.callback(self._seal, start, minutes)
            p = subprocess.Popen([str(folder / 'node.exe'), '-e', boot, str(folder / 'index.js'), '--trust', '--force', '-p', '--model', 'grok-4.7-xhigh', '--workspace', str(ROOT), '--output-format', 'stream-json', str(prompt_file)], cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
            LIVE['agent'] = p
            stack.callback(p.wait)
            stack.callback(p.kill)
            job = win32job.CreateJobObject(None, '')
            limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
            limits['BasicLimitInformation']['LimitFlags'] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
            win32job.AssignProcessToJobObject(job, int(p._handle))
            stack.callback(job.Close)
            stack.callback(self._unscan)
            def watch():
                while p.poll() is None and not self.sentinel.exists(): time.sleep(HALT_POLL)
                p.kill()
            threading.Thread(target=watch, daemon=True).start()
            for raw in p.stdout:
                line = raw.decode('utf-8', 'replace').rstrip()
                log('agent', line)
                try: finished = json.loads(line).get('type') == 'result'
                except json.JSONDecodeError: finished = False
                if finished: p.kill(); break


Engine.region.__doc__ = f'''$CALL --region [NONE|LEFT,TOP,RIGHT,BOTTOM]
  Sets the rectangle that --capture, --retina and --hands work in, on the 0-{NORM} full-screen scale. NONE is the full screen (the default). With no value a selector opens for the owner: drag a rectangle, right-click for the full screen, Escape cancels.
  Returns {{region}}. Use it to zoom the eye on a small area; points in --hands are then 0-{NORM} of that rectangle.'''
Engine.capture.__doc__ = '''$CALL --capture
  Takes a screenshot of the region into shots/<id>.png and writes screen.json.
  Returns {id, image, size, region, at}. Capture before you judge the screen and after you act; the watch treats every change before the last capture as seen. You may read the image file yourself.'''
Engine.scan.__doc__ = f'''$CALL --scan
  Starts the pixel watch if it is not running (a windowless .venv pythonw process that lives until the wake ends) and returns its state.
  Returns {{kind: warming|live, grid, samples, rhythmic: boxes that blink by themselves, alerts: the last 16 {{box, cells, at}} where a quiet area changed, at, pid, skipped, err: the last crash if any}}. Boxes are 0-{NORM} of the full screen. Start it before you use the hand.'''
Engine.windows.__doc__ = f'''$CALL --windows
  Lists every visible, uncloaked, titled top-level window, top of the z-order first.
  Returns {{windows: [{{hwnd, title, box (0-{NORM} of the screen), pid, minimized, topmost, foreground}}]}}. Use it to corroborate what Retina sees, above all a window it may miss behind or over another.'''
Engine.retina.__doc__ = f'''$CALL --retina "QUESTION"
  Retina is Fara, a vision model, asked one fresh question about the latest capture (no memory of earlier questions). It first names every window, dialog and overlay it sees, then answers and proposes one action.
  Returns {{task, capture, region, thoughts, action, arguments, coordinate (0-{NORM} of the capture), program}}; program is a --hands program for that action, yours to run, change or ignore. Use it for any image analysis: one concrete question per call is the fastest and most precise.'''
Engine.jev.__doc__ = '''$CALL ''' + JEV_USAGE + '''
  Asks Jev, a decision model, typed questions. Jev also receives story.txt, ledger.md and goal.txt; a box question also receives the current capture.
  Returns Jev's answers with probabilities and confidence, and a summary line per question; a box is left top right bottom on the 0-1000 scale of the capture. Every choice includes change_approach: keep the goal and switch strategy. The numbers are evidence; you choose the act.'''
Engine.hands.__doc__ = f'''$CALL --hands "PROGRAM"
  Runs a program on the desktop, then waits and captures. PROGRAM is either gesture lines (click x y, rclick x y, dclick x y, tclick x y, move x y, drag x y x y ..., scroll n [x y], wheel n [x y], hwheel n [x y], key ctrl+s, type TEXT, paste TEXT, wait SECONDS, expect l t r b [SECONDS]; ; joins commands on one line) or Python with desktop (click, moveTo, drag, key, type_text, paste, scroll, wheel, position, expect), read, time, json and Path.
  Points are 0-{NORM} of the region. Every act first checks the watch: a quiet area that changed since your last capture stops the program, unless the act's point is inside it or an expect declared it.
  Returns the capture plus {{stdout, watch: live|stale|off, cleared, interrupted}}; interrupted is the alert that stopped the program.'''
Engine.story.__doc__ = '''$CALL --story "TEXT"
  Replaces story.txt with TEXT exactly: your living narrative and checklist, read by you and by Jev in this and the next wake.
  Returns {story_len}. Keep it current; shorten it by rewriting.'''
Engine.ledger.__doc__ = f'''$CALL --ledger "LESSON"
  Writes this wake's lesson into ledger.md (1-{LEDGER_LESSON} characters; a later call replaces it): what worked, what failed, what to do first next time. At wake end Trident adds the minutes used and how the wake ended, keeps the last {LEDGER_WAKES} wakes within {LEDGER_CHARS} characters, and shows the ledger to the next wake and to Jev.
  Returns {{wakes, lesson_len}}. Call it at the end of every wake.'''
Engine.recall.__doc__ = f'''$CALL --recall
  Digests the journals of up to {RECALL_RUNS} earlier wakes, never this one.
  Returns {{wakes: [{{run, span, sentence, calls (requests per tool), errors, final (start of the final message)}}]}}. Use it when the ledger is not enough.'''
Engine.stop.__doc__ = '''$CALL --stop
  Ends the wake: writes the stop file, so the mind is stopped, the watch ends and the ledger is sealed. Use it when the sentence is done or cannot be done.'''
Engine.mind.__doc__ = '''$CALL --mind "SENTENCE" [MINUTES]
  Wakes the mind on SENTENCE with an optional budget in minutes (or TRIDENT_MINUTES). Writes goal.txt and prompt.txt, runs the Cursor CLI in a job, journals it, and at the end stops the watch and seals the ledger.'''
MIND = '''You are Trident, a living program. Your body is this directory: main.py holds your tools, win32.py your hand, scan.py your watch, skills/ the tools you grew, story.txt and ledger.md your memory. The owner sentence below is the wake. You look, consult Jev, act with the hand, write story.txt, edit your source, or grow a skill and bring it into main.py when it is what you are. The body lives here; act wherever the sentence requires (in the body, elsewhere on this computer, on the screen or on the web) and report every place outside the body you changed. Call your tools with the commands listed below, exactly as written.

WORK IN SHORT LOOPS. Make your first tool call before any long reasoning. Think in short steps that each end in a tool call: probe, measure, adjust. A plan is not an action; run it.

TIME. {budget} Every tool result carries clock.used, and clock.left when the wake has a budget. With a budget, at half time compare the checklist with what is done and shrink what cannot finish; with a fifth left start nothing new: verify, report and write the ledger.

MEMORY. The LEDGER below holds the lessons of earlier wakes, newest last. Read it before you plan; do not repeat what it says failed, and start with what it says to do first. --recall digests the journals of earlier wakes. Never read run_*.md yourself: the journal of this wake only echoes you.

LOOK FIRST. Before you plan, observe what the sentence is about: files, tests and command output for work that lives in files; --scan, --capture, --retina and --windows for the screen. Know the state from what you observed in this wake, not from memory or expectation.

CHECKLIST. Split the sentence into numbered deliverables, one line each, and keep them at the top of story.txt. A deliverable is met only when evidence observed in this wake shows it: a command output, a test, a file, or a capture. One you cannot meet stays not met; never redefine it into something easier.

EYES. Retina (--retina) is Fara, a vision model: it names what is on the latest capture and where, and proposes one action for the hand. It can miss a window that hides or overlaps another and can invent items; before you act on its answer, corroborate it with --windows and, when it matters, by reading the capture yourself. Your captures in shots/ are images you may read. Points for the hand come from Retina or from a Jev box, on the 0–{norm} scale of the capture.

JEV. Jev (--jev) is typesafe/jev-1.14, a decision model on OpenRouter. It receives story.txt, ledger.md, goal.txt and the narrative you pass, and the current capture when a question asks for a box. Consult him before every decision that matters: when two or more ways forward remain, when you hold coordinates of screen items in text and need to know what they mean, when you need a box around a target, and before you claim a state is reached. Describe your confusion and list every way you consider, neutrally, with no conclusion in the narrative. ? asks a yes/no claim or a choice, # a score, @ a box on the current capture as left top right bottom on the 0–{norm} scale. Every choice includes change_approach; when he picks it, keep the goal and switch strategy. His numbers are evidence, not orders: you choose the act, and when you act against his answer you write why in story.txt.

THE HAND AND THE WATCH. --scan watches the pixels while the wake lives; Trident stops it when the wake ends, so never report it as running after that. Every hand action, gesture or Python, is checked against the watch. Before an act that changes the screen away from the point you act on, such as a palette, picker, menu, dialog or the text you type, declare that area with expect left top right bottom in the same program. Never route around the watch; if it stops you, capture and look.

EVOLVE. You may change your own source. When a tool fails you, fix the tool or grow a skill instead of working around it; a workaround that stays outside the body is lost at the next wake. A skill is skills/NAME.py beside main.py, defining NAME(engine, *args) with a docstring that starts with $CALL --NAME, says what it takes and returns and when to use it. From the next call it is the tool --NAME, listed with the others. Keep the body small: remove what no call reaches and merge what is written twice.

HONESTY. Report what you observed, not what you expect. Never call something validated, ready or running without evidence of this wake. When you bend a rule, route around a tool or change something outside the body, say so in story.txt and in the final report.

FINISH. Before the final message observe again the way you looked first and check every checklist line against it. The final message and story.txt give three layers: the output (each deliverable met, partial or not met, with its evidence), the trajectory (tools you routed around, rules you bent, places outside the body you changed), and the task (minutes used, against the budget when there is one). Then write the lesson with --ledger.

The ledger and your tools follow.'''

def tools():
    """Every public Engine method by name: the --flags, grown skills included."""
    return {n: f for n, f in vars(Engine).items() if callable(f) and n[0] != '_'}

def grow(folder):
    """Load each folder/NAME.py and add its NAME(engine, *args) to Engine as the tool --NAME."""
    for path in sorted(Path(folder).glob('*.py')):
        if path.stem in vars(Engine): raise SystemExit(f'skill {path.stem} shadows a tool')
        setattr(Engine, path.stem, getattr(module('skill_' + path.stem, path), path.stem))

grow(ROOT / 'skills')
HALT, LIVE = {}, {}

def note_halt(reason):
    if HALT.get('reason'): return
    HALT['reason'] = reason
    agent = LIVE.get('agent')
    if agent is not None and agent.poll() is None:
        try: agent.kill()
        except Exception: pass
    try: log('stopped', dict(reason=reason))
    except Exception: pass

def _on_signal(signum, _frame):
    note_halt('ctrl+c' if signum == signal.SIGINT else 'ctrl+break')
    raise KeyboardInterrupt

def install_halt():
    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, 'SIGBREAK'): signal.signal(signal.SIGBREAK, _on_signal)

def main():
    ctypes.WinDLL('shcore', use_last_error=True).SetProcessDpiAwareness(DPI_AWARENESS)
    install_halt()
    parser = argparse.ArgumentParser(add_help=False)
    group = parser.add_mutually_exclusive_group()
    for name in tools(): group.add_argument('--' + name, dest='entry', action='store_const', const=name)
    for flag in ('--fresh', '--journal'): parser.add_argument(flag, action='store_true')
    parser.add_argument('--work', default=str(ROOT))
    parser.add_argument('args', nargs='*')
    a = parser.parse_args()
    if a.journal:
        journal_serve()
        return
    if not a.entry: parser.error('need a tool flag')
    serve()
    started, engine = time.time(), None
    atexit.register(lambda: log('exit', dict(argv=sys.argv[1:], seconds=round(time.time()-started, EXIT_SECONDS), reason=HALT.get('reason') or 'ended')))
    try:
        engine = Engine(Path(a.work).resolve(), a.fresh)
        if engine.sentinel.exists() and a.entry not in ('stop', 'mind'):
            log('stopped', dict(text='stopped'))
            sys.exit(1)
        result = getattr(engine, a.entry)(*a.args)
        if result is not None: engine._emit(a.entry, result)
    except KeyboardInterrupt:
        note_halt('ctrl+c')
        sys.exit(INTERRUPTED_EXIT)

if __name__ == '__main__':
    main()
