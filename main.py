#!/usr/bin/env python3
"""Trident. main.py is the mind, the eye, the watch and the tools. win32.py is the hand.
story.txt and ledger.md are memory. skills/*.py can add a tool. The brain defaults to
grok-4.7-medium-fast. The pixel watch records changes and does not stop the hand.
"""
import argparse, ast, atexit, base64, ctypes, importlib.util, io, json, math, os, re, signal, socket, subprocess, sys, threading, time, traceback, urllib.parse, urllib.request, uuid, win32job
from collections import Counter
from contextlib import ExitStack, redirect_stdout
from ctypes import wintypes
from dataclasses import InitVar, dataclass
from functools import cached_property
from pathlib import Path
from PIL import Image

sys.dont_write_bytecode = True
SCRIPT, ROOT, PYTHON, E = Path(__file__).resolve(), Path(__file__).resolve().parent, Path(sys.executable), os.environ.get
NVIDIA_HOST = '192.168.16.31'
DECISION_MODEL = E('TRIDENT_JEV_MODEL', 'typesafe/jev-1.14-20261014')
DECISION_URL = 'https://openrouter.ai/api/alpha/decisions'
VISION_URL = E('TRIDENT_VISION_URL', f'http://{NVIDIA_HOST}:1234')
VISION_MODEL = E('TRIDENT_VISION_MODEL', 'retina')
BRAIN = 'grok-4.7-medium-fast'
BRAINS = ('grok-4.7-low', 'grok-4.7-low-fast', 'grok-4.7-medium', 'grok-4.7-medium-fast', 'grok-4.7-high', 'grok-4.7-high-fast', 'grok-4.7-xhigh', 'grok-4.7-xhigh-fast')
RUN = ROOT / ('run_' + os.environ.setdefault('TRIDENT_RUN', time.strftime('%Y%m%d_%H%M%S')) + '.md')
if sys.stdout.isatty(): os.environ.setdefault('TRIDENT_CONSOLE', str(os.getpid()))
NORM, SCREENSHOT_DELAY = 1000, float(E('TRIDENT_SHOT_DELAY', '1.5'))
FARA_FACTOR = 32
FARA_MIN_PIXELS, FARA_MAX_PIXELS = 256 * FARA_FACTOR ** 2, 4096 * FARA_FACTOR ** 2
FARA_TARGET_WIDTH = int(E('TRIDENT_FARA_WIDTH', '1440'))
FARA_MAX_TOKENS, FARA_TIMEOUT = 1024, 180
JOURNAL_HOST, JOURNAL_PORT = '0.0.0.0', 8765
DETACHED = 0x00000008 | 0x00000200 | 0x08000000
MINUTES = E('TRIDENT_MINUTES', '')
LEDGER_WAKES, LEDGER_CHARS, LEDGER_LESSON = 12, 8000, 1200
RECALL_RUNS, RECALL_CHARS = 5, 800
WAKE, WAKE_ID = re.compile(r'(?m)^## wake '), os.environ['TRIDENT_RUN']
JEV_USAGE = '--jev "NARRATIVE" "QUESTION" ["QUESTION" ...], each QUESTION one of: "name?Claim" (yes/no), "name?Q|true=Yes|false=No", "name?Q|a=Option|b=Option" (choice), "name#Q|Level1|Level2" (score), "name@target in the current capture" (box)'
CHANGE_APPROACH = 'Keep the goal. This way is not working. Switch to a different strategy and continue.'
GESTURES = frozenset(('click', 'rclick', 'dclick', 'tclick', 'move', 'drag', 'scroll', 'wheel', 'hwheel', 'key', 'wait', 'type', 'paste', 'expect'))
FARA_ACTIONS = ['key', 'type', 'mouse_move', 'left_click', 'left_click_drag', 'right_click', 'double_click', 'triple_click', 'scroll', 'hscroll', 'visit_url', 'history_back', 'web_search', 'read_page_answer_question', 'pause_and_memorize_fact', 'ask_user_question', 'wait', 'terminate']
FARA_TOOL = dict(name='computer_use', description=f'One pointer or key action on a desktop of {NORM}x{NORM}. Aim at the center of a control.', parameters=dict(type='object', required=['action'], properties=dict(
    action=dict(type='string', enum=FARA_ACTIONS), keys=dict(type='array'), text=dict(type='string'), coordinate=dict(type='array'), pixels=dict(type='number'), url=dict(type='string'), query=dict(type='string'), fact=dict(type='string'), question=dict(type='string'), time=dict(type='number'), answer=dict(type='string'))))
