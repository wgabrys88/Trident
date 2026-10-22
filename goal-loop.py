#!/usr/bin/env python3
"""Desktop loop. One wake is the goal sentence.

Fara is the eye and the hand's author. The first look is the goal.
Every later look is sent with no extra text, so main.py resends each
saved assistant reply: the thought, then the tool call.
"""
import json, os, re, shutil, subprocess, sys, time
from pathlib import Path

import main

ROOT = Path(__file__).resolve().parent
CREATE_NO_WINDOW = 0x08000000
STOP_ACTIONS = frozenset(('terminate', 'ask_user_question'))
STEPS, MINUTES = 60, 20
FARA_FAILURES = 2  # stop after this many consecutive invalid Fara replies
REPEAT_LIMIT = int(os.environ.get('TRIDENT_REPEAT_LIMIT', '2'))  # identical actions allowed in a row; the next identical one is refused (0 = off)
TYPE_ALLOW = re.compile(os.environ.get('TRIDENT_TYPE_ALLOW', r'^\*?(Untitled|Calculator)\b'))  # window titles that may receive keystrokes
KEYBOARD = frozenset(('type', 'key', 'history_back'))
NAV_KEYS = frozenset(('tab', 'shift', 'up', 'down', 'left', 'right', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright', 'pageup', 'pagedown'))  # each press moves focus or scrolls: exempt from REPEAT_LIMIT

def plain(text):
    text = str(text).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in '"\'': return text[1:-1].strip()
    return text

def child(work, *args):
    return subprocess.run(
        [sys.executable, '-u', '-B', '-X', 'utf8', str(ROOT / 'main.py'), '--work', str(work), '--chain', *map(str, args)],
        cwd=str(ROOT), capture_output=True, creationflags=CREATE_NO_WINDOW)

def load(path, fallback):
    try: return json.loads(path.read_text('utf-8'))
    except (OSError, json.JSONDecodeError): return fallback

def fara(work):
    state = load(work / 'fara.json', dict(task='', awaiting_user=False, turns=[]))
    state.setdefault('turns', [])
    state.setdefault('awaiting_user', False)
    return state

def retina_arg(turns, awaiting, goal, answer):
    if not turns: return (goal,)
    if awaiting: return (answer,) if answer else None
    return ()

def shot(work, dest):
    screen = load(work / 'screen.json', {})
    src = Path(screen.get('image') or '')
    if src.is_file(): shutil.copyfile(src, dest)

def same(look):
    arguments = look.get('arguments') or {}
    return [look.get('action'), look.get('coordinate'), arguments.get('text'), arguments.get('keys')]

def refuse(look, before, recent):
    keys = (look.get('arguments') or {}).get('keys') or []
    nav = look.get('action') == 'key' and keys and all(str(k).strip().lower() in NAV_KEYS for k in keys)
    if REPEAT_LIMIT and not nav and recent[-REPEAT_LIMIT:] == [same(look)] * REPEAT_LIMIT:
        return f"Your last action was not executed: the same {look.get('action')} was already done {REPEAT_LIMIT} times in a row and did not change the screen. Try something else."
    if look.get('action') in KEYBOARD:
        hwnd, title = main.HAND()._do_foreground()
        if not TYPE_ALLOW.search(title) and (hwnd in before or title.endswith(' - Notepad')):  # Notepad reopens old tabs in a new window
            return f'Your last action was not executed: the foreground window "{title}" holds the user\'s existing work and must not receive keystrokes. Open a new window for the task and use that.'
    return ''

def record(journal, row):
    main.log('step', {k: v for k, v in row.items() if k != 'stderr'})
    with journal.open('a', encoding='utf-8') as out: out.write(json.dumps(row, ensure_ascii=False) + '\n')

def prepare(work, goal):
    goal = plain(goal)
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    (work / 'stop').unlink(missing_ok=True)
    region = work / 'region.txt'
    if not region.exists() or not region.read_text('utf-8').strip():
        if child(work, '--region').returncode: return None
    state = fara(work)
    if state.get('awaiting_user'): return goal
    (work / 'goal.txt').write_text(goal, encoding='utf-8')
    (work / 'fara.json').write_text(json.dumps(dict(task=goal, awaiting_user=False, turns=[]), ensure_ascii=False), encoding='utf-8')
    return ''

def loop(goal):
    work = ROOT / 'task'
    answer = prepare(work, goal)
    if answer is None: return
    goal = plain(goal)
    run = work / 'runs' / time.strftime('goal_%Y%m%d_%H%M%S')
    run.mkdir(parents=True)
    journal = run / 'steps.jsonl'
    deadline = time.monotonic() + MINUTES * 60
    before, recent, note, refused = set(main.HAND()._do_windows()), [], '', 0
    for n in range(1, STEPS + 1):
        if (work / 'stop').exists() or time.monotonic() >= deadline: break
        state = fara(work)
        argument = retina_arg(state['turns'], state['awaiting_user'], goal, answer)
        if argument is None: break
        if note and argument == (): argument, note = (note,), ''
        cap = child(work, '--capture')
        if cap.returncode:
            record(journal, dict(step=n, returncode=cap.returncode, saved=False, reply='', stderr=cap.stderr.decode('utf-8', 'replace')[-2000:]))
            continue
        seen = len(fara(work)['turns'])
        proc = child(work, '--retina', *argument)
        state = fara(work)
        saved = len(state['turns']) > seen
        if proc.returncode == 0: answer = ''
        look = load(work / 'look.json', {}) or {}
        reply = next((turn.get('text', '') for turn in reversed(state['turns']) if turn.get('role') == 'assistant'), '')
        row = dict(step=n, returncode=proc.returncode, saved=saved, reply=reply)
        if proc.returncode == 0:
            row.update(thoughts=look.get('thoughts'), action=look.get('action'), arguments=look.get('arguments'), coordinate=look.get('coordinate'), program=look.get('program'))
        else:
            row['stderr'] = proc.stderr.decode('utf-8', 'replace')[-2000:]
            record(journal, row)
            if state.get('failures', 0) >= FARA_FAILURES: break
            continue
        if look.get('action') in STOP_ACTIONS:
            shot(work, run / f'step_{n:02d}.png')
            record(journal, row)
            break
        block = refuse(look, before, recent)
        if block:
            row.update(hands=None, refused=block); record(journal, row)
            note, refused = block, refused + 1
            if refused >= FARA_FAILURES: break
            continue
        refused = 0; recent.append(same(look))
        hands = child(work, '--hands', look.get('program') or '')
        row['hands'] = hands.returncode
        if hands.returncode: row['stderr'] = hands.stderr.decode('utf-8', 'replace')[-2000:]
        shot(work, run / f'step_{n:02d}.png')
        record(journal, row)
