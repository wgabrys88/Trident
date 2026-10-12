import asyncio, ctypes, json, msvcrt, os, sys, threading, time
from pathlib import Path
from urllib.parse import urlparse
from fabric import ARGUMENTS, CFG, ROOT, RUN, Job, execute, log, object_in, publish_owner, request, token, validate, write

DEVICES = ['manipulator', 'retina', 'phonation', 'relay', 'cochlea', 'discern']
GUI = {'launch', 'browse', 'focus', 'key', 'type', 'click', 'draw'}
KEEP = ('observed', 'box', 'image', 'error', 'finish', 'window', 'desktop')

class Body:
    def __init__(self):
        self.jobs, self.logs = {}, []
    def spawn(self, role, command=None):
        stream = (RUN/f'{role}.log').open('ab')
        self.logs.append(stream)
        self.jobs[role] = Job(command or [sys.executable, '-B', str(ROOT/f'{role}.py')],
                              cwd=ROOT, stdout=stream, stderr=stream)
        log('device_started', role=role, pid=self.jobs[role].process.pid)
    def stop(self, role):
        job = self.jobs.get(role)
        if not job:
            return
        job.close()
        job.process.wait(timeout=3)
        log('device_stopped', role=role, code=job.process.returncode)
        if role == 'manipulator':
            ctypes.windll.user32.mouse_event(4, 0, 0, 0, 0)
            for key in (0x10, 0x11, 0x12, 0x5B):
                ctypes.windll.user32.keybd_event(key, 0, 2, 0)
    async def call(self, role, op, args):
        job = self.jobs.get(role)
        if not job or job.process.poll() is not None:
            return dict(error=f'{role} is unavailable', outcome='not_attempted', verified=False)
        return await request(role, op, args, CFG['seconds'])
    def close(self):
        for role in list(self.jobs):
            try:
                self.stop(role)
            except Exception as error:
                log('cleanup_failed', role=role, error=str(error))
        for stream in self.logs:
            stream.close()

def load_key():
    path = ROOT/'.env'
    if path.exists():
        for line in path.read_text('utf-8-sig').splitlines():
            line = line.strip()
            if line.startswith('OPENROUTER_API_KEY='):
                os.environ['OPENROUTER_API_KEY'] = line.split('=', 1)[1].strip().strip('"').strip("'")
    if not os.environ.get('OPENROUTER_API_KEY'):
        raise SystemExit('OPENROUTER_API_KEY is required in the environment or .env')

def cursor_dir():
    root = Path(os.environ['LOCALAPPDATA'])/'cursor-agent'/'versions'
    builds = [path for path in root.iterdir() if path.is_dir() and (path/'node.exe').exists() and (path/'index.js').exists()]
    return max(builds, key=lambda path: path.name)

def luna(prompt):
    folder = cursor_dir()
    prompt_file = RUN/(token()+'.prompt')
    prompt_file.write_text(prompt, encoding='utf-8')
    bootstrap = "process.argv.push(require('node:fs').readFileSync(process.argv.pop(),'utf8'));require(process.argv[1]);"
    raw = execute([str(folder/'node.exe'), '-e', bootstrap, str(folder/'index.js'), '--trust',
                   '-p', '--model', CFG['mind_model'], '--workspace', str(ROOT),
                   '--output-format', 'stream-json', str(prompt_file)], CFG['seconds'], cwd=ROOT).decode('utf-8', errors='replace')
    prompt_file.with_suffix('.stream.jsonl').write_text(raw, encoding='utf-8')
    texts = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get('type') == 'assistant':
            texts.append(''.join(part.get('text', '') for part in event.get('message', {}).get('content', []) if part.get('type') == 'text'))
    if not texts:
        raise ValueError('Luna returned no text')
    log('inference', prompt=str(prompt_file), raw=texts[-1])
    return validate(object_in(texts[-1]))

def prose(look):
    if not look:
        return None
    return {key: look[key] for key in KEEP if look.get(key) is not None}

def save(state):
    write(ROOT/'private'/'continuity.json', state)

