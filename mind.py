import json, os, subprocess, time
from pathlib import Path

from base import CODE, SET, Fail, log
from memory import current_lesson, enter, learn

CHAT_CALLS = 12
BRAINS = ('grok-4.7-low', 'grok-4.7-low-fast', 'grok-4.7-medium', 'grok-4.7-medium-fast', 'grok-4.7-high', 'grok-4.7-high-fast', 'grok-4.7-xhigh', 'grok-4.7-xhigh-fast')
DEFAULT = 'grok-4.7-medium-fast'
MIND = '''You are Trident. Your code and your memory are this directory. main.py is the entry point, the other modules are your body, and story.txt and ledger.md are your memory. The sentence at the bottom is the wake.

Look, decide, act, write the story, or edit this directory and add a skill. The next command runs the code as it is on disk.

Call the tools with the commands in the tool list. Multiline text goes in a file. The command receives the path.

The eye looks at the latest capture and returns one point or one answer. Ask whether a control is visible before you ask for a click. A click request for something that is not drawn can hit a nearby pixel.

The decision call returns probabilities. P(yes) is the probability the claim is true, not a yes. Attach a picture only when you need a box. Put the evidence in the narrative for a grade.

The hand uses 0-1000 of the region. A stroke is a series of short moves with the button down. Then look again.

{budget}

Do not claim a deliverable is met without evidence from this wake.'''


def brain(sentence):
    forced = SET.get('mind_model') or ''
    if forced:
        return forced
    said = str(sentence).lower()
    for slug in sorted(BRAINS, key=len, reverse=True):
        if slug in said:
            return slug
    return DEFAULT


def budget(minutes, end):
    if not minutes:
        return 'No end time was given to the process. If the sentence names one, keep it.'
    when = time.strftime('%H:%M', time.localtime(end))
    return f'The wake has {minutes:g} minutes and the process ends at {when}. When under 20 percent of the time remains, stop starting new work, write the story and one ledger lesson, and stop.'


def read_text(path):
    return path.read_text(encoding='utf-8').strip() if path.exists() else ''


def prompt(app, sentence, minutes, end, docs):
    story = read_text(app.work / 'story.txt') or '(empty)'
    ledger = read_text(app.work / 'ledger.md') or 'There is no ledger lesson yet.'
    learned = learn(app.work)['text']
    return '\n\n'.join((MIND.format(budget=budget(float(minutes), end) if minutes else budget('', None)), 'STORY', story, 'LEDGER', ledger, 'LEARNED', learned, 'BRAIN', brain(sentence), 'TOOLS', *docs, 'SENTENCE', sentence.strip()))


def text_of(message):
    content = (message or {}).get('content') if isinstance(message, dict) else message
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(part.get('text', '') if isinstance(part, dict) else str(part) for part in content)
    return '' if content is None else str(content)


def tool_name(event):
    call = event.get('tool_call') or {}
    return next((key[:-8] for key in call if str(key).endswith('ToolCall')), 'tool')


