import json, os, queue, subprocess, threading, time, uuid
from pathlib import Path

from base import CODE, SET, Fail, event, log
from memory import current_lesson, enter, snapshot
from evidence import brief, usage_summary
from state import atomic

CHAT_CALLS = 42
CHAT_CHECKPOINT_CALLS = 9
BRAINS = ('grok-4.7-low', 'grok-4.7-low-fast', 'grok-4.7-medium', 'grok-4.7-medium-fast', 'grok-4.7-high', 'grok-4.7-high-fast', 'grok-4.7-xhigh', 'grok-4.7-xhigh-fast')
DEFAULT = 'grok-4.7-xhigh-fast'
MIND = '''You are Trident. Your code and your memory are this directory. main.py is the entry point, the other modules are your body, and story.txt and ledger.md are your memory. The sentence at the bottom is the wake.

Look, decide, act, write the story, or edit this directory and add a skill. The next command runs the code as it is on disk.

The sentence is the assignment. Story and ledger are notes from earlier work, not a second assignment. Edit the body or add a skill when the sentence asks for the program. Use the desktop tools only when the sentence asks for the screen. Skip a source file this wake has already read unless the change depends on it.

Call the tools with the commands in the tool list. Multiline text goes in a file. The command receives the path.

Trident's Fara eye is your only way to perceive the desktop or screenshots. Requests to read, view, inspect, analyze, or otherwise see the screen all go through --eye-file, even when your provider offers native image tools. Do not use native image tools, direct screenshot reads, or pixel-scanning scripts to substitute for Fara. Ordinary source-file reads and nonvisual command output remain available.

Give Fara one semantic action or observation request at a time, naming the window and target. Ask whether a control is visible before you ask for a click. If grounding is uncertain, ask a clearer question or use zoom. If Fara cannot see the target or is unavailable, report that limitation; do not invent coordinates or switch to native vision.

Fara grounds desktop actions; the body converts and executes its output. Copy Fara's returned program unchanged into a hand file. Do not calculate or revise desktop target coordinates. After acting, ask Fara what changed before choosing the next micro-task.

Use the body's --measure-file only for numerical diagnostics with explicit Fara-grounded bounds and exclusions. It measures visible pixels, not semantic coverage or hidden canvas. These perception instructions are a prompt contract, not a security boundary.

The decision call returns probabilities. P(yes) is the probability the claim is true, not a yes. For desktop decisions, put Fara's written observations in the narrative; do not attach screenshots to another model.

This chat can reset after {chat_calls} completed tool calls. After {checkpoint_calls} completed tool calls, update the story with this wake's current state and replace this wake's ledger lesson, then continue the task. Count all completed tool calls, including file reads, searches, writes, and shell calls. This checkpoint does not end the wake; do not call stop for it. Keep the story current after later meaningful changes.

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
    when = time.strftime('%H:%M:%S', time.localtime(end))
    return f'The wake has {minutes:g} minutes and the process ends at {when}. When under 20 percent of the time remains, stop starting new work, write the story and one ledger lesson, and stop.'


def read_text(path):
    return path.read_text(encoding='utf-8').strip() if path.exists() else ''


def prompt(app, sentence, minutes, end, docs):
    story, ledger, meta = snapshot(app.work)
    status = 'current wake' if meta.get('run') == SET['run'] else 'inherited; not current progress'
    story = json.dumps(dict(status=status, provenance=meta)) + '\n' + (story or '(empty)')
    packed, learned = brief(app.work, SET['run'], ledger)
    ledger = ledger or 'There is no ledger lesson yet.'
    measured = json.dumps(packed, ensure_ascii=False)
    return '\n\n'.join((MIND.format(budget=budget(float(minutes), end) if minutes else budget('', None), chat_calls=CHAT_CALLS, checkpoint_calls=CHAT_CHECKPOINT_CALLS), 'STORY', story, 'LEDGER (narrative claims)', ledger, 'MEASURED WHOLE-WAKE EVIDENCE', measured, 'LEARNED', learned, 'BRAIN', brain(sentence), 'TOOLS', *docs, 'SENTENCE', sentence.strip()))


def text_of(message):
    content = (message or {}).get('content') if isinstance(message, dict) else message
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(part.get('text', '') if isinstance(part, dict) else str(part) for part in content)
    return '' if content is None else str(content)


def compact_agent(item):
    """Keep the fields evidence counts. The stream file stores the raw line."""
    if not isinstance(item, dict):
        return item
    drop = {'message', 'text', 'tool_call', 'result', 'content'}
    kept = {}
    for key, value in item.items():
        if key in drop:
            continue
        if isinstance(value, str):
            kept[key] = value if len(value) <= 300 else value[:300]
        elif isinstance(value, (int, float, bool)) or value is None:
            kept[key] = value
        elif key == 'usage' and isinstance(value, dict):
            kept['usage'] = value
    message = item.get('message')
    if 'usage' not in kept and isinstance(message, dict) and isinstance(message.get('usage'), dict):
        kept['usage'] = message['usage']
    call = item.get('tool_call')
    if isinstance(call, dict):
        kept['tool'] = next((str(key)[:-8] for key in call if str(key).endswith('ToolCall')), None)
    if item.get('type') == 'thinking':
        kept['chars'] = len(item.get('text') or '')
    elif item.get('type') == 'assistant':
        kept['chars'] = len(text_of(message))
    elif item.get('type') == 'result':
        kept['chars'] = len(str(item.get('result') or ''))
    elif item.get('type') == 'user':
        kept['chars'] = len(text_of(message) if isinstance(message, dict) else str(message or ''))
    return kept


def tool_name(event):
    call = event.get('tool_call') or {}
    return next((key[:-8] for key in call if str(key).endswith('ToolCall')), 'tool')


def spawn(app, model, prompt_path, job_name):
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
    from base import PYTHON
    return subprocess.Popen([str(PYTHON), '-B', str(CODE / 'job_boot.py'), job_name, *argv], cwd=str(app.work), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=0x08000000)


def read_agent(proc, app, end, stop_grace=60):
    calls = running = 0
    last_say, thought = '', []
    stop_at = None
    result_at = terminal_reason = None
    inbox = queue.Queue()
    streams = app.work / 'streams'
    streams.mkdir(exist_ok=True)
    stream_path = streams / (uuid.uuid4().hex + '.stream')
    event('agent_stream', dict(path=str(stream_path)))
    def pump():
        try:
            with open(stream_path, 'wb') as saved:
                for raw in proc.stdout:
                    saved.write(raw if isinstance(raw, bytes) else raw.encode('utf-8'))
                    saved.flush()
                    inbox.put(raw)
        except (OSError, ValueError) as error:
            inbox.put(('error', str(error)))
        finally:
            inbox.put(None)
    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    def finish(reason, text):
        if thought:
            log('think', ''.join(thought))
        event('chat_end', dict(reason=reason, completed_calls=calls, running_tools=running))
        if proc.poll() is None:
            proc.kill()
        return reason, text
    while True:
        now = time.time()
        if end and now >= end:
            return finish('deadline', last_say)
        if (app.work / 'stop').exists():
            stop_at = stop_at or now + stop_grace
            if now >= stop_at:
                return finish('stopped', last_say)
        if result_at is not None and now >= result_at + .5:
            reason = 'failed_agent' if proc.poll() not in (None, 0) else terminal_reason
            return finish(reason, last_say)
        try:
            raw = inbox.get(timeout=.02)
        except queue.Empty:
            continue
        if raw is None:
            # The pipe may close just before Windows publishes the exit code.
            try:
                code = proc.wait(timeout=.05)
            except subprocess.TimeoutExpired:
                code = None
            reason = 'failed_agent' if code not in (None, 0) else (terminal_reason or ('stopped' if stop_at else 'failed_eof'))
            return finish(reason, last_say)
        if isinstance(raw, tuple):
            event('stream_error', raw[1])
            return finish('failed_stream', last_say)
        line = raw.decode('utf-8', 'replace').rstrip('\r\n') if isinstance(raw, bytes) else raw.rstrip('\r\n')
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            event('agent_raw', line)
            continue
        event('agent', compact_agent(item))
        if not isinstance(item, dict):
            continue
        kind, sub = item.get('type'), item.get('subtype')
        if kind == 'thinking' and sub == 'delta':
            thought.append(item.get('text') or '')
            continue
        if thought:
            log('think', ''.join(thought))
            thought = []
        if kind == 'assistant':
            said = text_of(item.get('message'))
            if said:
                last_say = said
                log('say', said)
        elif kind == 'tool_call' and sub == 'started':
            running += 1
            log('request ' + tool_name(item), json.dumps(item, ensure_ascii=False))
        elif kind == 'tool_call' and sub == 'completed':
            running = max(0, running - 1)
            calls += 1
            if calls >= CHAT_CALLS and running == 0 and not (app.work / 'stop').exists() and not (end and time.time() >= end):
                return finish('rotate', last_say)
        elif kind == 'system' and sub == 'init':
            log('start', str(item.get('model') or ''))
        elif kind == 'result':
            failed = item.get('is_error') or item.get('error') or str(sub or '').startswith('error')
            reason = 'failed_agent' if failed else ('stopped' if stop_at else 'completed')
            last_say = last_say or str(item.get('result') or '')
            if failed:
                return finish(reason, last_say)
            terminal_reason, result_at = reason, now


class Mind:
    name, flag = 'mind', 'mind_file'
    doc = '''$CALL --mind-file PATH