async def see(body, question, region=None):
    shot = await body.call('manipulator', 'capture', {})
    if not shot.get('image'):
        return dict(observed=shot.get('error') or 'The screen was not captured', box=None)
    args = dict(image=shot['image'], question=question)
    if region:
        args['region'] = region
    look = await body.call('retina', 'inspect', args)
    look.setdefault('observed', look.get('error') or '')
    look['desktop'] = shot.get('desktop')
    look['window'] = shot.get('window')
    look['at'] = shot.get('at')
    return look

def arm(state, ident, look):
    if look.get('box') and look.get('window') and look.get('desktop') and look.get('at'):
        state['targets'][ident] = dict(box=look['box'], at=look['at'], desktop=look['desktop'], window=look['window'])
        return ident

async def judge(body, state, questions):
    judged = await body.call('discern', 'decide', dict(state=state, questions=questions))
    if set((judged.get('answers') or {})) != set(questions):
        raise ValueError(judged.get('error') or 'Jev did not answer')
    return judged

async def act(body, state, decision, ident):
    op, args = decision['do'], dict(decision['args'])
    if op in ('click', 'draw'):
        target = state['targets'].get(args['target'])
        if not target:
            return dict(error='The eye has no box for that target', outcome='not_attempted', verified=False)
        args = args | dict(target=target)
    if op in ('say', 'done'):
        result = await body.call('phonation', 'speak', dict(text=args['text'], narration_id=ident))
        if op == 'done':
            state['goal'] = ''
        return result
    if op == 'rest':
        return dict(outcome='completed', resting=True)
    if op == 'recall':
        folder, hits = ROOT/'private'/'outcomes', []
        if folder.exists():
            for path in sorted(folder.glob('*.json')):
                text = path.read_text('utf-8')
                if args['query'].casefold() in text.casefold():
                    hits.append(dict(path=str(path), record=text))
        return dict(outcome='completed', records=hits)
    if op == 'look':
        return dict(outcome='completed')
    role = 'relay' if op in ('authorize', 'dial') else 'manipulator'
    return await body.call(role, op, args | dict(owner_id=state['owner']['id'], action_id=ident))

