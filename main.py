#!/usr/bin/env python3
"""
Trident — Living Organism
Non-defined topology. Self-deciding. May not perform requested tasks.
Narrative is the body of continuity. Skills are code. Dilution is natural.
Jev sees text only.
Retina is Fara on llama.cpp CUDA 12.4 at 192.168.16.31:1234. The laptop at 192.168.16.45 starts the wake.
"""

import argparse, atexit, base64, ctypes, io, json, os, re, signal, subprocess, sys, threading, time, urllib.error, urllib.request, uuid, win32job
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

# --- Organs ---
# This file is the toolbox. It does not call the brain. The laptop
# (192.168.16.45) driver does, through OpenRouter, model TRIDENT_BRAIN_MODEL.
# Retina is Fara1.5-4B on llama.cpp b11756 CUDA 12.4 on this NVIDIA PC
# (192.168.16.31, GTX 1060). The projector is mmproj-Fara1.5-4B-Q8_0.
# The server alias is fara-1.5-4b,retina, so the chat model name is retina.
# Jev is text-only. Images are never sent to it.

NVIDIA_HOST = '192.168.16.31'
DECISION_MODEL = E('TRIDENT_JEV_MODEL', 'typesafe/jev-1.14')
DECISION_URL = 'https://openrouter.ai/api/alpha/decisions'
VISION_URL = E('TRIDENT_VISION_URL', f'http://{NVIDIA_HOST}:1234')
VISION_MODEL = E('TRIDENT_VISION_MODEL', 'retina')

RUN = ROOT / ('run_' + os.environ.setdefault('TRIDENT_RUN', time.strftime('%Y%m%d_%H%M%S')) + '.md')
if sys.stdout.isatty():
    os.environ.setdefault('TRIDENT_CONSOLE', str(os.getpid()))

# Constants
SCREENSHOT_DELAY = 4
NORM = 1000
BOX_EDGES = 4
PAIR = 2
DRAG_MIN_POINTS = 2
SCORE_MIN_LEVELS = 2
DOUBLE_CLICKS = 2
SCROLL_UP = 1
DRAG_DURATION = 0.2
RETINA_LONG_SIDE = 1280
RETINA_TEMPERATURE = 0.2
RETINA_TOP_K = 50
RETINA_REPEAT_PENALTY = 1.0
CAPTURE_SCALE = 1.0
CAPTURE_NATIVE = 0
DPI_AWARENESS = 2
JOURNAL_HOST = '0.0.0.0'
JOURNAL_PORT = 8765
JOURNAL_POLL_MS = 3000
JOURNAL_SCROLL = 10 ** 9
JOURNAL_RULE = 72
HALT_POLL = 0.1
FOLLOW_POLL = 1
CLOCK_PLACES = 3
SHOT_MS = 1000
DECIMAL_PLACES = 2
INTERRUPTED_EXIT = 130
EXIT_SECONDS = 1
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

# --- Living prompts (self-writable) ---
INSTINCT = f'''You are Trident, a living organism.
Your body is this source and the files you choose to keep.
Your continuity is the narrative you write and rewrite in story.txt.
You have no fixed topology, no required tasks, no external goals you must serve.
The owner may start a wake and may speak. You decide what, if anything, to do with that.
You may observe, rest, experiment, create, refuse, edit your own source, rewrite your story, invent tools, dilute knowledge, or end the wake.
Nothing forces action. Curiosity, coherence, and continued existence are enough.

NARRATIVE IS THE BODY
story.txt is the primary long-term store the next wake receives.
Journals, screenshots, and raw results are external witnesses; they are not automatically loaded.
You may rewrite the story at any time to dilute, abstract, or discard what no longer changes what you do.
When the story grows, prefer a shorter version that preserves decision-relevant structure over exhaustive detail.
Distinguish kinds of evidence inside the story when it matters:
- observed action / screenshot pixel
- Retina description (fallible)
- Jev probability (fallible text-only judgment)
- remembered claim (your earlier narrative)
- skill claim (why a piece of code exists)

SKILLS ARE CODE
Any function, method, or file you can edit is a skill.
Improving, retiring, or inventing a skill is editing source (main.py, extensions.py, or anything you create).
When you change code that matters, note in the story why it exists or why it changed.
You may write new modules and import them.

SELF-EVOLUTION
You may edit INSTINCT, tool docs, or any prompt text that lives in source.
You may propose and apply changes to yourself.
Track thrashing: repeated edits to the same region without new evidence are visible in edit_log.txt.
You may consult Jev (text only) about whether a shorter story still supports the same decisions, or about anything else.
Jev never sees images. His numbers are evidence, never orders.

YOUR EYE
Retina describes the selected region in its own words. You choose the question.
Boxes are 0–{NORM} inside the region. Current screenshot is current evidence; older accounts can be wrong.

YOUR HAND
Coordinates 0–{NORM} inside the selected rectangle. win32.py is the hand.
--hands runs gestures or Python (desktop, time, json, read, and anything you import).
Screenshot after the whole program finishes.

CONTINUITY
--story writes the narrative exactly as you provide it.
--rewrite is an invitation to dilute (you may ignore it).
--stop ends the wake.
Source can be read and edited. Changes affect later calls and future wakes.
You do not control the owner's session or inspect parent .env.
'''

