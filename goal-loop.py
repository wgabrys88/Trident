#!/usr/bin/env python3
"""Desktop loop. One wake is the goal sentence.

Fara is the eye and the hand's author. The first look is the goal.
Every later look is sent with no extra text, so main.py resends each
saved assistant reply: the thought, then the tool call.
"""
import json, os, shutil, subprocess, sys, time
from pathlib import Path

import main

ROOT = Path(__file__).resolve().parent
CREATE_NO_WINDOW = 0x08000000
STOP_ACTIONS = frozenset(('terminate', 'ask_user_question'))
STEPS, MINUTES = 60, 20

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
    for n in range(1, STEPS + 1):
        if (work / 'stop').exists() or time.monotonic() >= deadline: break
        state = fara(work)
        argument = retina_arg(state['turns'], state['awaiting_user'], goal, answer)
        if argument is None: break
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
            continue
        if look.get('action') in STOP_ACTIONS:
            shot(work, run / f'step_{n:02d}.png')
            record(journal, row)
            break
        hands = child(work, '--hands', look.get('program') or '')
        row['hands'] = hands.returncode
        if hands.returncode: row['stderr'] = hands.stderr.decode('utf-8', 'replace')[-2000:]
        shot(work, run / f'step_{n:02d}.png')
        record(journal, row)