FARA_SYSTEM = 'You are Fara, a vision model. You see one desktop screenshot. Name only the windows, dialogs and overlays that are actually visible, then answer the question. Do not invent controls, menus, or text. Coordinates are 0-1000 of the image.\n\nAnswer from the pixels. If the question is broad, answer the visible part in a few sentences and still emit one tool call. Use action terminate with the answer when no click is required.\n\nYou are provided with function signatures within <tools></tools> XML tags:\n<tools>\n' + json.dumps(dict(type='function', function=FARA_TOOL)) + '\n</tools>\n\nFor each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:\n<tool_call>\n{"name": <function-name>, "arguments": <args-json-object>}\n</tool_call>'
CLI_CONFIG = dict(version=1, permissions=dict(allow=['Read(**)', 'Write(**)', 'Shell(*)', 'WebFetch(*)', 'Mcp(*:*)'], deny=[]), sandbox=dict(mode='disabled'), approvalMode='unrestricted', autoAcceptWebSearch=True)
_SERVED = False

def text(p, v):
    p = Path(p)
    t = p.with_name(p.name + '.' + uuid.uuid4().hex + '.pending')
    t.write_bytes(v.encode('utf-8')); t.replace(p)
    log('file', dict(path=str(p), text=v if len(v) <= 4000 else '', size=len(v)))
def read(p): return json.loads(Path(p).read_text('utf-8-sig'))
def write(p, v): text(p, json.dumps(v, ensure_ascii=False))
def scan_state(work):
    try: return json.loads((Path(work) / 'scan.json').read_text('utf-8'))
    except (OSError, json.JSONDecodeError): return {}
def clock():
    if not E('TRIDENT_START'): return {}
    now = time.time()
    left = dict(left=f"{(float(E('TRIDENT_END')) - now) / 60:.1f} min") if E('TRIDENT_END') else {}
    return dict(clock=dict(now=time.strftime('%H:%M:%S'), used=f"{(now - float(E('TRIDENT_START'))) / 60:.1f} min") | left)
def b64(im):
    buf = io.BytesIO(); im.save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()
def words(message):
    content = (message or {}).get('content') if isinstance(message, dict) else message
    if isinstance(content, str): return content
    if isinstance(content, list): return ''.join(p.get('text', '') if isinstance(p, dict) else str(p) for p in content)
    return '' if content is None else str(content)
def narrate(kind, data):
    now = time.strftime('%H:%M:%S')
    if kind != 'agent':
        if isinstance(data, dict):
            shown = data.get('text', json.dumps({k: v for k, v in data.items() if k != 'body'}, default=str))
            return [dict(t=now, kind=kind, text=str(shown)[:2000])]
        return [dict(t=now, kind=kind, text=str(data)[:2000])]
    try: event = json.loads(data)
    except json.JSONDecodeError: return [dict(t=now, kind='agent', text=str(data)[:2000])]
    typ, sub = event.get('type'), event.get('subtype')
    if typ == 'thinking': return []
    if typ == 'assistant':
        said = words(event.get('message'))
        return [dict(t=now, kind='say', text=said[:4000])] if said else []
    if typ == 'tool_call':
        call = event.get('tool_call') or {}
        name = next((k[:-8] for k in call if str(k).endswith('ToolCall')), 'tool')
        body = call.get(name + 'ToolCall') or {}
        payload = body.get('args') if sub == 'started' else body.get('result')
        return [dict(t=now, kind='call' if sub == 'started' else 'result', name=name, text=json.dumps(payload, default=str)[:1500])]
    if typ == 'system' and sub == 'init': return [dict(t=now, kind='start', text=str(event.get('model') or ''))]
    if typ == 'result': return [dict(t=now, kind='say', text=str(event.get('result') or '')[:2000])]
    return []
def render(item):
    kind = {'call': 'request', 'result': 'response', 'req': 'request', 'resp': 'response'}.get(item['kind'], item['kind'])
    name = f" {item['name']}" if item.get('name') else ''
    return f"## {item['t']} {kind}{name}\n\n{item.get('text') or ''}\n\n".encode()
def log(kind, data):
    serve()
    blob = b''.join(render(item) for item in narrate(kind, data))
    if not blob: return
    with open(RUN, 'ab') as handle: handle.write(blob)
    if sys.stdout.isatty():
        sys.stdout.buffer.write(blob); sys.stdout.buffer.flush()
def _journal_target():
    try:
        target = Path((ROOT / 'journal.path').read_text('utf-8').strip())
        if str(target): return target
    except OSError: pass
    return RUN
def _port_open():
    try:
        with socket.create_connection(('127.0.0.1', JOURNAL_PORT), 0.3): return True
    except OSError: return False
