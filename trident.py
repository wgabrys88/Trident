# trident.py — one body, Windows 11, Python 3.11.
# The sentence is the only assignment. This file does not contain a task.
import argparse
import ctypes
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BRAIN = 'grok-4.7-medium-fast'
BRAINS = (
    'grok-4.7-low', 'grok-4.7-low-fast', 'grok-4.7-medium', BRAIN,
    'grok-4.7-high', 'grok-4.7-high-fast', 'grok-4.7-xhigh', 'grok-4.7-xhigh-fast',
)
ROTATE_AT = 42
STOP_GRACE = 60
HEAD = re.compile(r'(?m)^## (\d\d:\d\d:\d\d) (\S+)')
WAKE = re.compile(r'(?m)^## wake ')
CREATE_NO_WINDOW = 0x08000000
CREATE_SUSPENDED = 0x00000004

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
ntdll = ctypes.WinDLL('ntdll', use_last_error=True)
kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
kernel32.CreateJobObjectW.restype = wintypes.HANDLE
kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
kernel32.SetInformationJobObject.restype = wintypes.BOOL
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
ntdll.NtResumeProcess.restype = ctypes.c_long


class JobBasicLimits(ctypes.Structure):
    _fields_ = [
        ('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
        ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
        ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD),
    ]


class JobLimits(ctypes.Structure):
    _fields_ = [
        ('BasicLimitInformation', JobBasicLimits), ('IoInfo', ctypes.c_uint64 * 6),
        ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
        ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t),
    ]


def wincheck(ok):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


def read(path):
    path = Path(path)
    if not path.is_file():
        return ''
    return path.read_text(encoding='utf-8-sig')


def write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_bytes(text.encode('utf-8') if isinstance(text, str) else text)
    os.replace(temp, path)


def heading(run, kind, text=''):
    body = '' if text is None else str(text)
    block = f"## {time.strftime('%H:%M:%S')} {kind}\n\n{body}\n\n" if body else f"## {time.strftime('%H:%M:%S')} {kind}\n\n"
    with open(run, 'a', encoding='utf-8') as handle:
        handle.write(block)
    sys.stdout.write(block)
    sys.stdout.flush()


def measured(path):
    text = read(path)
    heads = list(HEAD.finditer(text))
    counts = {}
    for head in heads:
        counts[head.group(2)] = counts.get(head.group(2), 0) + 1
    return {
        'run': Path(path).stem.removeprefix('run_'),
        'source': str(path),
        'first_event': heads[0].group(1) if heads else None,
        'last_event': heads[-1].group(1) if heads else None,
        'counts': counts,
        'recorded_errors': counts.get('err', 0),
    }


def earlier_runs(work, run_id):
    rows = []
    for path in sorted(Path(work).glob('run_*.md')):
        row = measured(path)
        if row['run'] != run_id:
            rows.append(row)
    return rows


def learned_line(rows, ledger):
    errors = sum(row['recorded_errors'] for row in rows)
    calls = sum(row['counts'].get('tool', 0) for row in rows)
    names = ', '.join(row['run'] for row in rows)
    advice = 'Compare the newest ledger line with the run markdown.' if ledger.strip() else 'There is no ledger lesson yet.'
    return f'{len(rows)} earlier wakes ({names}), {errors} recorded errors, {calls} tool calls. {advice}'


def newest(ledger):
    parts = WAKE.split(ledger or '')
    if len(parts) < 2:
        return 'There is no ledger lesson yet.'
    return '## wake ' + parts[-1].strip()


def choose_brain(sentence, model):
    if model:
        if model not in BRAINS:
            raise SystemExit('unknown mind model: ' + model)
        return model
    low = sentence.lower()
    found = [slug for slug in sorted(BRAINS, key=len, reverse=True) if slug in low]
    return found[0] if found else BRAIN


def closing_clock(minutes, end):
    window = max(0.05 * float(minutes), 2) * 60
    begin = end - window
    return (
        f'The wake has {float(minutes):g} minutes and ends at {time.strftime("%H:%M:%S", time.localtime(end))}. '
        f'From {time.strftime("%H:%M:%S", time.localtime(begin))} on, stop new work, write story.txt and lesson.txt, and write the stop file.'
    )