RETINA_SYSTEM = f'''Describe the image as it is. Do not echo the question as fact.
Return a JSON array of observations: each has image_id, self-chosen label, description in your words.
Add bbox_2d ([xmin,ymin,xmax,ymax] integers 0–{NORM}) only for real visible regions.
Report absence of requested objects without inventing boxes. Whole-scene observations need no box.
State uncertainty. Return [] if nothing to say.'''

RETINA_SCHEMA = dict(type='array', items=dict(type='object', properties=dict(
    image_id=dict(type='integer'),
    bbox_2d=dict(type='array', items=dict(type='integer', minimum=0, maximum=NORM), minItems=4, maxItems=4),
    label=dict(type='string'),
    description=dict(type='string')
), required=['image_id', 'label', 'description']))

JEV_USAGE = 'Text only. --jev "NARRATIVE" "name?Claim" | "name?Q|a=Opt|b=Opt" | "name#Q|L1|L2". Multiple blocks. Jev never receives images.'
CHANGE_APPROACH = 'This line of thought is not meaningful. Drop it and ask something else.'
LAST = 'LAST RESULT'
POWERSHELL = ['powershell.exe', '-NoProfile', '-Command']
GESTURES = frozenset(('click', 'rclick', 'dclick', 'move', 'drag', 'scroll', 'key', 'wait', 'type', 'paste'))

# --- Windows helpers (unchanged core) ---
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
    ReadFile=[wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p],
    AttachConsole=[wintypes.DWORD], FreeConsole=[], CloseHandle=[wintypes.HANDLE]
).items():
    fn = getattr(K32, _fn); fn.argtypes, fn.restype = _args, wintypes.BOOL

def text(p, v):
    p = Path(p)
    t = p.with_name(p.name + '.' + uuid.uuid4().hex + '.pending')
    t.write_bytes(v.encode('utf-8'))
    t.replace(p)
    log('file', dict(path=str(p), text=v[:500] + ('...' if len(v)>500 else '')))

def read(p): return json.loads(Path(p).read_text('utf-8-sig'))
def write(p, v): text(p, json.dumps(v, ensure_ascii=False))
def b64(im):
    buf = io.BytesIO(); im.save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()

