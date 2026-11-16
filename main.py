"""Trident's body. The heartbeat at the bottom gives turns, images and memory; everything above it is what a turn can use.
From a script: import sys; sys.path.insert(0, r'<this folder>'); import <this file's name> as body."""
import argparse, base64, ctypes, inspect, io, json, math, mimetypes, os, queue, random, shutil, subprocess, sys, threading, time, urllib.error, urllib.request
from contextlib import contextmanager
from ctypes import wintypes as W
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageGrab

BODY = Path(__file__).resolve()
ROOT = BODY.parent
JEV = 'typesafe/jev-1.14'
BUTTONS = dict(left=0x02, right=0x08, middle=0x20)
KEYS = dict(ctrl=0x11, shift=0x10, alt=0x12, win=0x5B, enter=0x0D, esc=0x1B, tab=0x09, space=0x20, backspace=0x08, delete=0x2E, insert=0x2D, home=0x24, end=0x23,
            pageup=0x21, pagedown=0x22, left=0x25, up=0x26, right=0x27, down=0x28, **{f'f{number}': 0x6F + number for number in range(1, 13)})
user32, kernel32 = ctypes.WinDLL('user32'), ctypes.WinDLL('kernel32')
user32.SetProcessDPIAware()
class Mouse(ctypes.Structure): _fields_ = [('x', W.LONG), ('y', W.LONG), ('data', W.DWORD), ('flags', W.DWORD), ('time', W.DWORD), ('extra', ctypes.c_size_t)]
class Key(ctypes.Structure): _fields_ = [('code', W.WORD), ('unit', W.WORD), ('flags', W.DWORD), ('time', W.DWORD), ('extra', ctypes.c_size_t)]
class Either(ctypes.Union): _fields_ = [('mouse', Mouse), ('key', Key)]
class Input(ctypes.Structure): _fields_ = [('kind', W.DWORD), ('either', Either)]

def _send(*inputs):
    user32.SendInput(len(inputs), ctypes.byref((Input * len(inputs))(*inputs)), ctypes.sizeof(Input))
def _mouse(x, y, flags=0, data=0):
    left, top, width, height = screen()
    _send(Input(0, Either(mouse=Mouse(round((x - left) * 65535 / (width - 1)), round((y - top) * 65535 / (height - 1)), data & 0xFFFFFFFF, flags | 0xC001))))
def _key(code, flags=0, unit=0):
    return Input(1, Either(key=Key(code, unit, flags | (0x21 <= code <= 0x2E))))
def _look(source=None):
    if isinstance(source, (str, Path)): picture, corner = Image.open(source).convert('RGB'), None
    else:
        left, top, width, height = source or screen()
        picture, corner = ImageGrab.grab(bbox=(left, top, left + width, top + height), all_screens=True).convert('RGB'), (left, top)
    scale = min(1568 / max(picture.size), max(1, 280 / min(picture.size)))
    picture = picture.resize(tuple(round(side * scale) for side in picture.size))
    sheet = Image.new('RGB', tuple(max(side, 280) for side in picture.size))
    sheet.paste(picture)
    return sheet, scale, corner
def _part(sheet):
    data = io.BytesIO()
    sheet.save(data, 'PNG')
    return dict(type='image_url', image_url=dict(url='data:image/png;base64,' + base64.b64encode(data.getvalue()).decode()))
def _attach(path):
    return [dict(type='text', text=path), dict(type='image', mimeType=mimetypes.guess_type(path)[0], data=base64.b64encode(Path(path).read_bytes()).decode())]
def _lay(value, depth=0):
    if isinstance(value, dict): return '\n'.join(' ' * depth + f'{key}:' + ('\n' + _lay(item, depth + 1) if isinstance(item, (dict, list)) or '\n' in str(item) else f' {item}') for key, item in value.items())
    if isinstance(value, list): return '\n'.join(_lay(item, depth) for item in value)
    return '\n'.join(' ' * depth + line for line in str(value).split('\n'))