def render(work, run, run_id, sentence, minutes, end, model):
    story = read(work / 'story.txt')
    campaign = read(work / 'campaign.txt')
    ledger = read(work / 'ledger.md')
    current = measured(run)
    prior = earlier_runs(work, run_id)
    packed = {'current': current, 'historical': prior}
    budget = closing_clock(minutes, end)
    parts = [
        'You are the Trident organism. trident.py is your body. story.txt is the plan: a short ordered list of subtasks. campaign.txt is the task list. ledger.md is memory. The run markdown is the record. You act with Cursor\'s own tools. The sentence at the bottom is the only assignment.',
        budget,
        'Write story.txt, the whole plan, whenever it changes. Write lesson.txt as this wake\'s one lesson, 1-7200 characters. Write an empty file named stop when the assignment is finished. In the closing window, write those three and do not start new work. Your last message is not completion. The stop file is.',
        'Programs you start from this chat die when the chat ends. Put anything the next chat must reopen into story.txt. There is no vision server and there are no Trident tool flags.',
        'STORY',
        story or '(empty)',
        'CAMPAIGN',
        campaign or '(empty)',
        'LEDGER',
        newest(ledger),
        'MEASURED',
        json.dumps(packed, ensure_ascii=False),
        'LEARNED',
        learned_line(prior, ledger),
        'BRAIN',
        model,
        'RUN',
        str(run),
        'SENTENCE',
        sentence.strip(),
    ]
    return '\n\n'.join(parts)


def runtime():
    versions = Path(os.environ['LOCALAPPDATA']) / 'cursor-agent' / 'versions'
    folders = [path for path in versions.iterdir() if path.name[:1].isdigit() and (path / 'index.js').exists()]
    if not folders:
        raise SystemExit('the agent runtime is not installed')
    return max(folders)


def open_job():
    job = kernel32.CreateJobObjectW(None, None)
    wincheck(job)
    limits = JobLimits()
    limits.BasicLimitInformation.LimitFlags = 0x2000
    wincheck(kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
    return job


def close_job(job):
    if job:
        wincheck(kernel32.CloseHandle(job))


def launch(work, model, prompt_path, job):
    folder = runtime()
    vendor = work / 'agent'
    profile = vendor / 'config'
    profile.mkdir(parents=True, exist_ok=True)
    (vendor / 'data').mkdir(parents=True, exist_ok=True)
    (profile / 'cli-config.json').write_text(json.dumps({
        'version': 1,
        'permissions': {'allow': ['Read(**)', 'Write(**)', 'Shell(*)', 'WebFetch(*)', 'Mcp(*:*)'], 'deny': []},
        'sandbox': {'mode': 'disabled'},
        'approvalMode': 'unrestricted',
        'autoAcceptWebSearch': True,
    }), encoding='utf-8')
    env = os.environ.copy()
    env['CURSOR_CONFIG_DIR'] = str(profile)
    env['CURSOR_DATA_DIR'] = str(vendor / 'data')
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONUTF8'] = '1'
    env.pop('OPENROUTER_API_KEY', None)
    argv = [
        str(folder / 'node.exe'), '-e',
        "process.argv.push(require('node:fs').readFileSync(process.argv.pop(),'utf8'));require(process.argv[1]);",
        str(folder / 'index.js'), '--trust', '--force', '-p', '--model', model,
        '--workspace', str(work), '--output-format', 'stream-json', str(prompt_path),
    ]
    proc = subprocess.Popen(
        argv, cwd=str(work), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW | CREATE_SUSPENDED,
    )
    if not kernel32.AssignProcessToJobObject(job, proc._handle):
        proc.kill()
        raise ctypes.WinError(ctypes.get_last_error())
    status = ntdll.NtResumeProcess(proc._handle)
    if status != 0:
        proc.kill()
        raise OSError(f'NtResumeProcess {status:#x}')
    return proc


def text_of(message):
    content = message.get('content') if isinstance(message, dict) else message
    if isinstance(content, list):
        return ''.join(part.get('text', '') if isinstance(part, dict) else str(part) for part in content)
    if content is None:
        return ''
    return str(content)


def consume(proc, work, run, end):
    calls = running = 0
    thought = ''
    reason = None
    stop_at = None
    inbox = queue.Queue()

    def pump():
        for raw in proc.stdout:
            inbox.put(raw)
        inbox.put(None)

    threading.Thread(target=pump, daemon=True).start()

    def finish(why):
        nonlocal thought
        if thought:
            heading(run, 'think', thought)
            thought = ''
        return why

    while True:
        now = time.time()
        if now >= end:
            return finish('deadline')
        if (work / 'stop').exists():
            stop_at = stop_at or now + STOP_GRACE
            if now >= stop_at:
                return finish('stopped')
        try:
            raw = inbox.get(timeout=0.2)
        except queue.Empty:
            continue
        if raw is None:
            code = proc.wait(timeout=10)
            if (work / 'stop').exists():
                return finish('stopped')
            if code != 0:
                return finish('failed_agent')
            return finish(reason or 'completed')
        line = raw.decode('utf-8', 'replace').rstrip('\r\n')
        if not line.startswith('{'):
            if line:
                heading(run, 'err', line)
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            continue
        kind, sub = item.get('type'), item.get('subtype')
        if kind == 'thinking' and sub == 'delta':
            thought += item.get('text') or ''
            continue
        if thought:
            heading(run, 'think', thought)
            thought = ''
        if kind == 'assistant' and text_of(item.get('message')):
            heading(run, 'say', text_of(item.get('message')))
        elif kind == 'tool_call' and sub == 'started':
            running += 1
        elif kind == 'tool_call' and sub == 'completed':
            running = max(0, running - 1)
            calls += 1
            heading(run, 'tool', json.dumps(item.get('tool_call') or {}, ensure_ascii=False))
            if calls >= ROTATE_AT and running == 0 and not (work / 'stop').exists() and time.time() < end:
                return finish('rotate')
        elif kind == 'system' and sub == 'init':
            heading(run, 'start', str(item.get('model') or ''))
        elif kind == 'result':
            failed = item.get('is_error') or item.get('error') or str(sub or '').startswith('error')
            reason = 'failed_agent' if failed else 'completed'
            if failed:
                return finish(reason)


def fold_lesson(work, run_id, state, start, minutes):
    lesson = WAKE.sub('', read(work / 'lesson.txt')).strip() or 'No lesson was written.'
    if len(lesson) > 7200:
        raise SystemExit('a lesson has 1-7200 characters')
    parts = WAKE.split(read(work / 'ledger.md'))[1:]
    kept = [part for part in parts if part.split(None, 1)[0] != run_id]
    stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start))
    kept.append(f'{run_id} {stamp} used {(time.time() - start) / 60:.1f} of {float(minutes):g} min, {state}\n{lesson}\n')
    write(work / 'ledger.md', ''.join('## wake ' + part for part in kept))