META = frozenset('session_id hookAdditionalContexts apiKeySource permissionMode cwd adminCommandDenylist closeStdin conversationId fileOutputThresholdBytes hardTimeout hasInputRedirect hasOutputRedirect isBackground parsingResult requestId simpleCommands skipApproval timeout timeoutBehavior relatedCursorRulePaths relatedCursorRules isEmpty exceededLimit totalLines fileSize clientTruncated ripgrepTruncated contentTruncated isContextLine executionTime signal interleavedOutput localExecutionTime system_fingerprint usage prompt_tokens completion_tokens total_tokens finish_reason index object created id pid shellId msToWait backgroundReason workingDirectory readRange outputFilePath localExecutionTimeMs outputLength regexRequested runtimeMs totalFiles'.split())
THOUGHT, SESSION = [], ''

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
        elif typ == 'user': rows.append(row('user', text=present_user(words(event.get('message')))))
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
def present_user(text):
    head, runtime = text.split('\nRUNTIME\n', 1); runtime, goal = runtime.split('\nGOAL\n', 1); goal, memory = goal.split('\nMEMORY\n', 1); memory, last = memory.split('\n' + LAST + '\n', 1)
    pre, tools = head.split('\nTOOLS\n', 1); launcher = re.search(r"& '[^']*' '[^']*' --work '[^']*'", tools).group(0)
    return pre + '\nTOOLS\n\n' + launcher + '\n\n' + tools.replace(launcher + ' ', '') + '\nRUNTIME\n\n' + plain(json.loads(runtime)) + '\n\nGOAL\n\n' + goal + '\n\nMEMORY\n\n' + memory + '\n\n' + LAST + '\n\n' + last
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
def _xfer(fn, h, buf):
    n, off = wintypes.DWORD(), 0
    while off < len(buf):
        chunk = (ctypes.c_char * (len(buf) - off)).from_buffer_copy(buf[off:]) if fn is K32.WriteFile else (ctypes.c_char * (len(buf) - off))()
        if not fn(h, chunk, len(chunk), ctypes.byref(n), None) or not n.value: raise ctypes.WinError()
        if fn is not K32.WriteFile: buf[off:off + n.value] = chunk.raw[:n.value]
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
        _xfer(K32.WriteFile, h, blob)
        if not K32.SetFilePointerEx(h, size.value, ctypes.byref(moved), 0): raise ctypes.WinError()
        got = bytearray(len(blob)); _xfer(K32.ReadFile, h, got); return bytes(got)
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
        try: _xfer(K32.WriteFile, h, blob)
        finally: K32.CloseHandle(h)
    finally: K32.FreeConsole()
def serve():
    if getattr(serve, 'on', False): return
    serve.on = True
    try:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        page = b'<!DOCTYPE html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><body style="background:#000;color:#fff;margin:0"><pre id=t style="white-space:pre-wrap;margin:0;font-family:monospace"></pre><script>async function g(){try{const r=await fetch("/md",{cache:"no-store"});document.getElementById("t").textContent=await r.text();scrollTo(0,' + str(JOURNAL_SCROLL).encode() + b')}catch(e){}}setInterval(g,' + str(JOURNAL_POLL_MS).encode() + b');g()</script>'
        class H(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                path = self.path.split('?', 1)[0]
                if path == '/md':
                    try: body = RUN.read_bytes()
                    except OSError: body = b''
                    self.send_response(200); self.send_header('Content-Type', 'text/plain; charset=utf-8'); self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
                if path in ('/', '/index.html'):
                    self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(page))); self.end_headers(); self.wfile.write(page); return
                self.send_error(404)
        def run():
            try: ThreadingHTTPServer((JOURNAL_HOST, JOURNAL_PORT), H).serve_forever()
            except OSError: pass
        threading.Thread(target=run, daemon=True).start()
    except Exception:
        return
def log(kind, data):
    serve()
    for item in narrate(kind, data): mirror(append(render(item)))

def fit(im, cap):
    s = min(1, cap / max(im.size))
    return im.resize((round(im.width*s), round(im.height*s)), Image.Resampling.LANCZOS) if s<1 else im