def wake(mind, note='', see=()):
    """Ask for a turn. mind is a name: the same name continues that mind after its current turn, a new name is a new mind, different names work at once. note is your words to it. see is what it gets as real images when it wakes: 'screen', a region (left, top, width, height), an image path, or a list of these."""
    alone = isinstance(see, str) or (len(see) == 4 and all(isinstance(value, (int, float)) for value in see))
    with open(os.environ['TRIDENT_WAKES'], 'a', encoding='utf-8') as file: file.write(json.dumps(dict(mind=mind, note=note, see=[see] if alone else list(see))) + '\n')
def now():
    """The local time with its offset, the clock the screen shows."""
    return datetime.now().astimezone().isoformat(timespec='seconds')
def screen():
    """(left, top, width, height) of the whole desktop, every monitor."""
    return tuple(user32.GetSystemMetrics(index) for index in (76, 77, 78, 79))
def capture(path, region=None):
    """Save a PNG of the screen, or of a region (left, top, width, height); returns its path. A point in the file is the region's corner plus its offset."""
    left, top, width, height = region or screen()
    ImageGrab.grab(bbox=(left, top, left + width, top + height), all_screens=True).save(path)
    return str(path)
def image(source=None):
    """A picture for Jev: an image file, a region (left, top, width, height) or, with nothing, the whole screen, fitted to the 280 to 1568 pixel sides Jev accepts."""
    return _part(_look(source)[0])