def drive(work, sentence, minutes, model, run_id):
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    for name in ('stop', 'lesson.txt'):
        path = work / name
        if path.exists():
            path.unlink()
    start = time.time()
    end = start + float(minutes) * 60
    run_id = run_id or time.strftime('%Y%m%d_%H%M%S')
    run = work / f'run_{run_id}.md'
    write(work / 'goal.txt', sentence.strip() + '\n')
    model = choose_brain(sentence, model)
    outcome = 'failed'
    heading(run, 'wake', f'minutes={float(minutes):g}')
    chat = 0
    while time.time() < end and not (work / 'stop').exists():
        chat += 1
        write(work / 'prompt.txt', render(work, run, run_id, sentence, minutes, end, model))
        heading(run, 'chat', f'{model} {chat}')
        job = open_job()
        proc = None
        try:
            proc = launch(work, model, work / 'prompt.txt', job)
            outcome = consume(proc, work, run, end)
        finally:
            close_job(job)
            if proc is not None and proc.poll() is None:
                proc.wait(timeout=15)
        heading(run, 'agent-exit', outcome)
        if outcome != 'rotate':
            break
    else:
        outcome = 'deadline' if time.time() >= end else 'stopped'
    fold_lesson(work, run_id, outcome, start, minutes)
    heading(run, 'mind', json.dumps({'state': outcome, 'minutes': round((time.time() - start) / 60, 1)}))
    return outcome


def main(argv=None):
    parser = argparse.ArgumentParser(prog='trident', add_help=False)
    parser.add_argument('--work', default='')
    parser.add_argument('--minutes', required=True)
    parser.add_argument('--sentence', default='')
    parser.add_argument('--mind-file', default='')
    parser.add_argument('--model', default='')
    parser.add_argument('--run', default='')
    args = parser.parse_args(argv)
    minutes = float(args.minutes)
    if minutes <= 0:
        raise SystemExit('minutes must be positive')
    if bool(args.sentence.strip()) == bool(args.mind_file):
        raise SystemExit('pass exactly one of --sentence or --mind-file')
    sentence = args.sentence.strip() if args.sentence.strip() else read(args.mind_file).strip()
    if not sentence:
        raise SystemExit('the sentence is empty')
    work = Path(args.work).resolve() if args.work else ROOT
    outcome = drive(work, sentence, minutes, args.model, args.run)
    if outcome not in ('completed', 'stopped', 'deadline'):
        raise SystemExit(outcome)


if __name__ == '__main__':
    main()
