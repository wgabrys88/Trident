import hashlib, json, os, re, time
from pathlib import Path

from base import SET, Fail, read_file
from state import atomic, json_file, locked

FRESH = ('story.txt', 'goal.txt', 'stop', 'look.json', 'region.txt')
HEAD = re.compile(r'(?m)^## (\d\d:\d\d:\d\d) (\S+)(?: (\S+))?$')
REPORT = re.compile(r'(?ms)^## \S+ report\n\n(.*?)(?=^## |\Z)')
WAKE = re.compile(r'(?m)^## wake ')


def wipe(work):
    work = Path(work)
    for name in FRESH:
        (work / name).unlink(missing_ok=True)
    return dict(fresh=True, ledger=(work / 'ledger.md').exists())


def journals(work):
    current = f"run_{SET['run']}.md"
    work = Path(work)
    paths = set(work.glob('run_*.md')) | {path.with_suffix('.md') for path in work.glob('run_*.jsonl')}
    return [path for path in sorted(paths) if path.name != current]


def digest(path):
    text = path.read_text(encoding='utf-8', errors='replace') if path.exists() else ''
    heads = HEAD.findall(text)
    reports = [body.strip() for body in REPORT.findall(text)]
    final = reports[-1][:800] if reports else ''
    from evidence import error_count, measured
    counts = measured(path)
    return dict(run=path.stem, span=[heads[0][0], heads[-1][0]] if heads else [], calls=counts['component_actions'],
                harness_calls=counts['harness_calls'], harness_count_basis=counts['harness_count_basis'],
                capture_ids=counts['capture_ids'], errors=error_count(counts), final=final)


def recall(work):
    return dict(wakes=[digest(path) for path in journals(work)][-5:])


def learn(work):
    """Counts from the measured cache. It does not reread journal prose or copy capture ids."""
    from evidence import measured, tally
    rows = [measured(path) for path in journals(work)][-5:]
    ledger = Path(work) / 'ledger.md'
    with locked(Path(work) / 'memory.lock'):
        text = ledger.read_text(encoding='utf-8') if ledger.exists() else ''
    return tally(rows, text)


def _parts(work):
    path = Path(work) / 'ledger.md'
    text = path.read_text(encoding='utf-8') if path.exists() else ''
    return path, WAKE.split(text)[1:]


def snapshot(work):
    work = Path(work)
    with locked(work / 'memory.lock'):
        def read(name):
            path = work / name
            return path.read_text(encoding='utf-8') if path.exists() else ''
        story, ledger = read('story.txt'), read('ledger.md')
        try:
            meta = json.loads(read('story.meta.json'))
        except (ValueError, TypeError):
            meta = {}
        if meta.get('sha256') != hashlib.sha256(story.encode()).hexdigest():
            meta = dict(run=None, status='unverified story provenance; missing or changed outside story tool')
        return story, ledger, meta


def current_lesson(work):
    with locked(Path(work) / 'memory.lock'):
        _path, parts = _parts(work)
    for part in parts:
        if part.split(None, 1)[0] == SET['run']:
            return part.split('\n', 1)[1].strip()
    return ''


def enter(work, lesson, state):
    with locked(Path(work) / 'memory.lock'):
        return _enter(work, lesson, state)


def _enter(work, lesson, state):
    lesson = WAKE.sub('', str(lesson)).strip()
    if not lesson:
        lesson = 'No lesson was written.'
    if len(lesson) > 1200:
        raise Fail('a lesson has 1-1200 characters')
    path, parts = _parts(work)
    if path.exists():
        # Retain older/replaced claims even when the prompt ledger is bounded.
        with open(Path(work) / 'ledger.history.jsonl', 'a', encoding='utf-8') as archive:
            archive.write(json.dumps(dict(at=time.time(), run=SET['run'], previous=path.read_text(encoding='utf-8'))) + '\n')
    kept = [part for part in parts if part.split(None, 1)[0] != SET['run']]
    start = float(SET['wake_start']) if SET.get('wake_start') else None
    minutes = SET.get('minutes') or ''
    budget = f' of {float(minutes):g}' if minutes else ''
    stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start)) if start is not None else 'start unknown'
    used = f'{(time.time() - start) / 60:.1f}' if start is not None else 'unknown'
    kept.append(f"{SET['run']} {stamp} used {used}{budget} min, {state}\n{lesson}\n")
    while len(kept) > 12 or sum(map(len, kept)) > 8000:
        del kept[0]
    atomic(path, ''.join('## wake ' + part for part in kept))
    return dict(wakes=len(kept), lesson_len=len(lesson))


class Story:
    name, flag = 'story', 'story_file'
    doc = '''$CALL --story-file PATH
Reads the whole new story.txt from the file: the checklist and the evidence. The next prompt includes that file.'''

    def run(self, app, path, dry=False):
        text = read_file(path)
        if dry:
            return dict(dry=True, story_len=len(text))
        meta = dict(run=SET['run'], chat=os.environ.get('TRIDENT_CHAT_ID'), at=time.time(), sha256=hashlib.sha256(text.encode()).hexdigest(), checkpoint_provenance='story component write; nine-call compliance unverified')
        with locked(app.work / 'memory.lock'):
            atomic(app.work / 'story.txt', text)
            json_file(app.work / 'story.meta.json', meta)
        return dict(story_len=len(text), provenance=meta)


class Ledger:
    name, flag = 'ledger', 'ledger_file'
    doc = '''$CALL --ledger-file PATH
Reads this wake's lesson from the file, 1-1200 characters. A later call replaces it. The wake records the minutes and how it ended. The next prompt shows the last lessons.'''

    def run(self, app, path, dry=False):
        text = read_file(path).strip()
        if not text:
            raise Fail('a lesson has 1-1200 characters')
        if dry:
            return dict(dry=True, lesson_len=len(text))
        return enter(app.work, text, 'open')


class Learn:
    name, flag = 'learn', 'learn'
    doc = '''$CALL --learn
Counts component actions in up to five earlier journals, separately from harness requests/completions. It does not count this wake. Ledger prose remains narrative claims, to compare with measured whole-wake evidence.'''

    def run(self, app, _arg, dry=False):
        return learn(app.work)


class Recall:
    name, flag = 'recall', 'recall'
    doc = '''$CALL --recall
Reads up to five earlier journals, never this one. final is the last report, the section marked report, not an earlier say.'''

    def run(self, app, _arg, dry=False):
        return recall(app.work)


class Stop:
    name, flag = 'stop', 'stop'
    doc = '''$CALL --stop
Writes the stop file. The mind has up to 60 seconds to finish its report before the process ends; an earlier wake deadline takes precedence. The body seals the ledger and preserves the termination reason.'''

    def run(self, app, _arg, dry=False):
        if dry:
            return dict(dry=True)
        (app.work / 'stop').write_bytes(b'')
        return dict(stopped=True)


story, ledger, learn_tool, recall_tool, stop = Story(), Ledger(), Learn(), Recall(), Stop()
