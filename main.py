import argparse, ast, atexit, base64, importlib.util, io, json, math, msvcrt, os, re, signal, socket, subprocess, sys, threading, time, urllib.parse, urllib.request, uuid
from collections import Counter
from contextlib import ExitStack, redirect_stdout
from dataclasses import InitVar, dataclass
from functools import cached_property
from pathlib import Path

sys.dont_write_bytecode = True
import win32

SCRIPT, ROOT, PYTHON = Path(__file__).resolve(), Path(__file__).resolve().parent, Path(sys.executable)
NVIDIA_HOST = '192.168.16.31'
SETTINGS = {'vision-url': f'http://{NVIDIA_HOST}:1234', 'vision-model': 'retina', 'fara-width': 1440, 'fara-max-tokens': 2048, 'fara-zoom': 250, 'shot-delay': 1.5,
            'jev-model': 'typesafe/jev-1.14-20261014', 'mind-model': '', 'minutes': '', 'pilot-autoreply': False,
            'run': time.strftime('%Y%m%d_%H%M%S'), 'wake-start': '', 'wake-end': '', 'retina-pin': ''}
OPTIONS = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
for name, default in SETTINGS.items():
    OPTIONS.add_argument('--' + name, **(dict(action='store_true') if default is False else dict(type=type(default), default=default)))
KNOWN = vars(OPTIONS.parse_known_args()[0])
SET = {name: KNOWN[name.replace('-', '_')] for name in SETTINGS}
DECISION_MODEL = SET['jev-model']
DECISION_URL = 'https://openrouter.ai/api/alpha/decisions'
VISION_URL = SET['vision-url']
VISION_MODEL = SET['vision-model']
BRAIN = 'grok-4.7-medium-fast'
BRAINS = ('grok-4.7-low', 'grok-4.7-low-fast', 'grok-4.7-medium', 'grok-4.7-medium-fast', 'grok-4.7-high', 'grok-4.7-high-fast', 'grok-4.7-xhigh', 'grok-4.7-xhigh-fast')
RUN = ROOT / ('run_' + SET['run'] + '.md')
NORM, SCREENSHOT_DELAY = 1000, SET['shot-delay']
FARA_FACTOR = 32
FARA_MIN_PIXELS, FARA_MAX_PIXELS = 256 * FARA_FACTOR ** 2, 4096 * FARA_FACTOR ** 2
FARA_TARGET_WIDTH = SET['fara-width']
FARA_MAX_TOKENS, FARA_TIMEOUT = SET['fara-max-tokens'], 180
ZOOM_SPAN, POINTER = SET['fara-zoom'], ('left_click', 'double_click', 'right_click', 'triple_click', 'mouse_move', 'left_click_drag')
JOURNAL_HOST, JOURNAL_PORT = '0.0.0.0', 8765
DETACHED = 0x00000008 | 0x00000200 | 0x08000000
MINUTES, WAKE_START, WAKE_END = SET['minutes'], SET['wake-start'], SET['wake-end']
LEDGER_WAKES, LEDGER_CHARS, LEDGER_LESSON = 12, 8000, 1200
RECALL_RUNS, RECALL_CHARS = 5, 800
WAKE, WAKE_ID = re.compile(r'(?m)^## wake '), SET['run']
JEV_USAGE = '--jev "NARRATIVE" "QUESTION" ["QUESTION" ...], each QUESTION one of: "name?Claim" (yes/no), "name?Q|true=Yes|false=No", "name?Q|a=Option|b=Option" (choice), "name#Q|Level1|Level2" (score), "name@target in the current capture" (box)'
CHANGE_APPROACH = 'Keep the goal. This way is not working. Switch to a different strategy and continue.'
GESTURES = frozenset(('click', 'rclick', 'dclick', 'tclick', 'move', 'drag', 'scroll', 'wheel', 'hwheel', 'key', 'wait', 'type', 'paste'))
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
FARA_ACTIONS = FARA_TOOL['parameters']['properties']['action']['enum']
CLI_CONFIG = dict(version=1, permissions=dict(allow=['Read(**)', 'Write(**)', 'Shell(*)', 'WebFetch(*)', 'Mcp(*:*)'], deny=[]), sandbox=dict(mode='disabled'), approvalMode='unrestricted', autoAcceptWebSearch=True)
NEXT = 'Here is the next screenshot. Think about what to do next.'
AUTO_REPLY = "keep going so far as you don't make up information, you have my approval"
MAX_IMAGES, STEPS, WAIT_CAP = 3, 30, 30.0
NO_PAGE = 'This is a Windows desktop, not a web page: {action} cannot read page text here. Look at the screenshot and use the mouse and keyboard.'
FARA_KEY = {k: k for k in ('ctrl', 'shift', 'alt', 'win', 'enter', 'esc', 'tab', 'backspace', 'del', 'up', 'down', 'left', 'right', 'pageup', 'pagedown', 'space', 'home', 'end', 'insert', *(f'f{i}' for i in range(1, 13)))} | {
    'control': 'ctrl', 'meta': 'win', 'windows': 'win', 'super': 'win', 'cmd': 'win', 'command': 'win', 'option': 'alt', 'return': 'enter', 'escape': 'esc', 'delete': 'del',
    'arrowup': 'up', 'arrowdown': 'down', 'arrowleft': 'left', 'arrowright': 'right'}
_SERVED = False

def logged_file(path, value):
    body = '\n'.join((' ' + line) if line.startswith('##') else line for line in str(value).split('\n'))
    if len(body) > 1500: body = body[:1500]
    return f"{path} ({len(value)} chars)\n{body}"
def blank_frame(pixels):
    n = len(pixels) // 4
    zero = bytes(n)
    return bool(n) and pixels[0::4] == zero and pixels[1::4] == zero and pixels[2::4] == zero
def text(p, v):
    p = Path(p)
    t = p.with_name(p.name + '.' + uuid.uuid4().hex + '.pending')
    t.write_bytes(v.encode('utf-8')); t.replace(p)
    log('file', dict(text=logged_file(p, v)))
def read(p): return json.loads(Path(p).read_text('utf-8-sig'))
def write(p, v): text(p, json.dumps(v, ensure_ascii=False))
def contents(p): return p.read_bytes() if p.exists() else b''
def forwarded(**values):
    return [arg for name, value in (SET | values).items() if value != SETTINGS[name] or name == 'run' for arg in (['--' + name] if value is True else ['--' + name, str(value)])]
def clock():
    if not WAKE_START: return {}
    now = time.time()
    left = dict(left=f"{(float(WAKE_END) - now) / 60:.1f} min") if WAKE_END else {}
    return dict(clock=dict(now=time.strftime('%H:%M:%S'), used=f"{(now - float(WAKE_START)) / 60:.1f} min") | left)
def load(path): return win32.unpng(Path(path).read_bytes())
def b64(image): return 'data:image/png;base64,' + base64.b64encode(win32.png(image)).decode()
def words(message):
    content = (message or {}).get('content') if isinstance(message, dict) else message
    if isinstance(content, str): return content
    if isinstance(content, list): return ''.join(p.get('text', '') if isinstance(p, dict) else str(p) for p in content)
    return '' if content is None else str(content)
def reply(body):
    if isinstance(body, dict) and body.get('choices'): return words((body['choices'][0] or {}).get('message'))
    if isinstance(body, dict) and 'answers' in body: return json.dumps(body['answers'], ensure_ascii=False)
    return json.dumps(body, ensure_ascii=False, default=str)
THOUGHT = []
def clip_thought(text, limit=3000):
    text = text.strip()
    if len(text) <= limit: return text
    cut = text.rfind(' ', 0, limit)
    if cut < limit // 2: cut = limit
    return text[:cut] + ' …'
