import json, os, subprocess, sys, tempfile
from pathlib import Path

MAIN = Path(__file__).resolve().parent / 'main.py'
PY = sys.executable
ENV = os.environ | {'TRIDENT_JOURNAL': '1', 'PYTHONDONTWRITEBYTECODE': '1'}


def run(work, *args):
    return subprocess.run([PY, '-B', str(MAIN), '--work', str(work), *args], capture_output=True, text=True, encoding='utf-8', errors='replace', env=ENV)


def need(name, proc, test):
    if not test:
        print(name, 'FAILED', proc.returncode)
        print(proc.stdout[-1500:])
        print(proc.stderr[-1500:])
        return False
    print(name, 'ok')
    return True


def main():
    ok = True
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        eye = work / 'goal.txt'
        eye.write_text('What is drawn?\n', encoding='utf-8')
        proc = run(work, '--run', 'eye', '--dry', '--eye-file', str(eye))
        body = json.loads(proc.stdout) if proc.returncode == 0 else {}
        system = body.get('system', '')
        ok &= need('eye', proc, proc.returncode == 0 and '1440' in system and '800' in system and not any(word in system.lower() for word in ('browser', 'fara', 'retina')))
        card = work / 'decide.txt'
        card.write_text('A flat picture is on the canvas.\n---\nreal?The picture is a realistic painting covering the canvas\n', encoding='utf-8')
        proc = run(work, '--run', 'decide', '--dry', '--decide-file', str(card))
        text = proc.stdout
        ok &= need('decide', proc, proc.returncode == 0 and 'P(yes)=' in text and ': yes ' not in text)
        past = work / 'run_past.md'
        past.write_text('## 10:00:00 say\n\nI will look at the screen first.The sentence is not met. BLOB\n\n## 10:00:01 request eye\n\ngoal\n\n## 10:00:02 request hands\n\nmove\n\n## 10:00:03 request hands\n\nmove\n\n## 10:00:04 report\n\nThe sentence is not met.\n\n', encoding='utf-8')
        (work / 'ledger.md').write_text('## wake past 2026-10-26 10:00 used 1 min, stopped\nprove one stroke\n', encoding='utf-8')
        proc = run(work, '--run', 'learn', '--learn')
        ok &= need('learn', proc, proc.returncode == 0 and 'file change' not in proc.stdout and 'Start from the newest ledger line.' in proc.stdout)
        proc = run(work, '--run', 'recall', '--recall')
        wakes = json.loads(proc.stdout).get('wakes') if proc.returncode == 0 else []
        final = wakes[-1]['final'] if wakes else ''
        ok &= need('recall', proc, final == 'The sentence is not met.' and not final.startswith("I'll look"))
        fresh = Path(tempfile.mkdtemp())
        (fresh / 'ledger.md').write_text('KEEP\n', encoding='utf-8')
        (fresh / 'story.txt').write_text('GONE\n', encoding='utf-8')
        proc = run(fresh, '--run', 'fresh', '--fresh')
        ok &= need('fresh', proc, proc.returncode == 0 and (fresh / 'ledger.md').read_text(encoding='utf-8') == 'KEEP\n' and not (fresh / 'story.txt').exists())
        proc = run(work, '--run', 'missing', '--eye-file', str(work / 'no-such-file.txt'))
        ok &= need('missing', proc, proc.returncode != 0 and 'not a file' in proc.stderr)
        sentence = work / 'sentence.txt'
        sentence.write_text('Paint a cat.\n', encoding='utf-8')
        (work / 'story.txt').write_text('CHECKLIST\n1. A cat.\n', encoding='utf-8')
        proc = run(work, '--run', 'mind', '--minutes', '40', '--dry', '--mind-file', str(sentence))
        prompt = json.loads(proc.stdout).get('prompt', '') if proc.returncode == 0 else ''
        banned = ('fara', 'retina', 'jev', 'browser', 'fifth', 'score goes')
        ok &= need('mind', proc, proc.returncode == 0 and 'CHECKLIST' in prompt and '20 percent' in prompt and 'P(yes)' in prompt and not any(word in prompt.lower() for word in banned))
    if not ok:
        sys.exit(1)


if __name__ == '__main__':
    main()