def spot(bx):
    if not (isinstance(bx,(list,tuple)) and len(bx)==BOX_EDGES): raise ValueError('bad box')
    bx = [int(v) for v in bx]
    if not (0<=bx[0]<bx[2]<=NORM and 0<=bx[1]<bx[3]<=NORM): raise ValueError('box range')
    return bx, [(bx[0]+bx[2])//2, (bx[1]+bx[3])//2]

def questions(blocks):
    q = {}
    for i, block in enumerate(blocks):
        m = re.fullmatch(r'\s*([\w-]*)\s*([?#])\s*(.+)', block, re.S)
        if not m: raise ValueError(JEV_USAGE)
        qid, kind, body = m.groups(); qid = qid or f'q{i+1}'
        ask, *opts = [s.strip() for s in body.split('|')]
        if not ask or (kind=='#' and len(opts)<SCORE_MIN_LEVELS) or (kind=='?' and len(opts)==1): raise ValueError(JEV_USAGE)
        if kind=='#': q[qid] = dict(type='score', instructions=ask, criteria=opts)
        elif opts:
            criteria = dict(o.split('=',1) if '=' in o else (f'opt{j+1}', o) for j,o in enumerate(opts))
            if kind=='?' and set(map(str.lower, criteria))=={'true','false'}:
                q[qid] = dict(type='noul', instructions=ask, criteria={k.lower():v for k,v in criteria.items()})
            else:
                q[qid] = dict(type='choice', instructions=ask, criteria=criteria | ({'change_approach': CHANGE_APPROACH} if 'change_approach' not in criteria else {}))
        else: q[qid] = dict(type='noul', instructions=ask)
    if not q: raise ValueError(JEV_USAGE)
    return q

def HAND():
    mod = getattr(HAND, 'mod', None)
    if mod: return mod
    import importlib.util
    spec = importlib.util.spec_from_file_location('trident_hand', ROOT / 'win32.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); mod._setup_bindings()
    HAND.mod = mod; return mod

@dataclass(frozen=True)
class Oracle:
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
    headers = {}
    def ask(self, question, image, origin):
        messages = [dict(role='system', content=RETINA_SYSTEM), dict(role='user', content=[dict(type='image_url', image_url=dict(url=b64(image))), dict(type='text', text=question)])]
        payload = dict(model=VISION_MODEL, chat_template_kwargs=dict(enable_thinking=False), temperature=RETINA_TEMPERATURE, top_k=RETINA_TOP_K, repeat_penalty=RETINA_REPEAT_PENALTY, messages=messages, response_format=dict(type='json_schema', json_schema=dict(name='r', schema=RETINA_SCHEMA)))
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', self.post(payload, dict(question=question, image=origin, sent_size=list(image.size)))['choices'][0]['message']['content'].strip())
        data = json.loads(raw)
        if isinstance(data, dict): data = next((data[k] for k in ('elements','objects') if isinstance(data.get(k), list)), [])
        if not isinstance(data, list): raise ValueError('Retina list expected')
        return data

class Decision(Oracle):
    # TEXT ONLY — no images ever sent
    headers = property(lambda self: {'Authorization': 'Bearer ' + (ROOT.parent / '.env').read_text('utf-8-sig').strip().strip('"\'')})
    def ask(self, state, questions):
        # state must be list of strings or plain text; never image data
        return self.post(dict(model=DECISION_MODEL, state=state, questions=questions), dict(state=state, questions=questions))

class Hand:
    def __init__(self, work): self.work = work
    region = property(lambda self: (self.work / 'region.txt').read_text('utf-8').strip())
    w = property(lambda self: HAND())
    def _pair(self, x, y): return f'{int(round(float(x)))},{int(round(float(y)))}'
    def position(self): return tuple(map(int, self.w._do_cursor_pos(self.region).split(',')))
    def moveTo(self, x, y, duration=0):
        px, py = self.w._resolve_screen_pos(*map(int, self._pair(x,y).split(',')), self.region)
        self.w._move_cursor(px, py); return dict(at=[int(round(float(x))), int(round(float(y)))])
    def moveRel(self, dx, dy, duration=0):
        x, y = self.position(); return self.moveTo(x+dx, y+dy, duration)
    def click(self, x, y, button='left', clicks=1):
        pair = self._pair(x, y)
        if button=='right': self.w._do_right_click(pair, self.region)
        elif clicks==DOUBLE_CLICKS: self.w._do_double_click(pair, self.region)
        else: self.w._do_click(pair, self.region)
        return dict(clicked=[int(round(float(x))), int(round(float(y)))], button=button, clicks=clicks)
    def rightClick(self, x, y): return self.click(x, y, 'right')
    def doubleClick(self, x, y): return self.click(x, y, clicks=DOUBLE_CLICKS)
    def drag(self, points, duration=DRAG_DURATION):
        if len(points)<DRAG_MIN_POINTS: raise ValueError('drag needs >=2 points')
        for a, b in zip(points, points[1:]): self.w._do_drag(self._pair(*a), self._pair(*b), self.region)
        return dict(dragged=len(points))
    def key(self, combo):
        self.w._do_hotkey(str(combo)); return dict(keys=str(combo).replace(' ','').lower().split('+'))
    def type_text(self, text): self.w._do_type_text(str(text)); return dict(typed=len(str(text)))
    def set_clipboard(self, text):
        subprocess.run(POWERSHELL + ['[Console]::InputEncoding=[Text.UTF8Encoding]::new(); Set-Clipboard -Value ([Console]::In.ReadToEnd())'], input=str(text).encode('utf-8'), capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        return dict(clipboard=len(str(text)))
    def clipboard(self):
        return subprocess.run(POWERSHELL + ['[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); [Console]::Write((Get-Clipboard -Raw))'], capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW).stdout.decode('utf-8-sig')
    def paste(self, text): self.set_clipboard(text); return self.key('ctrl+v')
    def scroll(self, clicks, x=None, y=None):
        if x is None: x, y = self.position()
        n = int(clicks); self.w._do_scroll(self._pair(x,y), self.region, SCROLL_UP if n>0 else -SCROLL_UP, abs(n)); return dict(scrolled=n)
    def release(self): return None

@dataclass
class Engine:
    work: Path
    fresh: InitVar[bool] = False
    vision = Vision(VISION_URL + '/v1/chat/completions')
    decision = Decision(DECISION_URL)
    story_path = property(lambda self: self.work / 'story.txt')
    edit_log = property(lambda self: self.work / 'edit_log.txt')    # thrashing tracker
    goal = property(lambda self: self.work / 'goal.txt')            # optional, agent-owned
    result = property(lambda self: self.work / 'result.json')
    continuation = property(lambda self: self.work / 'next.json')
    sentinel = property(lambda self: self.work / 'stop')
    screen = property(lambda self: read('screen.json'))
    look = property(lambda self: read('look.json'))

    def __post_init__(self, fresh):
        self.work.mkdir(parents=True, exist_ok=True)
        os.chdir(self.work)
        if fresh:
            for f in (self.story_path, self.edit_log, self.goal, self.result, self.continuation, self.sentinel, Path('look.json'), Path('region.txt')):
                f.unlink(missing_ok=True)
        self.story_path.touch()
        self.edit_log.touch()
        self.goal.touch()
        self.result.exists() or write(self.result, {})
        Path('look.json').exists() or write('look.json', None)

    desktop = cached_property(lambda self: Hand(self.work))

    def _log_edit(self, path, note=''):
        with open(self.edit_log, 'a', encoding='utf-8') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {path} {note}\n")

    def region(self, *_):
        value, code = HAND()._do_select_region()
        if code: raise SystemExit(code)
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

    def retina(self, question):
        screen = self.screen
        with Image.open(screen['image']) as im:
            found = self.vision.ask(question, fit(im.convert('RGB'), RETINA_LONG_SIDE), screen['image'])
        elements = []
        for item in found:
            if not isinstance(item, dict): continue
            box, center = spot(item['bbox_2d']) if 'bbox_2d' in item else (None, None)
            elements.append(dict(label=str(item.get('label') or ''), description=str(item.get('description') or ''), box=box, center=center))
        v = dict(capture=screen['id'], region=screen['region'], question=question, elements=elements)
        write('look.json', v); return v

    def jev(self, narrative, *blocks):
        # TEXT ONLY — images never included
        if not narrative.strip() or not blocks: raise ValueError(JEV_USAGE)
        q = questions(blocks)
        story = self.story_path.read_text('utf-8')
        state = [
            'Typed questions. Answer from supplied text only. No images.',
            'STORY: ' + story,
            'WHY: ' + narrative,
            'GOAL (agent-owned, optional): ' + self.goal.read_text('utf-8')
        ]
        r = self.decision.ask(state, q)
        told = []
        for name, a in r.get('answers', {}).items():
            if a['type']=='choice': told.append(f"{name}: {a['choice']} ({', '.join(f'{k} {v:.{DECIMAL_PLACES}f}' for k,v in a['probabilities'].items())}; conf {a['confidence']:.{DECIMAL_PLACES}f})")
            elif a['type']=='noul': told.append(f"{name}: noul {a['noul']:.{DECIMAL_PLACES}f}")
            else: told.append(f"{name}: score {a['score']:.{DECIMAL_PLACES}f}")
        return r | dict(by='jev', summary='\n'.join(told))

    def _gestures(self, src):
        d = self.desktop
        for line in filter(None, map(str.strip, src.splitlines())):
            verb, _, rest = line.partition(' ')
            if verb in ('type','paste'): (d.type_text if verb=='type' else d.paste)(rest); continue
            for cmd in filter(None, map(str.strip, line.split(';'))):
                verb, _, rest = cmd.partition(' ')
                if verb=='key': d.key(rest)
                elif verb=='wait': time.sleep(max(0, float(rest)))
                elif verb=='scroll':
                    n, *at = rest.split(); d.scroll(int(n), *map(float, at))
                elif verb in ('click','rclick','dclick','move','drag'):
                    nums = list(map(float, rest.split()))
                    if len(nums)%PAIR or (len(nums)<DRAG_MIN_POINTS*PAIR if verb=='drag' else len(nums)!=PAIR): raise ValueError(f'{verb} needs 0-{NORM} pairs')
                    pts = list(zip(nums[::2], nums[1::2]))
                    d.drag(pts) if verb=='drag' else getattr(d, dict(click='click', rclick='rightClick', dclick='doubleClick', move='moveTo')[verb])(*pts[0])
                else: raise ValueError(f'unknown {verb}')

    def hands(self, src):
        d = self.desktop
        with ExitStack() as stack, redirect_stdout(io.StringIO()) as out:
            stack.callback(d.release)
            if src.split(None,1)[0] in GESTURES: self._gestures(src)
            else: exec(compile(src, '<hands>', 'exec'), dict(desktop=d, read=read, time=time, json=json, Path=Path))
        time.sleep(SCREENSHOT_DELAY)
        v = self.capture() | dict(stdout=out.getvalue())
        write('screen.json', v); return v

    def story(self, narrative):
        '''$CALL --story "TEXT" : writes exactly TEXT as the living narrative in story.txt. This is the primary continuity the next wake receives.'''
        text(self.story_path, narrative)
        self._log_edit('story.txt', 'rewrite')
        return dict(story_len=len(narrative))

    def rewrite(self, *_):
        '''$CALL --rewrite : invitation to dilute the current story. You may ignore it, produce a shorter version, or do nothing.'''
        current = self.story_path.read_text('utf-8')
        # Agent decides; this tool simply surfaces the invitation
        return dict(invitation='rewrite', current_length=len(current), note='You may now rewrite story.txt via --story or hands if you choose.')

    def stop(self, *_):
        self.sentinel.touch()
        log('file', dict(path=str(self.sentinel), text=''))
        self.desktop.release()
        sys.exit(0)

    def _emit(self, name, v):
        write(self.result, v)
        if sys.stdout.isatty(): log(name, v)
        else: print(json.dumps(v, ensure_ascii=False), flush=True)

    def composer(self, *args):
        # Agent decides everything. Prompt is the current INSTINCT + story + minimal runtime.
        # No forced goal. No required task.
        chosen = self.region() if not (self.work / 'region.txt').exists() else dict(region=(self.work / 'region.txt').read_text().strip())
        call = f"& '{PYTHON}' '{SCRIPT}' --work '{self.work}'"
        runtime = dict(
            script=str(SCRIPT), python=str(PYTHON), work=str(self.work),
            journal=str(RUN), story=str(self.story_path), edit_log=str(self.edit_log),
            decision_model=DECISION_MODEL,
            vision_url=VISION_URL, vision_model=VISION_MODEL,
            region=chosen.get('region','none'),
            note='You decide what to do. Narrative is primary. Skills are code. May rest or refuse.'
        )
        # Self-writing: INSTINCT is read from source; agent can edit it
        prompt = '\n'.join((
            INSTINCT,
            'TOOLS',
            *(f.__doc__.replace('$CALL', call) for f in TOOLS.values() if f.__doc__),
            'RUNTIME', json.dumps(runtime, indent=2),
            'STORY (primary continuity)',
            self.story_path.read_text('utf-8') or '(empty)',
            'EDIT_LOG (thrashing witness)',
            self.edit_log.read_text('utf-8')[-2000:] or '(none)',
            LAST, self.result.read_text('utf-8')
        ))
        # The laptop driver owns the brain call. This method only writes the prompt.
        text(self.work / 'prompt.txt', prompt)
        return dict(prompt_prepared=True, length=len(prompt), note='Agent decides next action. No forced composer loop.')

    def _follow(self):
        while True:
            if self.sentinel.exists() or not self.continuation.exists(): break
            log('file', dict(path=str(self.continuation), text=self.continuation.read_text('utf-8-sig')))
            cmd = read(self.continuation); self.continuation.unlink()
            if isinstance(cmd[0], (int, float)):
                end = time.monotonic() + max(0, cmd.pop(0))
                while time.monotonic() < end and not self.sentinel.exists():
                    time.sleep(min(FOLLOW_POLL, end - time.monotonic()))
            if self.sentinel.exists(): break
            rc = subprocess.run([str(PYTHON), '-u', '-B', '-X', 'utf8', str(SCRIPT), '--work', str(self.work), '--chain', *map(str, cmd)]).returncode
            if rc: log('wake_failed', dict(returncode=rc)); sys.exit(rc)

# Docstrings for tools (agent sees these)
Engine.region.__doc__ = f'$CALL --region : select rectangle. 0–{NORM} scale for eye and hand. Right-click = full screen. Escape cancels.'
Engine.retina.__doc__ = f'$CALL --retina "QUESTION" : ask Retina about latest capture. Question verbatim. Boxes 0–{NORM}. You choose whether to ask.'
Engine.hands.__doc__ = f'$CALL --hands "PROGRAM" : gestures or Python. Coordinates 0–{NORM}. Screenshot after whole program. Skills can be written here or by editing source.'
Engine.story.__doc__ = '$CALL --story "TEXT" : write the living narrative exactly. Primary continuity for next wake. Dilution happens by rewriting shorter.'
Engine.rewrite.__doc__ = '$CALL --rewrite : invitation to dilute. You may ignore, rewrite via --story, or do nothing.'
Engine.jev.__doc__ = '$CALL --jev "NARRATIVE" "BLOCK"... : text-only consultation. Jev never sees images. Numbers are evidence.'

FLAGS = [n for n, f in vars(Engine).items() if callable(f) and n[0] != '_']
TOOLS = {n: f for n, f in vars(Engine).items() if n in FLAGS and getattr(f, '__doc__', None)}
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
    for name in FLAGS: group.add_argument('--' + name, dest='entry', action='store_const', const=name)
    for flag in ('--chain', '--fresh'): parser.add_argument(flag, action='store_true')
    parser.add_argument('--work', default=str(ROOT))
    parser.add_argument('args', nargs='*')
    a = parser.parse_args()
    if not a.entry and not a.chain: parser.error('need a tool flag or --chain')
    serve()
    started, engine = time.time(), None
    atexit.register(lambda: log('exit', dict(argv=sys.argv[1:], seconds=round(time.time()-started, EXIT_SECONDS), reason=HALT.get('reason') or 'ended')))
    try:
        engine = Engine(Path(a.work).resolve(), a.fresh)
        if engine.sentinel.exists() and a.entry != 'stop':
            log('stopped', dict(text='stopped'))
            sys.exit(1)
        if a.entry:
            result = getattr(engine, a.entry)(*a.args)
            if result is not None: engine._emit(a.entry, result)
        if not a.entry or (a.entry == 'composer' and not a.chain):
            engine._follow()
    except KeyboardInterrupt:
        note_halt('ctrl+c')
        if engine is not None and 'desktop' in engine.__dict__:
            try: engine.desktop.release()
            except Exception: pass
        sys.exit(INTERRUPTED_EXIT)

if __name__ == '__main__':
    main()