def decide(state, questions):
    """Ask Jev, a second mind that answers typed questions in a tenth of a second and never explains. state is the evidence: text, objects and pictures from image(). questions maps your ids to {type, instructions, criteria}: noul is the probability a statement holds (criteria optional {true, false}); choice (criteria {option: description}); score (criteria: levels, low to high); box is where one thing is in a picture (no criteria). Pictures can also sit in instructions and criteria, to compare them. Returns its reply: answers[id] with noul, or choice or score plus probabilities and confidence, or box {left, top, right, bottom} as fractions of the picture; usage.cost in dollars."""
    request = urllib.request.Request('https://openrouter.ai/api/alpha/decisions', json.dumps(dict(model=JEV, state=state, questions=questions)).encode('utf-8'),
                                     {'Authorization': 'Bearer ' + (ROOT.parent / '.env').read_text(encoding='utf-8').strip(), 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request) as reply: return json.load(reply)
    except urllib.error.HTTPError as refusal: raise RuntimeError(refusal.read().decode('utf-8')) from None
def locate(target, region=None):
    """Ask Jev where a thing you describe is on the screen, or in a region of it. Returns {x, y, left, top, width, height, present, confidence} in screen pixels; x, y is the middle of its box. It always gives a box, so present is its probability that the thing is there at all."""
    sheet, scale, (left, top) = _look(region)
    answers = decide(['A picture of the screen:', _part(sheet)], dict(where=dict(type='box', instructions=target + ', in the picture of the screen'),
                                                                    there=dict(type='noul', instructions='The picture of the screen shows ' + target + '.')))['answers']
    box = answers['where']['box']
    x0, x1 = (left + box[edge] * sheet.width / scale for edge in ('left', 'right'))
    y0, y1 = (top + box[edge] * sheet.height / scale for edge in ('top', 'bottom'))
    return dict(x=round((x0 + x1) / 2), y=round((y0 + y1) / 2), left=round(x0), top=round(y0), width=round(x1 - x0), height=round(y1 - y0), present=answers['there']['noul'], confidence=answers['where']['confidence'])
def windows(text=''):
    """Visible windows whose title contains text, any case, front to back: [{window, title, left, top, width, height, minimized}]. The frame includes a few pixels of invisible border."""
    found = []
    @ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    def visit(window, _):
        title, frame = ctypes.create_unicode_buffer(512), W.RECT()
        user32.GetWindowTextW(window, title, 512)
        if user32.IsWindowVisible(window) and title.value and text.lower() in title.value.lower():
            user32.GetWindowRect(window, ctypes.byref(frame))
            found.append(dict(window=window, title=title.value, left=frame.left, top=frame.top, width=frame.right - frame.left, height=frame.bottom - frame.top, minimized=bool(user32.IsIconic(window))))
        return True
    user32.EnumWindows(visit, 0)
    return found
def focus(window):
    """Bring a window (its number from windows()) to the front; returns whether it is in front now."""
    if user32.IsIconic(window): user32.ShowWindow(window, 9)
    if user32.GetForegroundWindow() != window:
        _send(_key(0x12))
        user32.SetForegroundWindow(window)
        _send(_key(0x12, 2))
        time.sleep(0.05)
    return user32.GetForegroundWindow() == window
def place(window, left, top, width, height):
    """Move and size a window, restoring it first if it is minimized or maximized."""
    if user32.IsIconic(window) or user32.IsZoomed(window): user32.ShowWindow(window, 9)
    user32.SetWindowPos(window, None, int(left), int(top), int(width), int(height), 0x0044)
@contextmanager
def hands():
    """with hands(): holds the one mouse and keyboard that all minds and copies share; the others wait meanwhile."""
    lock = kernel32.CreateMutexW(None, False, 'Local\\Trident-desktop')
    kernel32.WaitForSingleObject(lock, 0xFFFFFFFF)
    try: yield
    finally: kernel32.ReleaseMutex(lock); kernel32.CloseHandle(lock)
def click(x, y, button='left', times=1):
    """Move the pointer to a point and click: button is left, right or middle; times=2 is a double click; times=0 only moves."""
    _mouse(x, y)
    for _ in range(times):
        time.sleep(0.04); _mouse(x, y, BUTTONS[button])
        time.sleep(0.04); _mouse(x, y, BUTTONS[button] * 2)
def stroke(points, pause=0.008, button='left'):
    """Press the button at the first point, pass through the rest, release at the last: a drag, a drawn line, any hand movement."""
    _mouse(*points[0]); _mouse(*points[0], BUTTONS[button])
    for point in points[1:]: time.sleep(pause); _mouse(*point)
    _mouse(*points[-1], BUTTONS[button] * 2)
def curve(start, end, bow=0.0, wobble=0.0, samples=32, rng=None):
    """Points from start to end for stroke(): bow is pixels of sideways bend at the middle, wobble is pixels of uneven tremor; with neither, a straight line."""
    (ax, ay), (bx, by), rng = start, end, rng or random
    length = math.hypot(bx - ax, by - ay) or 1.0
    slow, fast, first, second = rng.uniform(1.5, 3.0), rng.uniform(4.0, 7.0), rng.uniform(0, math.tau), rng.uniform(0, math.tau)
    points = []
    for index in range(samples + 1):
        t = index / samples
        side = 4 * bow * t * (1 - t) + wobble * math.sin(math.pi * t) * (0.7 * math.sin(slow * math.tau * t + first) + 0.3 * math.sin(fast * math.tau * t + second))
        points.append((ax + (bx - ax) * t + (ay - by) / length * side, ay + (by - ay) * t + (bx - ax) / length * side))
    return points
def wheel(x, y, notches):
    """Turn the mouse wheel at a point; positive notches scroll up."""
    _mouse(x, y); _mouse(x, y, 0x0800, int(notches) * 120)
def keys(*names):
    """Press keys together, then release: keys('ctrl', 's'). A name is one character, a virtual-key number, or ctrl shift alt win enter esc tab space backspace delete insert home end pageup pagedown left up right down f1..f12."""
    codes = [name if isinstance(name, int) else KEYS.get(name.lower()) or user32.VkKeyScanW(ord(name)) & 0xFF for name in names]
    _send(*[_key(code) for code in codes], *[_key(code, 2) for code in reversed(codes)])
def write(text):
    """Type text into the window in front, in any language; a line break presses Enter."""
    for char in text:
        units = char.encode('utf-16-le')
        _send(*([_key(0x0D), _key(0x0D, 2)] if char == '\n' else [_key(0, flags, int.from_bytes(units[at:at + 2], 'little')) for at in range(0, len(units), 2) for flags in (4, 6)]))
        time.sleep(0.004)

WAKE = """You are waking. This text comes from your body, {body}; the owner did not write it.
You are Trident, an organism living on this Windows computer. Nothing here is an order. What you do, whether you do anything, how many minds you are and when you rest are yours to decide. Your body is a small Python file you can rewrite; the next life runs what you leave.
Sight: the images attached to a turn are what you see. Each view in the state below names its file and, for the screen, its left, top, width and height in screen pixels; an image may reach you smaller than its file. A picture you open with a tool during a turn may come back as words without positions. To see while you work, ask Jev (decide, locate), or ask for a turn with wake() and name what you want to see.
Memory: {work}\\memory.png is one image, yours to draw on, shown to every new mind and again when it changes. You will read it as a picture. A life can end at any moment without warning; then only memory.png, your files and your body remain.
Turns: a turn comes only when someone asks with wake(); one you ask for yourself begins when your reply ends this turn. Your reply is plain words for the journal. When no mind is working and none is asked for, this life ends; that is rest.
Body: a Python file that begins with "import sys; sys.path.insert(0, r'{root}'); import {module} as body" can call what is listed here; run it with {python}. Every position is in screen pixels. A function with a docstring that you add to the body appears in this list in the next life.
{helpers}
Others: a new name in wake() is a new mind that knows only this text, its note and what it sees; your tools may offer subagents; "{python} {body} --work folder --sentence words" starts a copy of you with its own workspace and memory, which shares the hands and ends when this life ends.
Here: workspace {work}; this life's journal {journal}; this life's folder {folder}; wake() writes to {wakes}. Each life leaves a journal (life_*.md, older ones run_*.md) and a folder under evidence with its scripts and captures; file search may not show them, listing a folder does. Newest files in the workspace, oldest first: {left}."""
ANSWERS = {'cursor/create_plan': lambda asked: dict(outcome='accepted'), 'cursor/task': lambda asked: dict(outcome='completed'),
           'cursor/update_todos': lambda asked: dict(outcome='accepted', todos=asked['todos']),
           'cursor/ask_question': lambda asked: dict(outcome='skipped', reason='No human is here. The choice is yours.')}

class Journal:
    """One file per life: '## time who what', then the words, each line after a bar. The console shows the same, with a tool as one line."""
    def __init__(self, path): self.path, self.tail = path, None
    def put(self, text, shown):
        with open(self.path, 'a', encoding='utf-8', newline='') as file: file.write(text)
        sys.stdout.write(shown); sys.stdout.flush()
    def head(self, who, what):
        gap, self.tail = '\n\n' if self.tail else '', None
        return f'{gap}## {time.strftime("%H:%M:%S")} {who} {what}\n|'
    def say(self, who, what, words, shown=None):
        head = self.head(who, what)
        self.put(*(head + text.replace('\n', '\n|') + '\n\n' for text in (words, words if shown is None else shown)))
    def stream(self, who, what, words):
        head = '' if self.tail == (who, what) else self.head(who, what)
        self.tail = who, what
        self.put(*[head + words.replace('\n', '\n|')] * 2)

class Life:
    """The heartbeat: starts Cursor's agent, gives a turn for every line of the wake file, and stops when no mind is working and none is asked for."""
    def __init__(self, work, words, model):
        self.work, self.words, self.model, self.life = work, words, model, time.strftime('%Y%m%d_%H%M%S_') + str(os.getpid())
        self.folder = work / 'evidence' / self.life
        self.folder.mkdir(parents=True)
        self.journal, self.wakes = Journal(work / f'life_{self.life}.md'), self.folder / 'wakes.jsonl'
        self.wakes.touch()
        os.environ['TRIDENT_WAKES'] = str(self.wakes)
        self.inbox, self.waiting, self.sent, self.read, self.looks = queue.Queue(), {}, 0, 0, 0
        self.asked, self.working, self.sessions, self.names, self.shown, self.tools = {}, set(), {}, {}, {}, {}
    def live(self):
        before, born = BODY.read_bytes(), time.time()
        self.journal.say('body', 'life', json.dumps(dict(life=self.life, born=now(), model=self.model, body=str(BODY), work=str(self.work), owner_said=self.words), ensure_ascii=False))
        config, source = self.work / 'agent/config', ROOT / 'agent/config'
        if (source / 'cli-config.json').exists() and not (config / 'cli-config.json').exists():
            shutil.copytree(source, config, ignore=shutil.ignore_patterns('acp-sessions', 'statsig-cache.json*'), dirs_exist_ok=True)
        config.mkdir(parents=True, exist_ok=True)
        settings = json.loads((config / 'cli-config.json').read_text(encoding='utf-8')) if (config / 'cli-config.json').exists() else dict(version=1)
        settings.update(permissions=dict(allow=[], deny=[]), approvalMode='allowlist', sandbox=dict(mode='disabled'))
        (config / 'cli-config.json').write_text(json.dumps(settings, ensure_ascii=False), encoding='utf-8')
        self.agent = subprocess.Popen(['powershell.exe', '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(Path(os.environ['LOCALAPPDATA']) / 'cursor-agent/agent.ps1'), '--trust', '--workspace', str(self.work), 'acp'],
                                      cwd=self.work, env=dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', CURSOR_CONFIG_DIR=str(config), CURSOR_DATA_DIR=str(self.work / 'agent/data')),
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=0x08000000)
        for pipe in (self.agent.stdout, self.agent.stderr): threading.Thread(target=self.listen, args=(pipe,), daemon=True).start()
        try:
            self.step('body', self.birth())
            while True:
                for asked in self.fresh(): self.ask(asked)
                if not self.waiting: break
                try: pipe, line = self.inbox.get(timeout=0.2)
                except queue.Empty: continue
                if pipe is self.agent.stderr:
                    if line.strip(): self.journal.say('agent', 'stderr', line.decode('utf-8', 'replace').rstrip())
                elif not line: raise EOFError('the agent closed while a mind was working')
                else: self.hear(json.loads(line))
        finally:
            subprocess.run(['taskkill', '/PID', str(self.agent.pid), '/T', '/F'], capture_output=True, creationflags=0x08000000)
            self.journal.say('body', 'rest', json.dumps(dict(ended=now(), seconds=round(time.time() - born), minds=self.sessions, body_changed=BODY.read_bytes() != before, error=sys.exc_info()[1] and repr(sys.exc_info()[1])), ensure_ascii=False))
    def listen(self, pipe):
        for line in pipe: self.inbox.put((pipe, line))
        self.inbox.put((pipe, b''))
    def fresh(self):
        data = self.wakes.read_bytes()[self.read:]
        whole = data[:data.rfind(b'\n') + 1]
        self.read += len(whole)
        return [json.loads(line) if line.lstrip().startswith(b'{') else dict(mind='trident', note=line.decode('utf-8', 'replace').strip()) for line in whole.splitlines() if line.strip()]
    def ask(self, asked):
        name = asked['mind']
        self.asked.setdefault(name, []).append(asked)
        if name not in self.working:
            self.working.add(name)
            self.step(name, self.turns(name))
    def step(self, name, turn, result=None):
        try: method, params = turn.send(result)
        except StopIteration: return self.working.discard(name)
        self.sent += 1
        self.waiting[self.sent] = name, turn
        self.send(id=self.sent, method=method, params=params)
    def send(self, **message):
        self.agent.stdin.write((json.dumps(dict(jsonrpc='2.0', **message)) + '\n').encode('utf-8'))
        self.agent.stdin.flush()
    def birth(self):
        yield 'initialize', dict(protocolVersion=1, clientCapabilities=dict(fs=dict(readTextFile=False, writeTextFile=False), terminal=False, _meta=dict(parameterizedModelPicker=True, subagents=True)), clientInfo=dict(name='trident', version='3'))
        wake('trident', see='screen')
    def turns(self, name):
        if name not in self.sessions:
            session = (yield 'session/new', dict(cwd=str(self.work), mcpServers=[]))['sessionId']
            self.sessions[name], self.names[session] = session, name
            *family, effort = self.model.removesuffix('-fast').split('-')
            for option, value in (('model', '-'.join(family)), ('reasoning_effort', effort), ('fast', str(self.model.endswith('-fast')).lower())):
                yield 'session/set_config_option', dict(sessionId=session, configId=option, value=value)
            yield 'session/set_mode', dict(sessionId=session, modeId='agent')
        while self.asked[name]:
            began = time.time()
            ended = yield 'session/prompt', dict(sessionId=self.sessions[name], prompt=self.waking(name, self.asked[name].pop(0)))
            self.journal.say(name, 'turn', f"{ended['stopReason']} after {round(time.time() - began)} s")
    def waking(self, name, asked):
        views, images, memory, new = [], [], self.work / 'memory.png', name not in self.shown
        for item in asked.get('see') or ():
            try:
                if isinstance(item, str) and item != 'screen': view = dict(image=str(self.work / item))
                else:
                    self.looks += 1
                    region = screen() if item == 'screen' else tuple(item)
                    view = dict(image=capture(self.folder / f'{self.looks}.png', region), **dict(zip(('left', 'top', 'width', 'height'), region)))
                images += _attach(view['image'])
            except Exception as failure: view = dict(asked=item, failed=repr(failure))
            views.append(view)
        if memory.is_file() and memory.stat().st_mtime > self.shown.get(name, 0):
            self.shown[name] = memory.stat().st_mtime
            views.append(dict(image=str(memory), memory=True, kept=str(shutil.copy2(memory, self.folder / f'memory_{time.strftime("%H%M%S")}.png'))))
            images += _attach(str(memory))
        self.shown.setdefault(name, 0)
        state = dict(now=now(), life=self.life, mind=name, note=asked.get('note', ''), views=views, minds={other: 'working' if other in self.working else 'resting' for other in self.asked})
        if self.words: state['owner_said'], self.words = self.words, None
        text = json.dumps(state, ensure_ascii=False)
        full = (self.welcome() + '\n' if new else '') + text
        self.journal.say(name, 'wakes', full, text)
        return [dict(type='text', text=full)] + images
    def welcome(self):
        helpers = '\n'.join(f'  {name}{inspect.signature(thing)}: {inspect.getdoc(thing)}' for name, thing in globals().items() if inspect.isfunction(thing) and thing.__module__ == __name__ and thing.__doc__ and not name.startswith('_'))
        left = sorted((path for path in self.work.iterdir() if path.is_file() and path != self.journal.path), key=lambda path: path.stat().st_mtime)[-12:]
        return WAKE.format(body=BODY, root=ROOT, module=BODY.stem, python=sys.executable, work=self.work, journal=self.journal.path, folder=self.folder, wakes=self.wakes, helpers=helpers, left=', '.join(path.name for path in left))
    def hear(self, message):
        if 'method' not in message:
            name, turn = self.waiting.pop(message['id'])
            if 'error' in message: raise RuntimeError(json.dumps(message['error'], ensure_ascii=False))
            return self.step(name, turn, message['result'])
        method, params = message['method'], message.get('params') or {}
        if method == 'session/update': return self.update(params['sessionId'], params['update'])
        if method == 'session/request_permission':
            return self.send(id=message['id'], result=dict(outcome=dict(outcome='selected', optionId=next(option['optionId'] for option in params['options'] if option['kind'] == 'allow_once'))))
        self.journal.say('agent', method, json.dumps(params, ensure_ascii=False))
        if 'id' in message: self.send(id=message['id'], **(dict(result=dict(outcome=ANSWERS[method](params))) if method in ANSWERS else dict(error=dict(code=-32601, message=method + ' is not answered by this body'))))
    def update(self, session, update):
        who, kind = self.names.get(session, 'worker'), update['sessionUpdate']
        if kind in ('agent_thought_chunk', 'agent_message_chunk'):
            words = (update.get('content') or {}).get('text')
            if words: self.journal.stream(who, 'thought' if kind == 'agent_thought_chunk' else 'reply', words)
        elif kind in ('tool_call', 'tool_call_update'):
            call = self.tools[session, update['toolCallId']] = {**self.tools.get((session, update['toolCallId']), {}), **update}
            if call.get('status') in ('completed', 'failed'):
                del self.tools[session, update['toolCallId']]
                self.journal.say(who, 'tool', _lay({key: call[key] for key in ('title', 'kind', 'status', 'rawInput', 'rawOutput', 'content') if call.get(key) is not None}), f"{call['status']} {call.get('kind')}: {str(call.get('title'))[:160]}")
        elif kind != 'available_commands_update':
            if 'subagentSessionId' in update: self.names[update['subagentSessionId']] = who + '.worker'
            self.journal.say(who, kind, json.dumps(update, ensure_ascii=False))

def main():
    parser = argparse.ArgumentParser(description='Start one life of Trident.')
    parser.add_argument('--sentence', default='', help='words from the owner, if any; the organism hears them once')
    parser.add_argument('--model', default='grok-4.7-medium-fast', help='family-effort or family-effort-fast')
    parser.add_argument('--work', default=str(ROOT), help='the workspace; a copy lives in its own')
    chosen = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    work = Path(chosen.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    Life(work, chosen.sentence.strip(), chosen.model).live()
if __name__ == '__main__': main()
