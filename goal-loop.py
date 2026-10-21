#!/usr/bin/env python3
"""General desktop loop. One wake is a goal, a window, and a step cap.

Fara is the eye and the hand's author. The first look is the goal.
Every later look is sent with no extra text, so main.py resends each
saved assistant reply: the thought, then the tool call.
"""
import argparse, ctypes, json, os, shutil, subprocess, sys, time
from pathlib import Path

import main

ROOT = Path(__file__).resolve().parent
CREATE_NO_WINDOW = 0x08000000
SW_HIDE, SW_SHOW, SW_RESTORE = 0, 5, 9
STOP_ACTIONS = frozenset(('terminate', 'ask_user_question'))
SETTLE = 3

def hide():
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd: ctypes.windll.user32.ShowWindow(hwnd, SW_HIDE)

def plain(text):
    text = str(text).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in '"\'': return text[1:-1].strip()
    return text

def foreground(class_name):
    user32 = ctypes.windll.user32
    found = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def each(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        if buf.value != class_name or not user32.IsWindowVisible(hwnd): return True
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        found.append((hwnd, title.value))
        return True
    user32.EnumWindows(proto(each), 0)
    if not found: return
    hwnd = next((h for h, title in found if 'chess' in title.lower()), found[0][0])
    user32.ShowWindow(hwnd, SW_RESTORE if user32.IsIconic(hwnd) else SW_SHOW)
    user32.SetForegroundWindow(hwnd)

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

def loop():
    hide()
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--work', required=True)
    parser.add_argument('--goal', required=True)
    parser.add_argument('--launch', required=True)
    parser.add_argument('--window', required=True)
    parser.add_argument('--answer', default='')
    parser.add_argument('--steps', type=int, default=60)
    parser.add_argument('--minutes', type=int, default=20)
    args = parser.parse_args()
    args.goal = plain(args.goal)
    os.environ['TRIDENT_BRAIN_MODEL'] = os.environ.get('TRIDENT_BRAIN_MODEL') or 'x-ai/grok-4.7'
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    region = work / 'region.txt'
    if not region.exists() or not region.read_text('utf-8').strip(): region.write_text('NONE', encoding='utf-8')
    (work / 'goal.txt').write_text(args.goal, encoding='utf-8')
    state = fara(work)
    run = work / 'runs' / time.strftime('goal_%Y%m%d_%H%M%S')
    run.mkdir(parents=True)
    journal = run / 'steps.jsonl'
    if state['awaiting_user'] and not args.answer:
        record(journal, dict(waiting=True, task=state.get('task', ''), thoughts=(load(work / 'look.json', {}) or {}).get('thoughts', '')))
        return
    if not state['turns']:
        subprocess.Popen(['cmd', '/c', 'start', '', args.launch], creationflags=CREATE_NO_WINDOW)
        time.sleep(SETTLE)
    deadline = time.monotonic() + args.minutes * 60
    for n in range(1, args.steps + 1):
        if (work / 'stop').exists() or time.monotonic() >= deadline: break
        state = fara(work)
        argument = retina_arg(state['turns'], state['awaiting_user'], args.goal, args.answer)
        if argument is None: break
        foreground(args.window)
        cap = child(work, '--capture')
        if cap.returncode:
            record(journal, dict(step=n, returncode=cap.returncode, saved=False, reply='', stderr=cap.stderr.decode('utf-8', 'replace')[-2000:]))
            continue
        seen = len(fara(work)['turns'])
        proc = child(work, '--retina', *argument)
        state = fara(work)
        saved = len(state['turns']) > seen
        if proc.returncode == 0: args.answer = ''
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

if __name__ == '__main__':
    loop()