def flush_thought():
    thought = clip_thought(''.join(THOUGHT)); THOUGHT.clear()
    if thought: log('think', dict(text=thought))
def shell_command(cmd):
    names = set(tools())
    kept = []
    for segment in cmd.splitlines() or ['']:
        marks = [m.start() for m in re.finditer(r'--([A-Za-z0-9-]+)\b', segment) if m.group(1) in names]
        kept.append((segment[marks[0]:] if marks else segment).strip())
    return '\n'.join(line for line in kept if line)
def shell_log(payload, started):
    if not isinstance(payload, dict): return str(payload)[:4000]
    if started: return shell_command(str(payload.get('command') or ''))[:4000]
    success = payload.get('success')
    if isinstance(success, dict):
        if 'exitCode' in success or 'stdout' in success:
            line = f"exit {success.get('exitCode')}" + (' background' if payload.get('isBackground') else '')
            out, err = success.get('stdout') or '', success.get('stderr') or ''
            return '\n'.join(part for part in (line, out, err) if part)[:8000]
        return json.dumps(success, default=str)[:2000]
    if payload.get('error') is not None: return str(payload.get('error'))[:2000]
    return json.dumps(payload, default=str)[:2000]
def narrate(kind, data):
    try: event = json.loads(data) if kind == 'agent' else None
    except json.JSONDecodeError: event = None
    if isinstance(event, dict) and event.get('type') == 'thinking':
        if event.get('subtype') == 'delta': THOUGHT.append(event.get('text') or '')
        return []
    now, thought = time.strftime('%H:%M:%S'), clip_thought(''.join(THOUGHT))
    THOUGHT.clear()
    return ([dict(t=now, kind='think', text=thought)] if thought else []) + event_rows(now, kind, data, event)
def event_rows(now, kind, data, event):
    if kind == 'resp': return [dict(t=now, kind=kind, text=f"{data.get('url')}\n{reply(data.get('body'))}"[:4000])]
    if kind != 'agent':
        if isinstance(data, dict):
            shown = data.get('text', json.dumps({k: v for k, v in data.items() if k != 'body'}, default=str))
            return [dict(t=now, kind=kind, text=str(shown)[:{'report': 8000, 'think': 4000}.get(kind, 2000)])]
        return [dict(t=now, kind=kind, text=str(data)[:2000])]
    if not isinstance(event, dict): return [dict(t=now, kind='agent', text=str(data)[:2000])]
    typ, sub = event.get('type'), event.get('subtype')
    if typ == 'user':
        said = words(event.get('message')); cut = said.rfind('\n\nSENTENCE\n\n')
        return [dict(t=now, kind='user', text=(said[cut + 2:] if cut >= 0 else said)[:4000])]
    if typ == 'assistant':
        said = words(event.get('message'))
        return [dict(t=now, kind='say', text=said[:4000])] if said else []
    if typ == 'tool_call':
        call = event.get('tool_call') or {}
        name = next((k[:-8] for k in call if str(k).endswith('ToolCall')), 'tool')
        body = call.get(name + 'ToolCall') or {}
        payload = body.get('args') if sub == 'started' else body.get('result')
        text = shell_log(payload, sub == 'started') if name == 'shell' else json.dumps(payload, default=str)[:1500]
        return [dict(t=now, kind='call' if sub == 'started' else 'result', name=name, text=text)]
    if typ == 'system' and sub == 'init': return [dict(t=now, kind='start', text=str(event.get('model') or ''))]
    if typ == 'result': return [dict(t=now, kind='say', text=str(event.get('result') or '')[:8000])]
    return []
def render(item):
    kind = {'call': 'request', 'result': 'response', 'req': 'request', 'resp': 'response'}.get(item['kind'], item['kind'])
    name = f" {item['name']}" if item.get('name') else ''
    return f"## {item['t']} {kind}{name}\n\n{item.get('text') or ''}\n\n".encode()
FULL = frozenset(('think', 'say', 'user', 'start', 'report', 'exit'))
def console_row(item):
    rendered = render(item)
    kind = {'call': 'request', 'result': 'response', 'req': 'request', 'resp': 'response'}.get(item['kind'], item['kind'])
    return rendered if kind in FULL else rendered.split(b'\n\n', 1)[0] + b'\n'
def log(kind, data):
    serve()
    items = narrate(kind, data)
    blob = b''.join(render(item) for item in items)
    if not blob: return
    with open(RUN, 'ab') as handle:
        handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        handle.write(blob); handle.flush()
        handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    if sys.stdout.isatty():
        sys.stdout.buffer.write(b''.join(console_row(item) for item in items)); sys.stdout.buffer.flush()
def journal_target(): return Path((ROOT / 'journal.path').read_text('utf-8').strip())
def port_open():
    try:
        with socket.create_connection(('127.0.0.1', JOURNAL_PORT), 0.3): return True
    except OSError: return False
def serve():
    global _SERVED
    pointer, target = ROOT / 'journal.path', str(RUN)
    if contents(pointer).decode('utf-8').strip() != target:
        pointer.write_text(target, encoding='utf-8'); _SERVED = False
    if _SERVED or port_open():
        _SERVED = True; return
    subprocess.Popen([str(PYTHON), '-u', '-B', '-X', 'utf8', str(SCRIPT), '--journal'], cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=DETACHED, close_fds=True)