def spawn(app, model, prompt_path):
    vendor = app.work / 'agent'
    profile = vendor / 'config'
    profile.mkdir(parents=True, exist_ok=True)
    (vendor / 'data').mkdir(parents=True, exist_ok=True)
    temp = CODE.parent / 'temp'
    temp.mkdir(exist_ok=True)
    config = dict(version=1, permissions=dict(allow=['Read(**)', 'Write(**)', 'Shell(*)', 'WebFetch(*)', 'Mcp(*:*)'], deny=[]), sandbox=dict(mode='disabled'), approvalMode='unrestricted', autoAcceptWebSearch=True)
    (profile / 'cli-config.json').write_text(json.dumps(config), encoding='utf-8')
    versions = Path(os.environ['LOCALAPPDATA']) / 'cursor-agent' / 'versions'
    folders = [path for path in versions.iterdir() if path.name[:1].isdigit() and (path / 'index.js').exists()] if versions.exists() else []
    if not folders:
        raise Fail('the agent runtime is not installed')
    folder = max(folders)
    env = os.environ | dict(CURSOR_CONFIG_DIR=str(profile), CURSOR_DATA_DIR=str(vendor / 'data'), TEMP=str(temp), TMP=str(temp), PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    env.pop('OPENROUTER_API_KEY', None)
    boot = "process.argv.push(require('node:fs').readFileSync(process.argv.pop(),'utf8'));require(process.argv[1]);"
    argv = [str(folder / 'node.exe'), '-e', boot, str(folder / 'index.js'), '--trust', '--force', '-p', '--model', model, '--workspace', str(app.work), '--output-format', 'stream-json', str(prompt_path)]
    return subprocess.Popen(argv, cwd=str(app.work), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, creationflags=0x08000000)


def read_agent(proc, app, end):
    calls = running = 0
    last_say, thought = '', []
    stop_at = None
    for raw in proc.stdout:
        now = time.time()
        if end and now >= end:
            proc.kill()
            return 'ended', last_say
        if (app.work / 'stop').exists():
            stop_at = stop_at or now + 60
            if now >= stop_at:
                proc.kill()
                return 'stopped', last_say
        line = raw.decode('utf-8', 'replace').rstrip('\n')
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind, sub = event.get('type'), event.get('subtype')
        if kind == 'thinking' and sub == 'delta':
            thought.append(event.get('text') or '')
            continue
        if thought:
            log('think', ''.join(thought)[:3000])
            thought = []
        if kind == 'assistant':
            said = text_of(event.get('message'))
            if said:
                last_say = said
                log('say', said[:4000])
        elif kind == 'tool_call' and sub == 'started':
            running += 1
            log('request ' + tool_name(event), '')
        elif kind == 'tool_call' and sub == 'completed':
            running = max(0, running - 1)
            calls += 1
            if calls >= CHAT_CALLS and running == 0 and not (app.work / 'stop').exists() and not (end and time.time() >= end):
                proc.kill()
                return 'rotate', last_say
        elif kind == 'system' and sub == 'init':
            log('start', str(event.get('model') or ''))
        elif kind == 'result':
            proc.kill()
            return 'done', last_say or str(event.get('result') or '')
    if (app.work / 'stop').exists():
        return 'stopped', last_say
    return 'done', last_say


class Mind:
    name, flag = 'mind', 'mind_file'
    doc = '''$CALL --mind-file PATH
Reads the wake sentence from the file and runs until the stop file appears or the end time passes. The prompt includes story.txt and ledger.md. Multiline tool arguments are files.'''

    def run(self, app, path, dry=False, docs_of=None):
        sentence = read_text_file(path)
        minutes = float(SET['minutes']) if SET.get('minutes') else None
        docs = docs_of() if docs_of else ()
        if dry:
            end = time.time() + (minutes or 0) * 60
            return dict(dry=True, prompt=prompt(app, sentence, minutes, end, docs), model=brain(sentence))
        return drive(app, sentence, minutes, docs_of or (lambda: ()))


def read_text_file(path):
    from base import read_file
    return read_file(path).strip()


def drive(app, sentence, minutes, docs_of):
    start = time.time()
    end = start + minutes * 60 if minutes else None
    (app.work / 'stop').unlink(missing_ok=True)
    (app.work / 'region.txt').write_text('NONE', encoding='utf-8')
    if sentence:
        (app.work / 'goal.txt').write_text(sentence, encoding='utf-8')
    job = None
    outcome, said = 'ended', ''
    try:
        while True:
            if end and time.time() >= end:
                outcome = 'ended'
                break
            if (app.work / 'stop').exists():
                outcome = 'stopped'
                break
            prompt_path = app.work / 'prompt.txt'
            prompt_path.write_text(prompt(app, sentence, minutes, end, docs_of()), encoding='utf-8')
            proc = spawn(app, brain(sentence), prompt_path)
            if job is None:
                job = app.desk.open_job(proc._handle)
            else:
                app.desk.assign_job(job, proc._handle)
            outcome, said = read_agent(proc, app, end)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            if outcome != 'rotate':
                break
        report = said or read_text(app.work / 'story.txt')
        if report:
            log('report', report)
        state = 'stopped' if outcome == 'stopped' or (app.work / 'stop').exists() else 'ended'
        enter(app.work, current_lesson(app.work), state)
        return dict(state=state, minutes=round((time.time() - start) / 60, 1))
    finally:
        app.desk.close_job(job)


mind = Mind()
