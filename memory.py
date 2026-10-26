import re, time
from pathlib import Path

from base import SET, Fail, read_file

COUNTED = {'eye', 'decide', 'hands', 'capture', 'edit', 'read', 'windows', 'region', 'place', 'story', 'ledger', 'stop'}
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
    return [path for path in sorted(Path(work).glob('run_*.md')) if path.name != current]


def digest(path):
    text = path.read_text(encoding='utf-8', errors='replace')
    heads = HEAD.findall(text)
    calls = {}
    for _stamp, kind, name in heads:
        flag = name if kind == 'request' and name in COUNTED else (kind if kind in COUNTED else '')
        if flag:
            calls[flag] = calls.get(flag, 0) + 1
    reports = [body.strip() for body in REPORT.findall(text)]
    final = reports[-1][:800] if reports else ''
    return dict(run=path.stem, span=[heads[0][0], heads[-1][0]] if heads else [], calls=calls, errors=sum(kind == 'err' for _stamp, kind, _name in heads), final=final)


def recall(work):
    return dict(wakes=[digest(path) for path in journals(work)][-5:])


def learn(work):
    rows = [digest(path) for path in journals(work)][-5:]
    calls, errors = {}, 0
    for row in rows:
        errors += row['errors']
        for key, value in row['calls'].items():
            calls[key] = calls.get(key, 0) + value
    ledger = Path(work) / 'ledger.md'
    advice = 'Start from the newest ledger line.' if ledger.exists() and ledger.read_text(encoding='utf-8').strip() else 'There is no ledger lesson yet.'
    return dict(wakes=len(rows), errors=errors, calls=calls, text=f'{len(rows)} earlier wakes, {errors} errors, {sum(calls.values())} tool calls. {advice}')


def _parts(work):
    path = Path(work) / 'ledger.md'
    text = path.read_text(encoding='utf-8') if path.exists() else ''
    return path, WAKE.split(text)[1:]


def current_lesson(work):
    _path, parts = _parts(work)
    for part in parts:
        if part.split(None, 1)[0] == SET['run']:
            return part.split('\n', 1)[1].strip()
    return ''


def enter(work, lesson, state):
    lesson = WAKE.sub('', str(lesson)).strip()
    if not lesson:
        lesson = 'No lesson was written.'
    if len(lesson) > 1200:
        raise Fail('a lesson has 1-1200 characters')
    path, parts = _parts(work)
    kept = [part for part in parts if part.split(None, 1)[0] != SET['run']]
    start = float(SET['wake_start']) if SET.get('wake_start') else time.time()
    minutes = SET.get('minutes') or ''
    budget = f' of {float(minutes):g}' if minutes else ''
    kept.append(f"{SET['run']} {time.strftime('%Y-%m-%d %H:%M', time.localtime(start))} used {(time.time() - start) / 60:.0f}{budget} min, {state}\n{lesson}\n")
    while len(kept) > 12 or sum(map(len, kept)) > 8000:
        del kept[0]
    path.write_text(''.join('## wake ' + part for part in kept), encoding='utf-8')
    return dict(wakes=len(kept), lesson_len=len(lesson))


class Story:
    name, flag = 'story', 'story_file'
    doc = '''$CALL --story-file PATH
Reads the whole new story.txt from the file: the checklist and the evidence. The next prompt includes that file.'''

    def run(self, app, path, dry=False):
        text = read_file(path)
        if dry:
            return dict(dry=True, story_len=len(text))
        (app.work / 'story.txt').write_text(text, encoding='utf-8')
        return dict(story_len=len(text))


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
Counts eye, decide, hands, capture, edit, and read in up to five earlier journals. It does not count this wake. The text says to start from the newest ledger line when one exists.'''

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
Writes the stop file. The process ends the wake after this call. The next message is the final report.'''

    def run(self, app, _arg, dry=False):
        if dry:
            return dict(dry=True)
        (app.work / 'stop').write_bytes(b'')
        return dict(stopped=True)


story, ledger, learn_tool, recall_tool, stop = Story(), Ledger(), Learn(), Recall(), Stop()