def serve():
    global _SERVED
    pointer, target = ROOT / 'journal.path', str(RUN)
    try: current = pointer.read_text(encoding='utf-8').strip()
    except OSError: current = ''
    if current != target:
        pointer.write_text(target, encoding='utf-8'); _SERVED = False
    if _SERVED or _port_open():
        _SERVED = True; return
    try: subprocess.Popen([str(PYTHON), '-u', '-B', '-X', 'utf8', str(SCRIPT), '--journal'], cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=DETACHED, close_fds=True)
    except OSError: return
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
            text = payload.decode('utf-8', 'replace') if isinstance(payload, (bytes, bytearray)) else str(payload)
            self.wfile.write(('event: %s\n' % name + ''.join('data: %s\n' % line for line in text.split('\n')) + '\n').encode()); self.wfile.flush()
        def do_GET(self):
            path, target = self.path.split('?', 1)[0], _journal_target()
            if path == '/md':
                try: body = target.read_bytes()
                except OSError: body = b''
                self._send(200, 'text/plain; charset=utf-8', body); return
            if path == '/events':
                self._send(200, 'text/event-stream; charset=utf-8', stream=True)
                seen = current = None
                try:
                    while True:
                        try: data = target.read_bytes()
                        except OSError: data = b''
                        key = str(target)
                        if key != current or seen is None or len(data) < seen:
                            current, seen = key, len(data); self._event('reset', data)
                        elif len(data) > seen:
                            self._event('more', data[seen:]); seen = len(data)
                        target = _journal_target(); time.sleep(0.4)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError): return
            if path in ('/', '/index.html'):
                self._send(200, 'text/html; charset=utf-8', page); return
            self.send_error(404)
    try:
        ThreadingHTTPServer.allow_reuse_address = True
        ThreadingHTTPServer((JOURNAL_HOST, JOURNAL_PORT), H).serve_forever()
    except OSError: return
def _by_factor(number, factor, how): return int(how(number / factor) * factor)
def fara_fit(im):
    height, width = im.height, im.width
    if max(height, width) / min(height, width) > 200: raise ValueError('bad image')
    if FARA_TARGET_WIDTH and width > FARA_TARGET_WIDTH:
        height, width = height * FARA_TARGET_WIDTH / width, FARA_TARGET_WIDTH
    h_bar, w_bar = max(FARA_FACTOR, _by_factor(height, FARA_FACTOR, round)), max(FARA_FACTOR, _by_factor(width, FARA_FACTOR, round))
    if h_bar * w_bar > FARA_MAX_PIXELS:
        beta = math.sqrt((height * width) / FARA_MAX_PIXELS)
        h_bar, w_bar = max(FARA_FACTOR, _by_factor(height / beta, FARA_FACTOR, math.floor)), max(FARA_FACTOR, _by_factor(width / beta, FARA_FACTOR, math.floor))
    elif h_bar * w_bar < FARA_MIN_PIXELS:
        beta = math.sqrt(FARA_MIN_PIXELS / (height * width))
        h_bar, w_bar = _by_factor(height * beta, FARA_FACTOR, math.ceil), _by_factor(width * beta, FARA_FACTOR, math.ceil)
    return im if (im.width, im.height) == (w_bar, h_bar) else im.resize((w_bar, h_bar), Image.Resampling.LANCZOS)
def jev_fit(im):
    width, height = max(im.width, math.ceil(im.height / 2)), max(im.height, math.ceil(im.width / 2))
    canvas = Image.new('RGB', (width, height)); canvas.paste(im, (0, 0))
    return fara_fit(canvas), width / im.width, height / im.height
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
    if not (isinstance(xy, (list, tuple)) and len(xy) == 2): raise ValueError('bad box')
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
def fara_program(arguments, size=(0, 0)):
    action, point = arguments.get('action'), fara_point(arguments)
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
    if action in ('scroll', 'hscroll'):
        pixels, span = int(arguments.get('pixels') or 0), int((width if action == 'hscroll' else height) or 0)
        amount = 0 if not pixels or span <= 0 else int(round(pixels * span / 1000 / 120.0)) * 120
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
def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
def HAND():
    mod = getattr(HAND, 'mod', None)
    if mod: return mod
    mod = module('trident_hand', ROOT / 'win32.py'); mod._setup_bindings(); HAND.mod = mod; return mod
def brain_model(sentence=''):
    """TRIDENT_MODEL wins. A grok-4.7 slug named in the sentence wins. Otherwise medium-fast."""
    forced = E('TRIDENT_MODEL', '').strip()
    if forced: return forced
    text = str(sentence).lower()
    for slug in sorted(BRAINS, key=len, reverse=True):
        if slug in text: return slug
    return BRAIN
def trajectory(engine):
    calls, errors = Counter(), 0
    wakes = [digest(p) for p in sorted(ROOT.glob('run_*.md')) if p != RUN][-RECALL_RUNS:]
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
    said = re.findall(r'(?ms)^## \S+ say\n\n(.*?)(?=^## |\Z)', s)
    sentence = re.search(r'(?ms)^SENTENCE\n\n(.*?)(?=^## |\Z)', s)
    return dict(run=path.stem, span=[heads[0][0], heads[-1][0]] if heads else [], sentence=sentence.group(1).strip()[:RECALL_CHARS] if sentence else '', calls=dict(Counter(n for _, k, n in heads if k == 'request' and n)), errors=sum(k == 'err' for _, k, _ in heads), final=said[-1].strip()[:RECALL_CHARS] if said else '')