async def main():
    load_key()
    private = ROOT/'private'
    private.mkdir(exist_ok=True)
    lock = (private/'living.lock').open('a+b')
    if lock.tell() == 0:
        lock.write(b'0')
        lock.flush()
    lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    run = private/token()
    run.mkdir()
    os.environ['TRIDENT_RUN'] = str(run)
    import fabric
    global RUN
    RUN = fabric.RUN = run
    for role in DEVICES:
        (RUN/role).mkdir()
    outputs = ROOT/'runtime'/'output'
    outputs.mkdir(parents=True, exist_ok=True)
    state_path = private/'continuity.json'
    state = dict(record=[], goal='', targets={}, owner=dict(id=token(), text='', source='no-owner-goal', at=time.time()))
    if state_path.exists():
        state.update(json.loads(state_path.read_text('utf-8')))
    if state.get('pending'):
        state['record'].append(dict(id=state.pop('pending')['id'], error='Process stopped during action'))
    state.update(targets=state.get('targets') or {}, active=True, process_state='running', run=str(run))
    save(state)
    body = Body()
    def console():
        for text in sys.stdin:
            if text.strip():
                publish_owner(text.strip(), 'local-owner')
    threading.Thread(target=console, daemon=True).start()
    try:
        models = Path(os.environ['LOCALAPPDATA'])/'Trident'/'models'
        body.spawn('vision-server', [str(ROOT/'runtime'/'llama'/'llama-server.exe'),
            '-m', str(models/CFG['vision_model']), '--mmproj', str(models/CFG['vision_projector']),
            '-ngl', '999', '--fit', 'off', '--ctx-size', str(CFG['ctx_size']),
            '--image-max-tokens', str(CFG['image_tokens']), '--parallel', '1',
            '--host', '127.0.0.1', '--port', str(urlparse(CFG['vision_url']).port), '--alias', CFG['vision_alias']])
        for role in DEVICES:
            body.spawn(role)
        print(f'Trident alive. Run: {RUN}\nType or send owner Telegram text or voice. Ctrl+C stops.', flush=True)
        if len(sys.argv) > 1:
            publish_owner(' '.join(sys.argv[1:]), 'local-owner')
        state['record'].append(dict(arrival=await body.call('phonation', 'speak', dict(text='Trident is here.', narration_id='arrival-'+run.name))))
        policy = (ROOT/'instinct.txt').read_text('utf-8')
        capabilities = {op: dict(required=sorted(ARGUMENTS[op][0]), optional=sorted(ARGUMENTS[op][1]), description=text)
                        for op, text in CFG['capabilities'].items()}
        announced, wake = set(), 0
        while not (RUN/'stop').exists():
            for role, job in list(body.jobs.items()):
                if job.process.poll() is not None and role not in announced:
                    announced.add(role)
                    state['record'].append(dict(device=role, code=job.process.returncode))
                    try:
                        body.stop(role)
                    except Exception as error:
                        log('cleanup_failed', role=role, error=str(error))
            for path in sorted((private/'inbox').glob('*.json')):
                message = json.loads(path.read_text('utf-8'))
                path.unlink()
                state['record'].append(dict(message=message))
                if message['source'] in ('local-owner', 'telegram-owner'):
                    state.update(owner=message, goal=message['text'])
                    write(private/'owner.json', message)
                wake = 0
            if time.time() < wake:
                await asyncio.sleep(.1)
                continue
            ident, proposal, scored, decision, result, review, verdict = token(), None, None, None, None, None, None
            try:
                seen = await see(body, state['goal'] or 'Describe every visible window, icon, control, and readable string in full sentences.')
                target = arm(state, ident, seen)
                context = dict(goal=state['goal'], owner=state['owner'], screen=prose(seen), target=target,
                               targets=state['targets'], record=state['record'], output_directory=str(outputs),
                               capabilities=capabilities)
                proposal = await asyncio.to_thread(luna, policy+'\n'+json.dumps(context, ensure_ascii=False))
                scored = await judge(body, dict(goal=state['goal'], screen=prose(seen), proposal=proposal), proposal['questions'])
                decision = await asyncio.to_thread(luna, policy+'\n'+json.dumps(context | dict(proposal=proposal, judged=scored), ensure_ascii=False))
                confirmed = await judge(body, dict(goal=state['goal'], screen=prose(seen), proposal=proposal, scored=scored, decision=decision), decision['questions'])
                if (RUN/'stop').exists() or any(json.loads(item.read_text('utf-8'))['source'] in ('local-owner', 'telegram-owner') for item in (private/'inbox').glob('*.json')):
                    state['record'].append(dict(id=ident, screen=prose(seen), proposal=proposal, scored=scored, decision=decision, confirmed=confirmed, superseded=True))
                    save(state)
                    continue
                state['pending'] = dict(id=ident, decision=decision)
                save(state)
                result = await act(body, state, decision, ident)
                if decision['do'] in GUI or decision['do'] == 'look':
                    review = await see(body, decision['args'].get('question') if decision['do'] == 'look' else decision['expect'],
                                       decision['args'].get('region') if decision['do'] == 'look' else None)
                    arm(state, ident, review)
                    verdict = await judge(body, dict(expect=decision['expect'], review=prose(review), action=result), decision['questions'])
                if decision['do'] == 'rest':
                    wake = time.time()+60
                entry = dict(id=ident, screen=prose(seen), target=target, proposal=proposal, scored=scored,
                             decision=decision, confirmed=confirmed, result=result, review=prose(review), verdict=verdict)
            except Exception as error:
                log('turn_failed', id=ident, error=str(error))
                entry = dict(id=ident, error=f'{type(error).__name__}: {error}', proposal=proposal, scored=scored,
                             decision=decision, result=result, review=prose(review), verdict=verdict)
                wake = time.time()+60
            state['record'].append(entry)
            write(private/'outcomes'/(ident+'.json'), entry)
            state.pop('pending', None)
            save(state)
    finally:
        body.close()
        state.update(active=False, process_state='stopped')
        save(state)
        lock.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