Reads the wake sentence from the file. Ends on a model result, failure, stop grace, or deadline; a chat rotates at 42 completed calls when no tools are running. Prompts include story provenance, ledger claims and whole-wake evidence. Multiline tool arguments are files.'''

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
    SET.update(wake_start=str(start), wake_end=str(end) if end else '')
    (app.work / 'stop').unlink(missing_ok=True)
    atomic(app.work / 'region.txt', 'NONE')
    if sentence:
        atomic(app.work / 'goal.txt', sentence)
    job = proc = None
    outcome, said = 'failed', ''
    previous_chat = os.environ.get('TRIDENT_CHAT_ID')
    chat_index = 0
    try:
        event('wake_start', dict(start=start, end=end, sentence=sentence, settings=SET.copy()))
        while True:
            if end and time.time() >= end:
                outcome = 'deadline'
                break
            if (app.work / 'stop').exists():
                outcome = 'stopped'
                break
            prompt_path = app.work / 'prompt.txt'
            chat_index += 1
            os.environ['TRIDENT_CHAT_ID'] = f"{SET['run']}:{chat_index}"
            text = prompt(app, sentence, minutes, end, docs_of())
            atomic(prompt_path, text)
            event('chat_prompt', dict(text=text, model=brain(sentence)))
            job_name = 'Trident-' + uuid.uuid4().hex
            job = app.desk.open_job(name=job_name)
            try:
                proc = spawn(app, brain(sentence), prompt_path, job_name)
                outcome, said = read_agent(proc, app, end)
            finally:
                app.desk.close_job(job)
                job = None
                if proc is not None and proc.poll() is None:
                    proc.kill()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            event('agent_exit', dict(returncode=proc.returncode, reason=outcome))
            proc.stdout.close()
            proc = None
            if outcome != 'rotate':
                break
        report = said or read_text(app.work / 'story.txt')
        if report:
            log('report', report)
        return dict(state=outcome, minutes=round((time.time() - start) / 60, 1))
    except BaseException as error:
        outcome = 'failed'
        event('wake_error', dict(type=type(error).__name__, message=str(error)))
        raise
    finally:
        app.desk.close_job(job)
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
            proc.stdout.close()
        enter(app.work, current_lesson(app.work), outcome)
        event('wake_end', dict(reason=outcome, start=start, end=time.time(), usage=usage_summary(app.work / f"run_{SET['run']}.jsonl")))
        if previous_chat is None:
            os.environ.pop('TRIDENT_CHAT_ID', None)
        else:
            os.environ['TRIDENT_CHAT_ID'] = previous_chat


mind = Mind()