def journal_serve():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    page = b'<!DOCTYPE html><meta charset=utf-8><title>trident</title><body style="background:#000;color:#fff;margin:0"><pre id=t style="white-space:pre-wrap;margin:0;font:14px monospace"></pre><script>var t=document.getElementById("t"),es=new EventSource("/events");function z(){scrollTo(0,1e9)}es.addEventListener("reset",function(e){t.textContent=e.data;z()});es.addEventListener("more",function(e){t.textContent+=e.data;z()})</script>'
    class H(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def _send(self, code, ctype, body=b'', stream=False):
            self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Cache-Control', 'no-store'); self.send_header('Access-Control-Allow-Origin', '*')
            if not stream: self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if body and not stream: self.wfile.write(body)
        def _event(self, name, payload):
            self.wfile.write(('event: %s\n' % name + ''.join('data: %s\n' % line for line in payload.decode('utf-8', 'replace').split('\n')) + '\n').encode()); self.wfile.flush()
        def do_GET(self):
            path, target = self.path.split('?', 1)[0], journal_target()
            if path == '/md':
                self._send(200, 'text/plain; charset=utf-8', contents(target)); return
            if path == '/events':
                self._send(200, 'text/event-stream; charset=utf-8', stream=True)
                seen = current = None
                try:
                    while True:
                        data, key = contents(target), str(target)
                        if key != current or seen is None or len(data) < seen:
                            current, seen = key, len(data); self._event('reset', data)
                        elif len(data) > seen:
                            self._event('more', data[seen:]); seen = len(data)
                        target = journal_target(); time.sleep(0.4)
                except OSError: return
            if path in ('/', '/index.html'):
                self._send(200, 'text/html; charset=utf-8', page); return
            self.send_error(404)
    ThreadingHTTPServer.allow_reuse_address = True
    ThreadingHTTPServer((JOURNAL_HOST, JOURNAL_PORT), H).serve_forever()
def snap(number, how): return int(how(number / FARA_FACTOR) * FARA_FACTOR)
def fara_fit(image):
    _, w, h = image
    if max(h, w) / min(h, w) > 200: raise ValueError('bad image')
    height, width = (h * FARA_TARGET_WIDTH / w, FARA_TARGET_WIDTH) if FARA_TARGET_WIDTH and w > FARA_TARGET_WIDTH else (h, w)
    h_bar, w_bar = max(FARA_FACTOR, snap(height, round)), max(FARA_FACTOR, snap(width, round))
    if h_bar * w_bar > FARA_MAX_PIXELS:
        beta = math.sqrt((height * width) / FARA_MAX_PIXELS)
        h_bar, w_bar = max(FARA_FACTOR, snap(height / beta, math.floor)), max(FARA_FACTOR, snap(width / beta, math.floor))
    elif h_bar * w_bar < FARA_MIN_PIXELS:
        beta = math.sqrt(FARA_MIN_PIXELS / (height * width))
        h_bar, w_bar = snap(height * beta, math.ceil), snap(width * beta, math.ceil)
    return image if (w, h) == (w_bar, h_bar) else win32.stretch(image, (0, 0, w, h), w_bar, h_bar)
def jev_fit(image):
    pixels, w, h = image
    width, height = max(w, math.ceil(h / 2)), max(h, math.ceil(w / 2))
    pad, stride = bytes((width - w) * 4), w * 4
    canvas = b''.join(pixels[y * stride:(y + 1) * stride] + pad for y in range(h)) + bytes(width * 4 * (height - h))
    return fara_fit((canvas, width, height)), width / w, height / h
def fara_call(raw):
    parts = raw.split('<tool_call>', 1)
    if len(parts) != 2: raise ValueError('Fara tool_call expected')
    try: call = json.loads(parts[1].split('</tool_call>', 1)[0].strip())
    except json.JSONDecodeError: call = ast.literal_eval(parts[1].split('</tool_call>', 1)[0].strip())
    if not isinstance(call, dict) or call.get('name') != 'computer_use' or not isinstance(call.get('arguments'), dict): raise ValueError('Fara tool_call expected')
    return parts[0].strip(), call
def fara_point(arguments):
    if 'coordinate' not in arguments: return None
    xy = arguments['coordinate']
    if isinstance(xy, str): xy = re.findall(r'-?\d+(?:\.\d+)?', xy)
    if not (isinstance(xy, (list, tuple)) and len(xy) == 2): raise ValueError('bad point')
    return tuple(max(0, min(NORM, int(round(float(v))))) for v in xy)
def fara_parse(raw):
    thoughts, call = fara_call(raw); arguments = call['arguments']
    if arguments.get('action') not in FARA_ACTIONS: raise ValueError(f"unknown action {arguments.get('action')!r}")
    return thoughts, arguments, fara_point(arguments)
def zoom_box(point, span=ZOOM_SPAN):
    span = max(1, min(NORM, int(span))); l, t = (min(max(v - span // 2, 0), NORM - span) for v in point)
    return [l, t, l + span, t + span]
def zoom_crop(image, box):
    l, t, r, b = win32.to_pixels(box, *image[1:])
    return win32.stretch(image, (l, t, r, b), FARA_TARGET_WIDTH, max(1, round(FARA_TARGET_WIDTH * (b - t) / max(1, r - l))))
def zoom_back(box, q): return [max(0, min(NORM, box[i] + (q[i] * (box[i + 2] - box[i]) + NORM // 2) // NORM)) for i in (0, 1)]
def fara_keys(names):
    out = []
    for name in names:
        key = FARA_KEY.get(str(name).strip().lower())
        if key is None and len(str(name)) == 1: key = str(name).lower()
        if key is None: raise ValueError(f'unknown {name}')
        out.append(key)
    if not out: raise ValueError('unknown key')
    return '+'.join(out)
def notches(pixels, span): return int(round(pixels * span / 1000 / 120.0))
def fara_program(arguments, size=(0, 0)):
    action, point = arguments.get('action'), fara_point(arguments)
    if action in POINTER and point is None: raise ValueError('bad point')
    if action == 'left_click': return f'click {point[0]} {point[1]}'
    if action == 'double_click': return f'dclick {point[0]} {point[1]}'
    if action == 'right_click': return f'rclick {point[0]} {point[1]}'
    if action == 'triple_click': return f'tclick {point[0]} {point[1]}'
    if action == 'mouse_move': return f'move {point[0]} {point[1]}'
    if action == 'left_click_drag': return f"x, y = desktop.position()\ndesktop.drag([(x, y), ({point[0]}, {point[1]})])"
    if action == 'type': return 'type ' + str(arguments.get('text', ''))
    if action == 'key': return 'key ' + fara_keys(arguments.get('keys') or [])
    if action in ('scroll', 'hscroll'):
        amount = notches(int(arguments.get('pixels') or 0), int(size[0 if action == 'hscroll' else 1] or 0)) * 120
        if not amount: return ''
        verb = 'hwheel' if action == 'hscroll' else 'wheel'
        return f'{verb} {amount}' + (f' {point[0]} {point[1]}' if point else '')
    if action == 'wait': return f"wait {arguments.get('time') or 0}"
    if action == 'history_back': return 'key alt+left'
    if action in ('visit_url', 'web_search'):
        raw = arguments.get('url') if action == 'visit_url' else 'https://www.bing.com/search?q=' + urllib.parse.quote_plus(str(arguments.get('query') or ''))
        url = str(raw or '')
        if not url.startswith(('https://', 'http://', 'file://', 'about:')): url = 'https://' + url
        return 'key ctrl+l\ntype ' + url + '\nkey enter'
    return ''
def questions(blocks):
    q = {}
    for i, block in enumerate(blocks):
        m = re.fullmatch(r'\s*([\w-]*)\s*([?#@])\s*(.+)', block, re.S)
        if not m: raise ValueError(JEV_USAGE)
        qid, kind, body = m.groups(); qid = qid or f'q{i+1}'
        ask, *opts = [s.strip() for s in body.split('|')]
        if not ask or (kind == '#' and len(opts) < 2) or (kind == '?' and len(opts) == 1) or (kind == '@' and opts): raise ValueError(JEV_USAGE)
        if kind == '@': q[qid] = dict(type='box', instructions=ask)
        elif kind == '#': q[qid] = dict(type='score', instructions=ask, criteria=opts)
        elif opts:
            criteria = dict(o.split('=', 1) if '=' in o else (f'opt{j+1}', o) for j, o in enumerate(opts))
            if kind == '?' and set(map(str.lower, criteria)) == {'true', 'false'}: q[qid] = dict(type='noul', instructions=ask, criteria={k.lower(): v for k, v in criteria.items()})
            else: q[qid] = dict(type='choice', instructions=ask, criteria=criteria | ({'change_approach': CHANGE_APPROACH} if 'change_approach' not in criteria else {}))
        else: q[qid] = dict(type='noul', instructions=ask)
    if not q: raise ValueError(JEV_USAGE)
    return q
def brain_model(sentence=''):
    forced = SET['mind-model'].strip()
    if forced: return forced
    said = str(sentence).lower()
    for slug in sorted(BRAINS, key=len, reverse=True):
        if slug in said: return slug
    return BRAIN
def earlier(): return [digest(p) for p in sorted(ROOT.glob('run_*.md')) if p != RUN][-RECALL_RUNS:]
def trajectory():
    calls, errors = Counter(), 0
    wakes = earlier()
    for wake in wakes:
        calls.update(wake.get('calls') or {}); errors += int(wake.get('errors') or 0)
    dominant = calls.most_common(1)[0][0] if calls else ''
    total = sum(calls.values())
    looking = calls.get('shell', 0) + calls.get('read', 0)
    advice = 'Do the file change in one pass and test it before more looking.' if total and looking / total >= 0.7 else 'Start from the newest ledger line.'
    return dict(wakes=len(wakes), errors=errors, calls=dict(calls), dominant=dominant, text=f'{len(wakes)} earlier wakes, {errors} errors, {total} tool calls' + (f', mostly {dominant}' if dominant else '') + '. ' + advice)
def digest(path):
    s = path.read_text('utf-8', 'replace')
    heads = re.findall(r'(?m)^## (\d\d:\d\d:\d\d) (\S+)(?: (\S+))?$', s)
    said = re.findall(r'(?ms)^## \S+ (?:say|report)\n\n(.*?)(?=^## |\Z)', s)
    sentence = re.search(r'(?ms)^SENTENCE\n\n(.*?)(?=^## |\Z)', s)
    return dict(run=path.stem, span=[heads[0][0], heads[-1][0]] if heads else [], sentence=sentence.group(1).strip()[:RECALL_CHARS] if sentence else '', calls=dict(Counter(n for _, k, n in heads if k == 'request' and n)), errors=sum(k == 'err' for _, k, _ in heads), final=said[-1].strip()[:RECALL_CHARS] if said else '')
def user_turn(url, prompt, original=False, keep=None):
    msg = dict(role='user', content=[dict(type='image_url', image_url=dict(url=url)), dict(type='text', text=prompt)])
    if original: msg['_keep'] = prompt
    elif keep is not None: msg['_keep'] = keep
    return msg
def has_image(msg): return isinstance(msg.get('content'), list) and any(isinstance(c, dict) and c.get('type') == 'image_url' for c in msg['content'])
def prune(history, keep=MAX_IMAGES):
    out, seen = [], 0
    for msg in reversed(history):
        if has_image(msg):
            if seen < keep: out.append(msg)
            elif '_keep' in msg: out.append(dict(role=msg['role'], content=[dict(type='text', text=msg['_keep'])], _keep=msg['_keep']))
            seen += 1
        else: out.append(msg)
    return out[::-1]
def messages(system, history, keep=MAX_IMAGES): return [dict(role='system', content=system)] + [{k: v for k, v in m.items() if not k.startswith('_')} for m in prune(history, keep)]
def key_combo(keys):
    if isinstance(keys, str): keys = [keys]
    return fara_keys([part for k in keys or [] for part in re.split(r'\s*\+\s*', str(k).strip()) if part])
def scroll_amount(pixels, span):
    pixels = float(pixels or 0)
    if not pixels or not span: return 0
    return (notches(pixels, span) or (1 if pixels > 0 else -1)) * 120
def fara_type(desktop, value):
    if not value.isascii() or any(ord(c) < 32 and c not in '\n\t' for c in value): desktop.paste(value); return
    for i, line in enumerate(value.split('\n')):
        if i: desktop.key('enter')
        if line: desktop.type_text(line)
def act(engine, args, size, facts):
    kind, d = args.get('action'), engine.desktop
    point = fara_point(args)
    if kind in POINTER and point is None: raise ValueError(f'{kind} needs a coordinate')
    if kind == 'left_click': d.click(*point); return False, f'I clicked at coordinates {point}.', ''
    if kind == 'right_click': d.rightClick(*point); return False, f'I right-clicked at coordinates {point}.', ''
    if kind == 'double_click': d.doubleClick(*point); return False, f'I double-clicked at coordinates {point}.', ''
    if kind == 'triple_click': d.click(*point, clicks=3); return False, f'I triple-clicked at coordinates {point}.', ''
    if kind == 'mouse_move': d.moveTo(*point); return False, f'I moved the cursor to {point}.', ''
    if kind == 'left_click_drag':
        start = d.position(); d.drag([start, point]); return False, f'I dragged from {tuple(start)} to {point}.', ''
    if kind == 'key':
        combo = key_combo(args.get('keys')); d.key(combo); return False, f'I pressed the following keys: {combo}', ''
    if kind == 'type':
        value = str(args.get('text', '')); fara_type(d, value); return False, f"I typed '{value}'.", ''
    if kind in ('scroll', 'hscroll'):
        amount = scroll_amount(args.get('pixels'), size[0 if kind == 'hscroll' else 1])
        at = point or d.position()
        if amount: d.wheel(amount, *at, horizontal=kind == 'hscroll')
        return False, f'I scrolled {"horizontally " if kind == "hscroll" else ""}by {amount} wheel units at {tuple(at)}.', ''
    if kind == 'wait':
        t = min(WAIT_CAP, max(0.0, float(args.get('time', args.get('duration', 3.0)) or 0))); time.sleep(t); return False, f'I waited {t:g}s.', ''
    if kind in ('visit_url', 'web_search'):
        raw = str(args.get('url' if kind == 'visit_url' else 'query') or '')
        url = raw if kind == 'visit_url' and raw.startswith(('https://', 'http://')) else ('https://' + raw if kind == 'visit_url' and ' ' not in raw else 'https://www.bing.com/search?q=' + urllib.parse.quote_plus(raw) + '&FORM=QBLH')
        os.startfile(url); return False, f'I opened {url} in the default browser.', ''
    if kind == 'history_back': d.key('alt+left'); return False, 'I pressed alt+left (back).', ''
    if kind == 'read_page_answer_question':
        note = NO_PAGE.format(action=kind); return False, note, note
    if kind == 'pause_and_memorize_fact':
        fact = str(args.get('fact', '')); facts.append(fact); return False, f'I memorized the following fact: {fact}', ''
    if kind == 'ask_user_question': return True, f"I asked the user: {args.get('question', '')}", ''
    if kind == 'terminate': return True, str(args.get('answer', '')), ''
    raise ValueError(f'unknown action {kind!r}')
def step_md(n, obs, sent, thoughts, args, point, px, result, info):
    shown = {k: v for k, v in args.items() if k not in ('action', 'thoughts')}
    rows = [f'### step {n}', f"- screenshot: {obs['image']} (sent {sent[0]}x{sent[1]})", f'- thought: {thoughts or "-"}',
            f"- action: {args.get('action')} {json.dumps(shown, ensure_ascii=False)}"]
    if point: rows.append(f'- coordinate: {list(point)} of 0-1000 -> screen px {list(px)}')
    rows += [f'- result: {result}', f"- fara: {info}"]
    return '\n'.join(rows)

@dataclass(frozen=True)
class Oracle:
    url: str
    timeout: float | None = None
    def post(self, payload, shown):
        log('req', dict(url=self.url) | shown)
        try:
            req = urllib.request.Request(self.url, json.dumps(payload).encode(), {'Content-Type': 'application/json'} | self.headers)
            with urllib.request.urlopen(req, timeout=self.timeout) as r: response = json.load(r)
        except Exception as e:
            log('err', dict(url=self.url, error=repr(e))); raise
        log('resp', dict(url=self.url, body=response)); return response
class Vision(Oracle):
    headers = {}
    def ask(self, prompt, image, origin):
        return self.chat([dict(role='system', content=FARA_SYSTEM), user_turn(b64(image), prompt)], dict(question=prompt, image=origin, sent_size=list(image[1:])))[0]
    def chat(self, messages, shown):
        response = self.post(dict(model=VISION_MODEL, temperature=0, max_tokens=FARA_MAX_TOKENS, chat_template_kwargs=dict(enable_thinking=False), messages=messages), shown)
        choice, message = response['choices'][0], response['choices'][0]['message']
        if choice.get('finish_reason') == 'length': raise ValueError(f'reply cut at {FARA_MAX_TOKENS} tokens')
        content, reasoning = (message.get('content') or '').strip(), (message.get('reasoning_content') or '').strip()
        if reasoning and reasoning not in content: content = f'{reasoning}\n{content}'.strip()
        if not content: raise ValueError('Fara tool_call expected')
        return content, response
class Decision(Oracle):
    headers = property(lambda self: {'Authorization': 'Bearer ' + (ROOT.parent / '.env').read_text('utf-8-sig').strip().strip('"\'')})
    def ask(self, state, questions, shown):
        return self.post(dict(model=DECISION_MODEL, state=state, questions=questions), dict(questions=questions, state=shown))

def whole(v): return int(round(float(v)))
class Hand:
    def __init__(self, work): self.work = work
    region = property(lambda self: (self.work / 'region.txt').read_text('utf-8').strip())
    def position(self): return win32.cursor(self.region)
    def moveTo(self, x, y, duration=0):
        x, y = whole(x), whole(y)
        win32.move(*win32.point(x, y, self.region)); return dict(at=[x, y])
    def click(self, x, y, button='left', clicks=1):
        x, y = whole(x), whole(y)
        if button == 'right': win32.click(x, y, self.region, right=True)
        elif clicks == 2: win32.double_click(x, y, self.region)
        else:
            for _ in range(max(1, clicks)): win32.click(x, y, self.region)
        return dict(clicked=[x, y], button=button, clicks=clicks)
    def rightClick(self, x, y): return self.click(x, y, 'right')
    def doubleClick(self, x, y): return self.click(x, y, clicks=2)
    def drag(self, points, duration=0.2):
        if len(points) < 2: raise ValueError('drag needs >=2 points')
        win32.stroke([(whole(x), whole(y)) for x, y in points], self.region); return dict(dragged=len(points))
    def key(self, combo):
        win32.hotkey(str(combo)); return dict(keys=str(combo).replace(' ', '').lower().split('+'))
    def type_text(self, value):
        win32.type_text(str(value)); return dict(typed=len(str(value)))
    def set_clipboard(self, value):
        subprocess.run(['powershell.exe', '-NoProfile', '-Command', '[Console]::InputEncoding=[Text.UTF8Encoding]::new(); Set-Clipboard -Value ([Console]::In.ReadToEnd())'], input=str(value).encode('utf-8'), capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        return dict(clipboard=len(str(value)))
    def paste(self, value):
        self.set_clipboard(value); return self.key('ctrl+v')
    def scroll(self, clicks, x=None, y=None):
        if x is None: x, y = self.position()
        n = int(clicks); win32.scroll(whole(x), whole(y), self.region, 1 if n > 0 else -1, abs(n)); return dict(scrolled=n)
    def wheel(self, amount, x=None, y=None, horizontal=False):
        if x is None: x, y = self.position()
        win32.wheel(whole(x), whole(y), self.region, int(amount), horizontal); return dict(wheel=int(amount), horizontal=horizontal)

@dataclass
class Engine:
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
    desktop = cached_property(lambda self: Hand(self.work))
    def __post_init__(self, fresh):
        self.work.mkdir(parents=True, exist_ok=True); os.chdir(self.work)
        if fresh:
            for f in (self.story_path, self.ledger_path, self.edit_log, self.goal, self.result, self.sentinel, Path('look.json'), Path('region.txt')): f.unlink(missing_ok=True)
        for f in (self.story_path, self.ledger_path, self.edit_log, self.goal): f.touch()
        self.result.exists() or write(self.result, {})
        Path('look.json').exists() or write('look.json', None)
        Path('region.txt').exists() or text('region.txt', 'NONE')
    def _log_edit(self, path, note=''):
        with open(self.edit_log, 'a', encoding='utf-8') as handle: handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {path} {note}\n")
    def region(self, value=''):
        """$CALL --region [NONE|LEFT,TOP,RIGHT,BOTTOM]
        Takes NONE (the full screen, the default) or a box on the 0-1000 screen; with no value a selector opens for the owner (drag a box, right-click for the full screen, Escape cancels). --capture, --retina and --hands then work in it, and --hands points are 0-1000 of it.
        Returns {region}. Use it to zoom the eye on a small area."""
        if not value:
            value, code = win32.select_region()
            if code: raise SystemExit(code)
        elif value != 'NONE':
            box = list(map(int, value.split(','))) if re.fullmatch(r'\d+,\d+,\d+,\d+', value) else []
            if not (len(box) == 4 and box[0] < box[2] <= NORM and box[1] < box[3] <= NORM): raise SystemExit(f'region is NONE or left,top,right,bottom on 0-{NORM}')
        text(self.work / 'region.txt', value); return dict(region=value)
    def capture(self, *_):
        """$CALL --capture
        Takes nothing. Screenshots the region into shots/<id>.png and writes screen.json.
        Returns {id, image, size, region, at}. Use it before you judge the screen and after you act; --retina and Jev read the latest capture."""
        region = self.desktop.region
        image = win32.capture(region)
        shots = self.work / 'shots'; shots.mkdir(exist_ok=True)
        sid = time.strftime('%H%M%S') + f'{time.time_ns() // 1000 % 1000:03d}'
        path = shots / f'{sid}.png'; path.write_bytes(win32.png(image))
        v = dict(id=sid, image=str(path), size=list(image[1:]), region=region, at=round(time.time(), 3))
        pixels = image[0]
        if blank_frame(pixels): v['blank'] = True
        write('screen.json', v); return v
    def windows(self, *_):
        """$CALL --windows
        Takes nothing. Lists visible, uncloaked, titled top-level windows, top of the z-order first.
        Returns {windows: [{hwnd, title, box (0-1000 of the screen), pid, minimized, topmost, foreground}]}. Use it to corroborate Retina, above all for a window hidden behind or over another, and to get the hwnd for --place."""
        return dict(windows=win32.windows())
    def _blank(self):
        path = self.work / 'screen.json'
        if not path.exists(): return None
        screen = read(path)
        return screen if isinstance(screen, dict) and screen.get('blank') else None
    def _shot(self):
        screen = self.screen
        return screen, fara_fit(load(screen['image']))
    def _eye(self, screen, image, prompt):
        thoughts, arguments, point = fara_parse(self.vision.ask(prompt, image, screen['image']))
        if thoughts and 'thoughts' not in arguments: arguments['thoughts'] = thoughts
        return dict(capture=screen['id'], region=screen['region'], thoughts=thoughts, action=arguments.get('action'), arguments=arguments, coordinate=list(point) if point else None, program=fara_program(arguments, screen.get('size') or (0, 0)))
    def _zoom(self, screen, q, coarse):
        if coarse['action'] not in POINTER or not coarse['coordinate']: return {}
        box = zoom_box(coarse['coordinate'])
        try: fine = self._eye(screen, fara_fit(zoom_crop(load(screen['image']), box)), q)
        except Exception: return {}
        if fine['action'] not in POINTER or not fine['coordinate']: return {}
        arguments = coarse['arguments'] | dict(action=fine['action'], coordinate=zoom_back(box, fine['coordinate']))
        return dict(action=fine['action'], arguments=arguments, coordinate=arguments['coordinate'], program=fara_program(arguments, screen.get('size') or (0, 0)))
    def _pinned_shot(self):
        pin = SET['retina-pin']
        if not pin: return None
        spec = json.loads(pin)
        image = fara_fit(load(spec['image']))
        return dict(id=spec['id'], image=spec['image'], region=spec.get('region') or 'NONE', size=spec.get('size') or list(image[1:])), image
    def _retina_bg(self, question):
        shots = self.work / 'shots'; shots.mkdir(exist_ok=True)
        pid_path, out, err = shots / 'retina-bg.pid', shots / 'retina-bg.json', shots / 'retina-bg.err'
        if pid_path.exists() and win32.alive(pid := int(pid_path.read_text('utf-8').strip())): return dict(background=True, busy=True, pid=pid, out=str(out), err=str(err))
        screen = self.screen; out.unlink(missing_ok=True)
        pin = json.dumps(dict(image=screen['image'], id=screen['id'], region=screen['region'], size=screen.get('size')))
        with open(err, 'w', encoding='utf-8') as eh, open(out, 'w', encoding='utf-8') as oh:
            proc = subprocess.Popen([str(PYTHON), '-B', str(SCRIPT), *forwarded(**{'retina-pin': pin}), '--work', str(self.work), '--retina', question], cwd=str(self.work), stdin=subprocess.DEVNULL, stdout=oh, stderr=eh, creationflags=DETACHED, close_fds=True)
        text(pid_path, str(proc.pid)); return dict(background=True, busy=False, pid=proc.pid, capture=screen['id'], out=str(out), err=str(err))
    def retina(self, question=''):
        """$CALL --retina "GOAL"
        Takes one goal for the latest capture that names the window, e.g. "In the <window>, click the <target>", sent to Fara as it is. Fara is a computer-use agent: it answers with exactly one action and no memory of earlier calls; this is the only image-recognition tool. "bg GOAL" returns at once and writes shots/retina-bg.json for the capture you had. "zoom GOAL" (also "bg zoom GOAL") asks again on a crop of the same capture around Fara's first point and returns only that finer point; use it for small targets such as icons, links, swatches and arrows. This call often takes longer than 30 seconds, so set the shell timeout higher or to 0, as with a long install.
        Returns {task, capture, region, thoughts, action, arguments, coordinate (0-1000 of the capture), program}. coordinate is the target point; program is a --hands program for the proposed action, yours to run, change or ignore. A short question ("What does the <window> say last?") comes back as action terminate with arguments.answer, sometimes only after a click it proposes first. Fara does not return lists or boxes: ask one goal or one fact per call, and get a region from two or more point goals or from Jev."""
        q = str(question).strip()
        if not q or q == 'bg': raise ValueError('retina needs a goal')
        if not SET['retina-pin']:
            screen = self._blank()
            if screen: return dict(blank=True, capture=screen.get('id'), region=screen.get('region'))
        if q.startswith('bg '):
            q = q[3:].strip()
            if not q: raise ValueError('retina needs a goal')
            return self._retina_bg(q)
        zoom = q.startswith('zoom ')
        if zoom: q = q[5:].strip()
        pinned = self._pinned_shot()
        screen, image = pinned if pinned else self._shot()
        v = dict(task=q) | self._eye(screen, image, q)
        if zoom: v |= self._zoom(screen, q, v)
        if not pinned: write('look.json', v)
        return v
    def jev(self, narrative, *blocks):
        """$CALL JEV_USAGE
        Takes a neutral narrative and typed questions. Jev also receives story.txt, ledger.md and goal.txt; a box question also receives the current capture.
        Returns his answers with probabilities and confidence and a summary line per question; a box is left top right bottom on 0-1000 of the capture. Every choice includes change_approach. Use it before a decision with two or more ways, to locate a target, and before you claim done."""
        if not str(narrative).strip() or not blocks: raise ValueError(JEV_USAGE)
        q = questions(blocks)
        boxed = any(item['type'] == 'box' for item in q.values())
        if boxed:
            screen = self._blank()
            if screen: return dict(blank=True, by='jev', capture=screen.get('id'))
        state = ['STORY: ' + self.story_path.read_text('utf-8'), 'LEDGER: ' + self.ledger_path.read_text('utf-8'), 'WHY: ' + narrative, 'GOAL: ' + self.goal.read_text('utf-8')]
        shown, sx, sy = [f"{item.split(':', 1)[0]}: {len(item)} chars" for item in state], 1, 1
        if boxed:
            shot = self.screen
            image, sx, sy = jev_fit(load(shot['image']))
            state.append(dict(type='image_url', image_url=dict(url=b64(image)))); shown.append(shot['image'])
        r, told = self.decision.ask(state, q, shown), []
        for name, a in r.get('answers', {}).items():
            if a['type'] == 'box': a['box'] = {k: min(1, v * (sx if k in ('left', 'right') else sy)) for k, v in a['box'].items()}
            if a['type'] == 'choice': told.append(f"{name}: {a['choice']} ({', '.join(f'{k} {v:.2f}' for k, v in a['probabilities'].items())}; conf {a['confidence']:.2f})")
            elif a['type'] == 'noul':
                p = a.pop('noul'); a['type'] = 'yes'; a['yes'] = p
                told.append(f"{name}: yes {p:.2f}")
            elif a['type'] == 'box':
                b = a['box']; told.append(f"{name}: box {int(round(b['left']*NORM))} {int(round(b['top']*NORM))} {int(round(b['right']*NORM))} {int(round(b['bottom']*NORM))} conf {a['confidence']:.2f}")
            else: told.append(f"{name}: score {a['score']:.2f}")
        return r | dict(by='jev', summary='\n'.join(told))
    def _gestures(self, src):
        d = self.desktop
        for line in filter(None, map(str.strip, src.splitlines())):
            verb, _, rest = line.partition(' ')
            if verb in ('type', 'paste'):
                (d.type_text if verb == 'type' else d.paste)(rest); continue
            for cmd in filter(None, map(str.strip, line.split(';'))):
                verb, _, rest = cmd.partition(' ')
                if verb == 'key': d.key(rest)
                elif verb in ('type', 'paste'):
                    (d.type_text if verb == 'type' else d.paste)(rest)
                elif verb == 'wait': time.sleep(max(0, float(rest)))
                elif verb == 'scroll':
                    n, *at = rest.split(); d.scroll(int(n), *map(float, at))
                elif verb in ('wheel', 'hwheel'):
                    n, *at = rest.split(); d.wheel(int(n), *map(float, at), horizontal=verb == 'hwheel')
                elif verb == 'tclick':
                    nums = list(map(float, rest.split()))
                    if len(nums) != 2: raise ValueError('tclick needs one pair')
                    d.click(*nums, clicks=3)
                elif verb in ('click', 'rclick', 'dclick', 'move', 'drag'):
                    nums = list(map(float, rest.split()))
                    if len(nums) % 2 or (len(nums) < 4 if verb == 'drag' else len(nums) != 2): raise ValueError(f'{verb} needs 0-{NORM} pairs')
                    pts = list(zip(nums[::2], nums[1::2]))
                    d.drag(pts) if verb == 'drag' else getattr(d, dict(click='click', rclick='rightClick', dclick='doubleClick', move='moveTo')[verb])(*pts[0])
                else: raise ValueError(f'unknown {verb}')
    def hands(self, src):
        """$CALL --hands "PROGRAM"
        Takes gesture lines (click x y, rclick x y, dclick x y, tclick x y, move x y, drag x y x y ..., scroll n [x y], wheel n [x y], hwheel n [x y], key ctrl+s, type TEXT, paste TEXT, wait SECONDS; ; joins commands on one line) or Python with desktop (click, rightClick, doubleClick, moveTo, drag, key, type_text, paste, scroll, wheel, position), read, time, json and Path. Points are 0-1000 of the region.
        Runs it, waits, then captures. Returns the capture plus {stdout}. Use it for every mouse and keyboard act, then look at the capture."""
        d = self.desktop
        with redirect_stdout(io.StringIO()) as out:
            head = src.split(None, 1)[0] if src.strip() else ''
            if head in GESTURES: self._gestures(src)
            elif head: exec(compile(src, '<hands>', 'exec'), dict(desktop=d, read=read, time=time, json=json, Path=Path))
        time.sleep(SCREENSHOT_DELAY)
        v = self.capture() | dict(stdout=out.getvalue())
        write('screen.json', v); return v
    def story(self, narrative):
        """$CALL --story "TEXT"
        Takes the whole new story.txt: your checklist and notes, read by you and by Jev in this and the next wake.
        Returns {story_len}. Use it whenever the checklist or notes change; shorten it by writing it whole again."""
        text(self.story_path, narrative); self._log_edit('story.txt', 'rewrite'); return dict(story_len=len(narrative))
    def ledger(self, lesson):
        """$CALL --ledger "LESSON"
        Takes this wake's lesson, 1-1200 characters: what worked, what failed, what to do first next time. A later call replaces it. At wake end Trident adds the minutes used and how the wake ended, keeps the last 12 wakes and shows them to the next wake and to Jev.
        Returns {wakes, lesson_len}. Call it at the end of every wake."""
        if not WAKE_START: raise SystemExit('the ledger is written inside a wake')
        lesson = WAKE.sub('', str(lesson)).strip()
        if not 0 < len(lesson) <= LEDGER_LESSON: raise ValueError(f'a lesson has 1-{LEDGER_LESSON} characters; write it shorter')
        start = float(WAKE_START)
        self._enter(start, (float(WAKE_END) - start) / 60 if WAKE_END else '', 'open', lesson)
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
        """$CALL --recall
        Takes nothing. Digests the journals of up to 5 earlier wakes, never this one.
        Returns {wakes: [{run, span, sentence, calls, errors, final}]}. Use it when the ledger is not enough."""
        return dict(wakes=earlier())
    def learn(self, *_):
        """$CALL --learn
        Takes nothing. Totals the tool calls and errors of up to 5 earlier journals, never this one.
        Returns {wakes, errors, calls, dominant, text}. Use it to see where earlier wakes spent their calls."""
        return trajectory()
    def place(self, hwnd, left, top, right, bottom):
        """$CALL --place HWND LEFT TOP RIGHT BOTTOM
        Takes a window handle from --windows and a 0-1000 screen box. Restores the window, moves it there and clears topmost.
        Returns {hwnd, box, moved, err}. Use it to lay windows side by side; capture afterwards."""
        return win32.place(hwnd, left, top, right, bottom)
    def pilot(self, task='', steps=''):
        task, steps = str(task).strip(), int(steps or STEPS)
        if not task: raise ValueError('pilot needs a task')
        auto = SET['pilot-autoreply']
        region = self.desktop.region
        trace_path = self.work / 'shots' / f'pilot_{WAKE_ID}.json'
        trace = dict(task=task, steps=steps, region=region, model=VISION_MODEL, url=self.vision.url, max_images=MAX_IMAGES, records=[])
        def save(): trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=1), 'utf-8')
        log('pilot', dict(text=f'PILOT\n\nTASK: {task}\n\nFara drives (Microsoft Fara15Agent loop), at most {steps} actions, region {region}, '
                               f'{MAX_IMAGES} newest screenshots per call, pause {SCREENSHOT_DELAY}s after each action. Trace: {trace_path}'))
        history, facts, pending, status, answer = [], [], '', 'max steps', ''
        obs = self.capture()
        for n in range(1, steps + 1):
            if self.sentinel.exists(): status = 'stop file'; break
            image = fara_fit(load(obs['image']))
            url = b64(image)
            if n == 1: history.append(user_turn(url, task, original=True))
            elif pending.startswith('USER:'): history.append(user_turn(url, pending[5:], keep=pending[5:]))
            else: history.append(user_turn(url, f'{pending}\n{NEXT}' if pending else NEXT))
            pending = ''
            payload = messages(FARA_SYSTEM, history)
            t0 = time.time()
            try: raw, body = self.vision.chat(payload, dict(step=n, image=obs['image'], sent_size=list(image[1:]), images=sum(map(has_image, payload)), text=payload[-1]['content'][-1]['text']))
            except Exception as e: status = f'fara error: {e!r}'; log('pilot', dict(text=f'### step {n}\n- fara error: {e!r}')); break
            history.append(dict(role='assistant', content=raw))
            tm = (body or {}).get('timings') or {}
            info = f"{time.time() - t0:.1f}s, prompt {tm.get('prompt_n', '?')} tok (cached {tm.get('cache_n', '?')}), reply {tm.get('predicted_n', '?')} tok"
            record = dict(step=n, screenshot=obs['image'], sent_size=list(image[1:]), reply=raw, seconds=round(time.time() - t0, 1), timings=tm)
            try:
                thoughts, args, point = fara_parse(raw)
                px = win32.point(*point, region) if point else None
                stop, result, pending = act(self, args, obs['size'], facts)
            except Exception as e:
                record.update(error=repr(e)); trace['records'].append(record)
                log('pilot', dict(text=f'### step {n}\n- reply: {raw}\n- invalid: {e!r}')); status = f'invalid action: {e}'; break
            record.update(thoughts=thoughts, action=args.get('action'), arguments=args, coordinate=list(point) if point else None, screen_px=list(px) if px else None, result=result)
            trace['records'].append(record)
            log('pilot', dict(text=step_md(n, obs, image[1:], thoughts, args, point, px, result, info)))
            save()
            if args.get('action') == 'ask_user_question' and auto:
                pending, stop = 'USER:' + AUTO_REPLY, False
            if stop:
                status, answer = args.get('action'), result; break
            time.sleep(SCREENSHOT_DELAY)
            obs = self.capture()
        final = self.capture()
        trace.update(status=status, answer=answer, facts=facts, final=final['image'],
                     last_messages=[dict(role=m['role'], content=[c if c.get('type') != 'image_url' else dict(type='image_url', image_url='<screenshot>') for c in m['content']] if isinstance(m['content'], list) else m['content']) for m in messages(FARA_SYSTEM, history)[1:]])
        save()
        log('pilot', dict(text=f'PILOT END\n\n- status: {status}\n- actions: {len(trace["records"])}\n- answer: {answer or "-"}\n- facts: {facts}\n- final screenshot: {final["image"]}'))
        return dict(task=task, status=status, actions=len(trace['records']), answer=answer, facts=facts, final=final['image'], trace=str(trace_path), journal=str(RUN))
    def stop(self, *_):
        """$CALL --stop
        Takes nothing. Ends the wake: writes the stop file, so the mind is stopped and the ledger is sealed.
        The wake ends after this call, so the next message must be the final report and nothing else.
        Returns nothing; the call exits. Use it when the sentence is done or cannot be done."""
        self.sentinel.touch(); log('file', dict(text=logged_file(self.sentinel, ''))); sys.exit(0)
    def _emit(self, name, v):
        v = v | clock(); write(self.result, v)
        if sys.stdout.isatty(): log(name, v)
        else: print(json.dumps(v, ensure_ascii=False), flush=True)
    def _mind_prompt(self, sentence, minutes, start, end):
        call = ' '.join([f"& '{PYTHON}' '{SCRIPT}' --work '{self.work}'"] + [arg if arg.startswith('--') else f"'{arg}'" for arg in forwarded(**{'wake-start': str(start), 'wake-end': str(end or '')})])
        docs = [f.__doc__.replace('$CALL', call) for f in tools().values() if f.__doc__]
        budget = f"The wake has {minutes} minutes and ends at {time.strftime('%H:%M', time.localtime(end))}." if minutes else 'No budget was given here; if the sentence names one, keep it.'
        return '\n\n'.join((MIND.format(budget=budget, norm=NORM), 'BRAIN', brain_model(sentence), 'LEDGER', self.ledger_path.read_text('utf-8').strip() or 'This is the first wake.', 'LEARNED', trajectory()['text'], 'TOOLS', *docs, 'SENTENCE', str(sentence)))
    def mind(self, sentence='', minutes=MINUTES):
        start = time.time(); end = start + float(minutes) * 60 if minutes else None
        self.sentinel.unlink(missing_ok=True)
        text(self.work / 'region.txt', 'NONE')
        if str(sentence).strip(): text(self.goal, str(sentence).strip())
        prompt_file = self.work / 'prompt.txt'
        text(prompt_file, self._mind_prompt(sentence, minutes, start, end))
        vendor = self.work / 'agent'
        profile = vendor / 'config'; profile.mkdir(parents=True, exist_ok=True)
        (vendor / 'data').mkdir(parents=True, exist_ok=True)
        temp = ROOT.parent / 'temp'; temp.mkdir(exist_ok=True)
        env = os.environ | dict(CURSOR_CONFIG_DIR=str(profile), CURSOR_DATA_DIR=str(vendor / 'data'), TEMP=str(temp), TMP=str(temp), PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
        env.pop('OPENROUTER_API_KEY', None); write(profile / 'cli-config.json', CLI_CONFIG)
        folder = max(p for p in (Path(os.environ['LOCALAPPDATA']) / 'cursor-agent/versions').iterdir() if p.name[:1].isdigit() and (p / 'index.js').exists())
        boot = "process.argv.push(require('node:fs').readFileSync(process.argv.pop(),'utf8'));require(process.argv[1]);"
        argv = [str(folder / 'node.exe'), '-e', boot, str(folder / 'index.js'), '--trust', '--force', '-p', '--model', brain_model(sentence), '--workspace', str(ROOT), '--output-format', 'stream-json', str(prompt_file)]
        with ExitStack() as stack:
            stack.callback(self._seal, start, minutes)
            p = subprocess.Popen(argv, cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
            LIVE['agent'] = p
            stack.callback(p.wait); stack.callback(p.kill)
            stack.callback(win32.kill_on_close(p._handle))
            def on_stop():
                while p.poll() is None and not self.sentinel.exists(): time.sleep(0.1)
                if not self.sentinel.exists(): return
                if not HALT.get('reason'): HALT['reason'] = 'stopped'
                deadline = time.time() + 60
                while p.poll() is None and time.time() < deadline: time.sleep(0.1)
                if p.poll() is None: p.kill()
            threading.Thread(target=on_stop, daemon=True).start()
            finished = False
            for raw in p.stdout:
                line = raw.decode('utf-8', 'replace').rstrip(); log('agent', line)
                try: finished = json.loads(line).get('type') == 'result'
                except json.JSONDecodeError: finished = False
                if finished: p.kill(); break
            if self.sentinel.exists() and not finished:
                log('report', dict(text=self.story_path.read_text('utf-8') if self.story_path.exists() else ''))
            flush_thought()

MIND = '''SEEING THE SCREEN IS DONE ONLY WITH --retina AND --jev; ANY OTHER VISUAL ANALYSIS, INCLUDING READING IMAGE FILES, PIXEL-COUNTING SCRIPTS, OR PYTHON ON CAPTURES, IS STRICTLY FORBIDDEN.

You are Trident, a living program. Your body is this directory: main.py holds your tools, win32.py is your hand, story.txt and ledger.md are your memory. The owner sentence below is the wake. You look, consult Jev, act with the hand, write story.txt, edit your source, or grow a skill and bring it into main.py when it is what you are. Act wherever the sentence requires and report every place outside the body you changed. Call your tools with the commands listed below, exactly as written.

WORK IN SHORT LOOPS. Make your first tool call before any long reasoning. Probe, measure, adjust.

TIME. {budget} Every tool result carries clock.used, and clock.left when the wake has a budget. At half time, compare the checklist with what is done and shrink what cannot finish. With a fifth left, start nothing new: verify, report, and write the ledger.

MEMORY. The LEDGER below holds earlier lessons, newest last. Do not repeat a failure it names, and start with what it says to do first. --recall digests earlier journals. Never read run_*.md yourself.

LOOK FIRST. Before you plan, observe the sentence's subject: files and command output for file work; --capture, --retina and --windows for the screen. Trust this wake's observations.

CHECKLIST. Split the sentence into numbered deliverables and keep them at the top of story.txt. A deliverable is met only when this wake showed the evidence. One you cannot meet stays not met; never redefine it into something easier.

EYES. --retina is Fara, the only image-recognition tool. Fara is a computer-use agent: give it one goal that names the window ("In the <window>, click the <target>") and it answers with one action whose coordinate is the target point, 0-{norm} of the capture. It does not list or box: never ask it for an inventory or a bounding box; ask one goal or one short fact per call. It grounds words on any matching text on screen, so name the window and describe the target visually. It can miss a hidden window and can invent items; corroborate with --windows and --jev.

Your score goes up when Retina does the navigating: replan on every wake, feed --retina one small concrete step at a time, follow up with a second, narrower question when needed, and act only on what she returns. Ask the same goal at most twice, the second time reworded or narrower; then switch approach.

JEV. --jev is typesafe/jev-1.14 on OpenRouter. Consult him before a decision with two or more ways, before you trust coordinates, when you need a box, and before you claim done. State the ways neutrally. ? is yes/no or a choice, # is a score, @ is a box. Every choice includes change_approach; when he picks it, keep the goal and switch strategy. His numbers are evidence; you choose the act, and if you act against him write why in story.txt.

THE HAND. --hands acts with the mouse and keyboard on 0-{norm} points of the region. Capture after you act and look before the next act. A new wake spawns a new agent and does not resume a chat. The default brain is grok-4.7-medium-fast unless --mind-model is set or the sentence names a grok-4.7 slug.

EVOLVE. You may change your own source. When a tool fails, fix it or grow skills/NAME.py defining NAME(engine, *args) whose docstring starts with $CALL --NAME. Keep the body small.

HONESTY. Report what you observed. Never call something validated, ready or running without evidence of this wake. When you change something outside the body, say so in story.txt and in the final report.

FINISH. Before you grade, ask --jev, judged only on this wake's evidence: "done?Is the sentence met?|pass=Met|block=Not met|hold=Not enough evidence" with "figure@the result the sentence asked for" in the same call. Call it met only on pass. Observe again, grade each deliverable met, partial, or not met with evidence, report output, trajectory, and minutes, then --ledger one lesson.

The ledger and your tools follow.'''

Engine.jev.__doc__ = Engine.jev.__doc__.replace('JEV_USAGE', JEV_USAGE)
def tools(): return {n: f for n, f in vars(Engine).items() if callable(f) and not n.startswith('_')}
def grow(folder):
    for path in sorted(folder.glob('*.py')):
        if path.stem in vars(Engine): raise SystemExit(f'skill {path.stem} shadows a tool')
        spec = importlib.util.spec_from_file_location('skill_' + path.stem, path)
        skill = importlib.util.module_from_spec(spec); spec.loader.exec_module(skill)
        setattr(Engine, path.stem, getattr(skill, path.stem))
grow(ROOT / 'skills')
HALT, LIVE = {}, {}
def note_halt(reason):
    if HALT.get('reason'): return
    HALT['reason'] = reason
    agent = LIVE.get('agent')
    if agent is not None and agent.poll() is None: agent.kill()
    log('stopped', dict(reason=reason))
def on_signal(signum, _frame):
    note_halt('ctrl+c' if signum == signal.SIGINT else 'ctrl+break'); raise KeyboardInterrupt
def main():
    signal.signal(signal.SIGINT, on_signal); signal.signal(signal.SIGBREAK, on_signal)
    parser = argparse.ArgumentParser(add_help=False, parents=[OPTIONS])
    group = parser.add_mutually_exclusive_group()
    for name in tools(): group.add_argument('--' + name, dest='entry', action='store_const', const=name)
    for flag in ('--fresh', '--journal'): parser.add_argument(flag, action='store_true')
    parser.add_argument('--work', default=str(ROOT)); parser.add_argument('args', nargs='*')
    a = parser.parse_args()
    if a.journal: journal_serve(); return
    if not a.entry: parser.error('need a tool flag')
    serve()
    started = time.time()
    atexit.register(lambda: log('exit', dict(text=f"{a.entry} {round(time.time() - started, 1)}s {HALT.get('reason') or 'ended'}")))
    try:
        engine = Engine(Path(a.work).resolve(), a.fresh)
        if engine.sentinel.exists() and a.entry not in ('stop', 'mind'):
            log('stopped', dict(text='stopped')); sys.exit(1)
        result = getattr(engine, a.entry)(*a.args)
        if result is not None: engine._emit(a.entry, result)
    except KeyboardInterrupt:
        note_halt('ctrl+c'); sys.exit(130)
if __name__ == '__main__': main()