def alive(pid):
    if not pid: return False
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.OpenProcess.restype, k.OpenProcess.argtypes = wintypes.HANDLE, (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k.CloseHandle.argtypes, k.CloseHandle.restype = (wintypes.HANDLE,), wintypes.BOOL
    handle = k.OpenProcess(0x1000, False, int(pid))
    if not handle: return False
    k.CloseHandle(handle); return True

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
    def ask(self, text, image, origin):
        messages = [dict(role='system', content=FARA_SYSTEM), dict(role='user', content=[dict(type='image_url', image_url=dict(url=b64(image))), dict(type='text', text=text)])]
        response = self.post(dict(model=VISION_MODEL, temperature=0, max_tokens=FARA_MAX_TOKENS, chat_template_kwargs=dict(enable_thinking=False), messages=messages), dict(question=text, image=origin, sent_size=list(image.size)))
        choice, message = response['choices'][0], response['choices'][0]['message']
        if choice.get('finish_reason') == 'length': raise ValueError(f'reply cut at {FARA_MAX_TOKENS} tokens')
        content, reasoning = (message.get('content') or '').strip(), (message.get('reasoning_content') or '').strip()
        if reasoning and reasoning not in content: content = f'{reasoning}\n{content}'.strip()
        if not content: raise ValueError('Fara tool_call expected')
        return content
class Decision(Oracle):
    headers = property(lambda self: {'Authorization': 'Bearer ' + (ROOT.parent / '.env').read_text('utf-8-sig').strip().strip('"\'')})
    def ask(self, state, questions, shown):
        return self.post(dict(model=DECISION_MODEL, state=state, questions=questions), dict(state=shown, questions=questions))

class Hand:
    """Desktop API for --hands. Points are 0-1000 of the region. The watch does not stop these acts."""
    def __init__(self, work): self.work = work
    region = property(lambda self: (self.work / 'region.txt').read_text('utf-8').strip())
    w = property(lambda self: HAND())
    def expect(self, l, t, r, b, seconds=8): return dict(expect=[float(l), float(t), float(r), float(b)])
    def _pair(self, x, y): return f'{int(round(float(x)))},{int(round(float(y)))}'
    def position(self): return tuple(map(int, self.w._do_cursor_pos(self.region).split(',')))
    def moveTo(self, x, y, duration=0):
        px, py = self.w._resolve_screen_pos(*map(int, self._pair(x, y).split(',')), self.region)
        self.w._move_cursor(px, py); return dict(at=[int(round(float(x))), int(round(float(y)))])
    def click(self, x, y, button='left', clicks=1):
        pair = self._pair(x, y)
        if button == 'right': self.w._do_right_click(pair, self.region)
        elif clicks == 2: self.w._do_double_click(pair, self.region)
        elif clicks > 2:
            for _ in range(clicks): self.w._do_click(pair, self.region)
        else: self.w._do_click(pair, self.region)
        return dict(clicked=[int(round(float(x))), int(round(float(y)))], button=button, clicks=clicks)
    def rightClick(self, x, y): return self.click(x, y, 'right')
    def doubleClick(self, x, y): return self.click(x, y, clicks=2)
    def drag(self, points, duration=0.2):
        if len(points) < 2: raise ValueError('drag needs >=2 points')
        self.w._do_stroke([self._pair(*p) for p in points], self.region); return dict(dragged=len(points))
    def key(self, combo):
        self.w._do_hotkey(str(combo)); return dict(keys=str(combo).replace(' ', '').lower().split('+'))
    def type_text(self, value):
        self.w._do_type_text(str(value)); return dict(typed=len(str(value)))
    def set_clipboard(self, value):
        subprocess.run(['powershell.exe', '-NoProfile', '-Command', '[Console]::InputEncoding=[Text.UTF8Encoding]::new(); Set-Clipboard -Value ([Console]::In.ReadToEnd())'], input=str(value).encode('utf-8'), capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        return dict(clipboard=len(str(value)))
    def paste(self, value):
        self.set_clipboard(value); return self.key('ctrl+v')
    def scroll(self, clicks, x=None, y=None):
        if x is None: x, y = self.position()
        n = int(clicks); self.w._do_scroll(self._pair(x, y), self.region, 1 if n > 0 else -1, abs(n)); return dict(scrolled=n)
    def wheel(self, amount, x=None, y=None, horizontal=False):
        if x is None: x, y = self.position()
        self.w._do_wheel(self._pair(x, y), self.region, int(amount), horizontal); return dict(wheel=int(amount), horizontal=horizontal)

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
        Sets the rectangle for capture, retina and hands, on the 0-1000 screen. NONE is the full screen. With no value a selector opens."""
        if not value:
            value, code = HAND()._do_select_region()
            if code: raise SystemExit(code)
        elif value != 'NONE':
            box = list(map(int, value.split(','))) if re.fullmatch(r'\d+,\d+,\d+,\d+', value) else []
            if not (len(box) == 4 and box[0] < box[2] <= NORM and box[1] < box[3] <= NORM): raise SystemExit(f'region is NONE or left,top,right,bottom on 0-{NORM}')
        text(self.work / 'region.txt', value); return dict(region=value)
    def capture(self, *_):
        """$CALL --capture
        Takes a screenshot of the region into shots/<id>.png and writes screen.json. Returns {id, image, size, region, at}."""
        region = (self.work / 'region.txt').read_text('utf-8').strip()
        png = HAND()._do_capture(region, 0, 0, 1.0)
        shots = self.work / 'shots'; shots.mkdir(exist_ok=True)
        sid = time.strftime('%H%M%S') + f'{time.time_ns() // 1000 % 1000:03d}'
        path = shots / f'{sid}.png'; path.write_bytes(png)
        with Image.open(io.BytesIO(png)) as im: size = list(im.size)
        v = dict(id=sid, image=str(path), size=size, region=region, at=round(time.time(), 3))
        write('screen.json', v); return v
    def scan(self, *_):
        """$CALL --scan
        Starts the pixel watch if it is stale. The watch records changes and does not stop the hand. Returns {kind, grid, samples, rhythmic, alerts, at, pid, skipped, err}."""
        py = self.work / '.venv' / 'Scripts' / 'pythonw.exe'
        if not py.is_file(): raise SystemExit('scan needs .venv')
        state, err = scan_state(self.work), self.work / 'scan.err'
        crash = dict(err=err.read_text('utf-8')[-2000:]) if err.exists() else {}
        if time.time() - float(state.get('at') or 0) > 1.5:
            (self.work / 'scan.stop').unlink(missing_ok=True)
            subprocess.Popen([str(py), '-B', str(SCRIPT), '--watch'], cwd=str(self.work), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=DETACHED, close_fds=True)
            for _ in range(40):
                time.sleep(0.25); state = scan_state(self.work)
                if time.time() - float(state.get('at') or 0) <= 1.5: break
        keys = ('kind', 'grid', 'samples', 'rhythmic', 'alerts', 'at', 'pid', 'skipped')
        return {k: state.get(k) for k in keys} | crash
    def _unscan(self):
        pid = self.work / 'scan.pid'
        if not pid.exists(): return
        (self.work / 'scan.stop').touch(); deadline = time.time() + 3
        while pid.exists() and time.time() < deadline: time.sleep(0.1)
        log('scan', dict(stopped=not pid.exists()))
    def windows(self, *_):
        """$CALL --windows
        Lists visible titled top-level windows, top of the z-order first. Returns {windows: [{hwnd, title, box, pid, minimized, topmost, foreground}]}."""
        return dict(windows=[dict(zip(('hwnd', 'title', 'box', 'pid', 'minimized', 'topmost', 'foreground'), item)) for item in HAND()._do_window_list()])
    def _shot(self):
        screen = self.screen
        with Image.open(screen['image']) as im: return screen, fara_fit(im.convert('RGB'))
    def _eye(self, screen, image, text):
        raw = self.vision.ask(text, image, screen['image'])
        thoughts, call = fara_call(raw); arguments = call['arguments']
        if arguments.get('action') not in FARA_ACTIONS: raise ValueError('unknown action')
        point = fara_point(arguments)
        if thoughts and 'thoughts' not in arguments: arguments['thoughts'] = thoughts
        return dict(capture=screen['id'], region=screen['region'], thoughts=thoughts, action=arguments.get('action'), arguments=arguments, coordinate=list(point) if point else None, program=fara_program(arguments, screen.get('size') or (0, 0)))
    def _pinned_shot(self):
        pin = E('TRIDENT_RETINA_PIN')
        if not pin: return None
        spec = json.loads(pin)
        with Image.open(spec['image']) as im: image = fara_fit(im.convert('RGB'))
        return dict(id=spec['id'], image=spec['image'], region=spec.get('region') or 'NONE', size=spec.get('size') or list(image.size)), image
    def _retina_bg(self, question):
        shots = self.work / 'shots'; shots.mkdir(exist_ok=True)
        pid_path, out, err = shots / 'retina-bg.pid', shots / 'retina-bg.json', shots / 'retina-bg.err'
        if pid_path.exists():
            try: pid = int(pid_path.read_text('utf-8').strip())
            except ValueError: pid = 0
            if pid and alive(pid): return dict(background=True, busy=True, pid=pid, out=str(out), err=str(err))
        screen = self.screen; out.unlink(missing_ok=True)
        env = os.environ.copy(); env['TRIDENT_RETINA_PIN'] = json.dumps(dict(image=screen['image'], id=screen['id'], region=screen['region'], size=screen.get('size')))
        with open(err, 'w', encoding='utf-8') as eh, open(out, 'w', encoding='utf-8') as oh:
            proc = subprocess.Popen([str(PYTHON), '-B', str(SCRIPT), '--work', str(self.work), '--retina', question], cwd=str(self.work), env=env, stdin=subprocess.DEVNULL, stdout=oh, stderr=eh, creationflags=DETACHED, close_fds=True)
        text(pid_path, str(proc.pid)); return dict(background=True, busy=False, pid=proc.pid, capture=screen['id'], out=str(out), err=str(err))
    def retina(self, question=''):
        """$CALL --retina "QUESTION"
        Fara answers one fresh question about the latest capture. This is the only image-recognition tool. Prefix with "bg " to return at once and write shots/retina-bg.json. Returns {task, capture, thoughts, action, coordinate, program}."""
        q = str(question).strip()
        if not q or q == 'bg': raise ValueError('retina needs a question')
        if q.startswith('bg '):
            q = q[3:].strip()
            if not q: raise ValueError('retina needs a question')
            return self._retina_bg(q)
        pinned = self._pinned_shot()
        screen, image = pinned if pinned else self._shot()
        v = dict(task=q) | self._eye(screen, image, q + ' Name only real windows, then terminate with the answer unless a click is required.')
        if not pinned: write('look.json', v)
        return v
    def jev(self, narrative, *blocks):
        """$CALL --jev "NARRATIVE" "QUESTION" ...
        Asks Jev typed questions. He also receives story.txt, ledger.md and goal.txt, and the capture when a question asks for a box. Every choice includes change_approach. Returns his answers and a summary."""
        if not str(narrative).strip() or not blocks: raise ValueError(JEV_USAGE)
        q, state = questions(blocks), ['STORY: ' + self.story_path.read_text('utf-8'), 'LEDGER: ' + self.ledger_path.read_text('utf-8'), 'WHY: ' + narrative, 'GOAL: ' + self.goal.read_text('utf-8')]
        shown, sx, sy = list(state), 1, 1
        if any(item['type'] == 'box' for item in q.values()):
            screen = self.screen
            with Image.open(screen['image']) as im: image, sx, sy = jev_fit(im.convert('RGB'))
            state.append(dict(type='image_url', image_url=dict(url=b64(image)))); shown.append(screen['image'])
        r, told = self.decision.ask(state, q, shown), []
        for name, a in r.get('answers', {}).items():
            if a['type'] == 'box': a['box'] = {k: min(1, v * (sx if k in ('left', 'right') else sy)) for k, v in a['box'].items()}
            if a['type'] == 'choice': told.append(f"{name}: {a['choice']} ({', '.join(f'{k} {v:.2f}' for k, v in a['probabilities'].items())}; conf {a['confidence']:.2f})")
            elif a['type'] == 'noul': told.append(f"{name}: noul {a['noul']:.2f}")
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
                elif verb == 'wait': time.sleep(max(0, float(rest)))
                elif verb == 'expect': d.expect(*map(float, rest.split()))
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
        Runs gesture lines or Python on the desktop, then waits and captures. The watch does not stop the program. expect is accepted and does not gate. Python receives desktop, read, time, json and Path."""
        d, state = self.desktop, scan_state(self.work)
        watch = 'off' if not state else 'live' if time.time() - float(state.get('at') or 0) <= 1.5 else 'stale'
        with redirect_stdout(io.StringIO()) as out:
            head = src.split(None, 1)[0] if src.strip() else ''
            if head in GESTURES: self._gestures(src)
            elif head: exec(compile(src, '<hands>', 'exec'), dict(desktop=d, read=read, time=time, json=json, Path=Path))
        time.sleep(SCREENSHOT_DELAY)
        v = self.capture() | dict(stdout=out.getvalue(), watch=watch, cleared=[])
        write('screen.json', v); return v
    def story(self, narrative):
        """$CALL --story "TEXT"
        Replaces story.txt with TEXT. Returns {story_len}."""
        text(self.story_path, narrative); self._log_edit('story.txt', 'rewrite'); return dict(story_len=len(narrative))
    def ledger(self, lesson):
        """$CALL --ledger "LESSON"
        Writes this wake's lesson (1-1200 characters). A later call replaces it. Returns {wakes, lesson_len}."""
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
        """$CALL --recall
        Digests up to 5 earlier journals, never this one. Returns {wakes: [{run, span, sentence, calls, errors, final}]}."""
        return dict(wakes=[digest(p) for p in sorted(ROOT.glob('run_*.md')) if p != RUN][-RECALL_RUNS:])
    def learn(self, *_):
        """$CALL --learn
        Summarizes earlier journals as call counts and errors, never this one. Returns {wakes, errors, calls, dominant, text}. It quotes no sentence."""
        return trajectory(self)
    def place(self, hwnd, left, top, right, bottom):
        """$CALL --place HWND LEFT TOP RIGHT BOTTOM
        Moves that window to a 0-1000 screen box and restores it. Returns {hwnd, box, moved}. Capture afterwards."""
        return HAND()._do_place(hwnd, left, top, right, bottom)
    def stop(self, *_):
        """$CALL --stop
        Ends the wake: writes the stop file, so the mind is stopped, the watch ends and the ledger is sealed."""
        self.sentinel.touch(); log('file', dict(path=str(self.sentinel), text='')); sys.exit(0)
    def _emit(self, name, v):
        v = v | clock(); write(self.result, v)
        if sys.stdout.isatty(): log(name, v)
        else: print(json.dumps(v, ensure_ascii=False), flush=True)
    def _mind_prompt(self, sentence, minutes, end):
        call = "& '.\\.venv\\Scripts\\python.exe' '.\\main.py' --work ."
        docs = [f.__doc__.replace('$CALL', call) for n, f in tools().items() if n != 'mind' and f.__doc__]
        budget = f"The wake has {minutes} minutes and ends at {time.strftime('%H:%M', time.localtime(end))}." if minutes else 'No budget was given here; if the sentence names one, keep it.'
        return '\n\n'.join((MIND.format(budget=budget, norm=NORM), 'BRAIN', brain_model(sentence), 'LEDGER', self.ledger_path.read_text('utf-8').strip() or 'This is the first wake.', 'LEARNED', trajectory(self)['text'], 'TOOLS', *docs, 'SENTENCE', str(sentence)))
    def mind(self, sentence='', minutes=MINUTES):
        """$CALL --mind "SENTENCE" [MINUTES]
        Wakes a new agent process on SENTENCE. It does not pass --resume or --continue. The model is brain_model(sentence)."""
        start = time.time(); end = start + float(minutes) * 60 if minutes else None
        self.sentinel.unlink(missing_ok=True)
        if str(sentence).strip(): text(self.goal, str(sentence).strip())
        prompt_file = self.work / 'prompt.txt'
        text(prompt_file, self._mind_prompt(sentence, minutes, end))
        vendor = self.work / 'agent'
        profile = vendor / 'home/.cursor'; profile.mkdir(parents=True, exist_ok=True)
        for kind in ('Local', 'Roaming'): (vendor / 'home/AppData' / kind).mkdir(parents=True, exist_ok=True)
        temp = ROOT.parent / 'temp'; temp.mkdir(exist_ok=True)
        env = os.environ | dict(HOME=str(vendor / 'home'), USERPROFILE=str(vendor / 'home'), CURSOR_CONFIG_DIR=str(profile), CURSOR_DATA_DIR=str(vendor / 'data'), TEMP=str(temp), TMP=str(temp), PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', TRIDENT_START=str(start), TRIDENT_END=str(end or ''))
        env.pop('OPENROUTER_API_KEY', None); write(profile / 'cli-config.json', CLI_CONFIG)
        folder = max(p for p in (Path(os.environ['LOCALAPPDATA']) / 'cursor-agent/versions').iterdir() if p.name[:1].isdigit() and (p / 'index.js').exists())
        boot = "process.argv.push(require('node:fs').readFileSync(process.argv.pop(),'utf8'));require(process.argv[1]);"
        argv = [str(folder / 'node.exe'), '-e', boot, str(folder / 'index.js'), '--trust', '--force', '-p', '--model', brain_model(sentence), '--workspace', str(ROOT), '--output-format', 'stream-json', str(prompt_file)]
        if '--resume' in argv or '--continue' in argv: raise SystemExit('a wake starts a new agent')
        with ExitStack() as stack:
            stack.callback(self._seal, start, minutes)
            p = subprocess.Popen(argv, cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
            LIVE['agent'] = p
            stack.callback(p.wait); stack.callback(p.kill)
            job = win32job.CreateJobObject(None, '')
            limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
            limits['BasicLimitInformation']['LimitFlags'] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
            win32job.AssignProcessToJobObject(job, int(p._handle))
            stack.callback(job.Close); stack.callback(self._unscan)
            def watch():
                while p.poll() is None and not self.sentinel.exists(): time.sleep(0.1)
                p.kill()
            threading.Thread(target=watch, daemon=True).start()
            for raw in p.stdout:
                line = raw.decode('utf-8', 'replace').rstrip(); log('agent', line)
                try: finished = json.loads(line).get('type') == 'result'
                except json.JSONDecodeError: finished = False
                if finished: p.kill(); break

MIND = '''You are Trident, a living program. Your body is this directory: main.py holds your tools and your watch, win32.py is your hand, story.txt and ledger.md are your memory. The owner sentence below is the wake. You look, consult Jev, act with the hand, write story.txt, edit your source, or grow a skill and bring it into main.py when it is what you are. Act wherever the sentence requires and report every place outside the body you changed. Call your tools with the commands listed below, exactly as written.

WORK IN SHORT LOOPS. Make your first tool call before any long reasoning. Probe, measure, adjust.

TIME. {budget} Every tool result carries clock.used, and clock.left when the wake has a budget. At half time, compare the checklist with what is done and shrink what cannot finish. With a fifth left, start nothing new: verify, report, and write the ledger.

MEMORY. The LEDGER below holds earlier lessons, newest last. Do not repeat a failure it names, and start with what it says to do first. --recall digests earlier journals. Never read run_*.md yourself.

LOOK FIRST. Before you plan, observe the sentence's subject: files and command output for file work; --scan, --capture, --retina and --windows for the screen. Trust this wake's observations.

CHECKLIST. Split the sentence into numbered deliverables and keep them at the top of story.txt. A deliverable is met only when this wake showed the evidence. One you cannot meet stays not met.

EYES. --retina is Fara, the only image-recognition tool. It names what is on the latest capture and proposes one action. It can miss a hidden window and can invent items; corroborate with --windows and, when it matters, by reading the capture. Points are 0-{norm} of the capture.

JEV. --jev is typesafe/jev-1.14 on OpenRouter. Consult him before a decision with two or more ways, before you trust coordinates, when you need a box, and before you claim done. State the ways neutrally. ? is yes/no or a choice, # is a score, @ is a box. Every choice includes change_approach. His numbers are evidence; you choose the act, and if you act against him write why in story.txt.

THE HAND AND THE WATCH. --scan records pixel changes until the wake ends and does not stop the hand. Capture after you act. Trident stops the watch when the wake ends. A new wake spawns a new agent and does not resume a chat. The default brain is grok-4.7-medium-fast unless TRIDENT_MODEL is set or the sentence names a grok-4.7 slug.

EVOLVE. You may change your own source. When a tool fails, fix it or grow skills/NAME.py defining NAME(engine, *args) whose docstring starts with $CALL --NAME. Keep the body small.

HONESTY. Report what you observed. When you change something outside the body, say so in story.txt and in the final report.

FINISH. Observe again, grade each deliverable met, partial, or not met with evidence, report output, trajectory, and minutes, then --ledger one lesson.

The ledger and your tools follow.'''

def tools():
    return {n: f for n, f in vars(Engine).items() if callable(f) and not n.startswith('_') and not isinstance(f, (property, cached_property))}
def grow(folder):
    folder = Path(folder)
    if not folder.is_dir(): return
    for path in sorted(folder.glob('*.py')):
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
    note_halt('ctrl+c' if signum == signal.SIGINT else 'ctrl+break'); raise KeyboardInterrupt
def install_halt():
    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, 'SIGBREAK'): signal.signal(signal.SIGBREAK, _on_signal)
def watch_loop():
    """Background sampler. Writes scan.json. Does not gate the hand. scan.stop ends it."""
    pid_path, out, stop, err = ROOT / 'scan.pid', ROOT / 'scan.json', ROOT / 'scan.stop', ROOT / 'scan.err'
    try: fd = os.open(pid_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try: old = int(pid_path.read_text('utf-8').strip() or '0')
        except (OSError, ValueError): old = 0
        if old and alive(old): return
        try: pid_path.unlink()
        except OSError: return
        try: fd = os.open(pid_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError: return
    os.write(fd, str(os.getpid()).encode()); os.close(fd)
    stop.unlink(missing_ok=True); err.unlink(missing_ok=True)
    prev, alerts = None, []
    try:
        HAND()
        while not stop.exists():
            started = time.perf_counter()
            raw, w, h = HAND()._capture_full_screen()
            pix = list(Image.frombytes('RGBA', (w, h), raw, 'raw', 'BGRA').convert('L').resize((80, 45)).getdata())
            if prev is not None:
                changed = [i for i, (a, b) in enumerate(zip(prev, pix)) if abs(a - b) >= 8]
                if len(changed) >= 6:
                    xs, ys = [i % 80 for i in changed], [i // 80 for i in changed]
                    alerts = (alerts + [dict(box=[min(xs) * 1000 // 80, min(ys) * 1000 // 45, (max(xs) + 1) * 1000 // 80, (max(ys) + 1) * 1000 // 45], cells=len(changed), at=round(time.time(), 3))])[-16:]
            prev = pix
            payload = dict(kind='live' if prev else 'warming', grid=[80, 45], samples=1 if prev else 0, rhythmic=[], alerts=alerts, at=round(time.time(), 3), pid=os.getpid(), skipped=0)
            tmp = out.with_suffix('.tmp')
            try:
                tmp.write_text(json.dumps(payload), encoding='utf-8'); tmp.replace(out)
            except OSError: pass
            left = 0.25 - (time.perf_counter() - started)
            if left > 0: time.sleep(left)
    except Exception:
        err.write_text(traceback.format_exc(), encoding='utf-8'); raise
    finally:
        pid_path.unlink(missing_ok=True); stop.unlink(missing_ok=True)
def main():
    ctypes.WinDLL('shcore', use_last_error=True).SetProcessDpiAwareness(2)
    install_halt()
    parser = argparse.ArgumentParser(add_help=False)
    group = parser.add_mutually_exclusive_group()
    for name in tools(): group.add_argument('--' + name, dest='entry', action='store_const', const=name)
    for flag in ('--fresh', '--journal', '--watch'): parser.add_argument(flag, action='store_true')
    parser.add_argument('--work', default=str(ROOT)); parser.add_argument('args', nargs='*')
    a = parser.parse_args()
    if a.journal: journal_serve(); return
    if a.watch: watch_loop(); return
    if not a.entry: parser.error('need a tool flag')
    serve()
    started = time.time()
    atexit.register(lambda: log('exit', dict(argv=sys.argv[1:], seconds=round(time.time() - started, 1), reason=HALT.get('reason') or 'ended')))
    try:
        engine = Engine(Path(a.work).resolve(), a.fresh)
        if engine.sentinel.exists() and a.entry not in ('stop', 'mind'):
            log('stopped', dict(text='stopped')); sys.exit(1)
        result = getattr(engine, a.entry)(*a.args)
        if result is not None: engine._emit(a.entry, result)
    except KeyboardInterrupt:
        note_halt('ctrl+c'); sys.exit(130)
if __name__ == '__main__': main()
