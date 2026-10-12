import asyncio, json, msvcrt, os, subprocess, sys, threading, time
from pathlib import Path
from fabric import CFG, ROOT, RUN, execute, log, object_in, publish_owner, request, token, write

DEVICES = ['manipulator', 'retina', 'phonation', 'relay', 'cochlea']
GUI = {'launch', 'browse', 'focus', 'key', 'type', 'click', 'draw'}

class Body:
    def __init__(self):
        self.processes, self.logs, self.failures = {}, [], {}
    def spawn(self, role, command=None):
        stream = (RUN / f'{role}.log').open('ab')
        self.logs.append(stream)
        argv = command or [sys.executable, '-B', str(ROOT / f'{role}.py')]
        try:
            process = subprocess.Popen(argv, cwd=ROOT, stdout=stream, stderr=stream,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as error:
            self.failures[role] = str(error)
            log('device_spawn_failed', role=role, error=str(error))
            return
        self.processes[role] = process
        log('device_started', role=role, pid=process.pid)
    def stop(self, role):
        process = self.processes[role]
        if process.poll() is None:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=5)
            process.wait(timeout=5)
        log('device_stopped', role=role, code=process.returncode)
    async def call(self, role, op, args, seconds):
        if role not in self.processes or self.processes[role].poll() is not None:
            return dict(error=f'{role} exited; request not attempted', verified=False)
        try:
            return await request(role, op, args, seconds)
        except TimeoutError:
            self.stop(role)
            if role == 'retina' and 'vision-server' in self.processes:
                self.stop('vision-server')
            return dict(error=f'{role} timed out; outcome unknown; device stopped', verified=False)
    def close(self):
        for role in self.processes:
            self.stop(role)
        for stream in self.logs:
            stream.close()

def infer(prompt):
    folder = Path(os.environ['LOCALAPPDATA']) / 'cursor-agent' / 'versions' / CFG['cursor_version']
    prompt_file = RUN / (token()+'.prompt')
    prompt_file.write_text(prompt, encoding='utf-8')
    bootstrap = "process.argv.push(require('node:fs').readFileSync(process.argv.pop(),'utf8'));require(process.argv[1]);"
    argv = [str(folder/'node.exe'), '-e', bootstrap, str(folder/'index.js'), '--mode', 'ask', '--trust',
            '-p', '--model', CFG['mind_model'], '--workspace', str(ROOT), '--output-format', 'text', str(prompt_file)]
    raw = execute(argv, CFG['mind_seconds'], cwd=ROOT).decode('utf-8', errors='replace')
    log('inference', prompt=str(prompt_file), raw=raw)
    return object_in(raw)

async def main():
    private = ROOT / 'private'
    private.mkdir(exist_ok=True)
    lock = (private / 'living.lock').open('a+b')
    lock.write(b'0'); lock.flush(); lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    run = private / token()
    run.mkdir()
    os.environ['TRIDENT_RUN'] = str(run)
    import fabric
    global RUN
    RUN = fabric.RUN = run
    for role in [*DEVICES, 'inbox']:
        (RUN/role).mkdir()
    outputs = ROOT / 'runtime' / 'output'
    outputs.mkdir(parents=True, exist_ok=True)
    state_path = private / 'continuity.json'
    state = json.loads(state_path.read_text('utf-8')) if state_path.exists() else dict(story='', record=[], goal='', targets={})
    state['targets'] = {}
    body, last, active, owner, reported = Body(), {}, False, {}, False
    if state['record']:
        last = state['record'][-1].get('result', {})
    def console():
        for text in sys.stdin:
            if text.strip():
                publish_owner(text.strip(), 'local-owner')
    threading.Thread(target=console, daemon=True).start()
    try:
        models = Path(os.environ['LOCALAPPDATA']) / 'Trident' / 'models'
        body.spawn('vision-server', [str(ROOT/'runtime'/'llama'/'llama-server.exe'),
            '-m', str(models/'LFM2.5-VL-3B-Q4_K_M.gguf'), '--mmproj', str(models/'mmproj-LFM2.5-VL-3B-Q8_0.gguf'),
            '--device', 'CUDA0', '--mmproj-device', 'CUDA0', '-ngl', '999', '--fit', 'off',
            '--ctx-size', '4096', '--parallel', '1', '--image-min-tokens', '64', '--image-max-tokens', '256',
            '--reasoning', 'off', '--host', '127.0.0.1', '--port', '8188', '--alias', 'retina'])
        for role in DEVICES:
            body.spawn(role)
        print(f'Trident alive. Run: {RUN}\nType or send the owner Telegram text/voice note. Ctrl+C stops.', flush=True)
        if len(sys.argv) > 1:
            publish_owner(' '.join(sys.argv[1:]), 'local-owner')
        policy = (ROOT/'instinct.md').read_text('utf-8')
        announced = set()
        while True:
            failures = body.failures | {role:f'exited with {process.returncode}' for role,process in body.processes.items() if process.poll() is not None}
            for role, detail in failures.items():
                if role not in announced:
                    announced.add(role)
                    failure = dict(error=f'{role}: {detail}', verified=False)
                    state['record'].append(failure)
                    log('device_failure', role=role, **failure)
                    if role != 'phonation':
                        delivery = await body.call('phonation', 'speak', dict(text=f'My {role} device stopped. Its last outcome is unconfirmed.'), 40)
                        state['record'].append(dict(delivery=delivery))
            messages = sorted((RUN/'inbox').glob('*.json'))
            if messages:
                message_path = messages[0]
                owner = json.loads(message_path.read_text('utf-8'))
                message_path.unlink()
                current = RUN/'owner.json'
                current.unlink(missing_ok=True)
                write(current, owner)
                state['goal'], active, reported = owner['text'], True, False
                state['record'].append(dict(owner=owner))
            if not active:
                await asyncio.sleep(.1)
                continue
            while len(json.dumps(state['record'])) > CFG['context_chars']:
                state['record'].pop(0)
            context = dict(goal=state['goal'], story=state['story'], record=state['record'],
                           last=last, capabilities=CFG['capabilities'], output_directory=str(outputs))
            decision, result = {}, {}
            try:
                decision = await asyncio.to_thread(infer, policy+'\n'+json.dumps(context, ensure_ascii=False))
                op, args = decision['do'], decision['args']
                if op not in CFG['capabilities'] or not decision['expect'] or not isinstance(args, dict):
                    raise ValueError('Invalid action')
                if last and not decision['compare'].strip():
                    raise ValueError('Compare prediction against outcome before acting')
                if len(decision['story']) > CFG['story_chars']:
                    raise ValueError('Keep the complete cumulative story within the supplied story limit')
                state['story'] = decision['story']
                log('prediction', decision=decision)
                if op in ('say', 'done'):
                    audit_prompt = ('Audit this proposed spoken sentence against the actual record. '
                        'For any claimed completed GUI effect, READ the supplied screenshot image using your image read tool; '
                        'a Yes, receipt, path or overview proves nothing. If image reading is unavailable deny the effect claim. '
                        'Conversation, intentions, questions and accurate blocker reports need no completed effect. '
                        'Check who did what: an owner saying they received a note does not mean the assistant received an owner voice note. '
                        'For each completed GUI claim cite the EXACT proof screenshot path that you read. '
                        'Return one JSON object: {"allowed":boolean,"claims_effect":boolean,"images_read":["paths"],"reason":"literal evidence"}.\n'
                        +json.dumps(dict(sentence=args['text'], story=state['story'], last=last, record=state['record']), ensure_ascii=False))
                    audit = await asyncio.to_thread(infer, audit_prompt)
                    proofs = {entry.get('result', {}).get('look', {}).get('image') for entry in state['record']
                              if entry.get('result', {}).get('look', {}).get('verified')}
                    cited = audit.get('images_read', [])
                    if audit.get('allowed') is not True or (audit.get('claims_effect') and
                        (not cited or any(path not in proofs for path in cited))):
                        raise ValueError('Spoken claim withheld: '+audit['reason'])
                    result = await body.call('phonation', 'speak', args, 40)
                    reported = bool(result.get('delivered'))
                    print(args['text'], result, flush=True)
                    if op == 'done' and result.get('delivered'):
                        active = False
                elif op == 'rest':
                    if not reported:
                        raise ValueError('Report the current outcome aloud before resting')
                    result, active = dict(resting=True), False
                elif op == 'recall':
                    query = args['query'].casefold()
                    matches = [line for path in private.glob('*/*.jsonl') for line in path.read_text('utf-8').splitlines() if query in line.casefold()]
                    result = dict(history='\n'.join(matches)[-8000:])
                elif op in ('authorize', 'dial'):
                    reported = False
                    result = await body.call('relay', op, args | dict(owner_id=owner['id']), 15)
                elif op == 'look' or op in GUI:
                    reported = False
                    if op in GUI and not decision['check'].strip():
                        raise ValueError('A GUI action requires a pointed check before execution')
                    if op in ('click', 'draw'):
                        args = args | dict(target=state['targets'][args['target']])
                    result = await body.call('manipulator', 'capture' if op == 'look' else op, args, 10)
                    if result.get('image'):
                        look_args = dict(image=result['image'], question=args['question'] if op == 'look' else decision['check'])
                        if op == 'look' and 'region' in args:
                            look_args['region'] = args['region']
                        look = await body.call('retina', 'inspect', look_args, CFG['look_seconds']+1)
                        look['verified'] = (op in GUI and look.get('verdict') == 'supported' and
                             len(look.get('observed', '').strip()) > 12 and not look.get('contradiction') and 'error' not in result)
                        result['look'] = look
                        if look.get('box'):
                            target_id = token()
                            state['targets'][target_id] = dict(box=look['box'], at=look['at'])
                            result['target'] = target_id
                        state['targets'] = {k:v for k,v in state['targets'].items() if time.time()-v['at'] < CFG['target_seconds']}
                else:
                    raise ValueError(op)
            except Exception as error:
                result = dict(error=f'{type(error).__name__}: {error}', verified=False, previous=result)
                log('turn_failed', **result)
                # Inference cannot narrate its own failure, but the owner must hear it.
                if not decision:
                    result['delivery'] = await body.call('phonation', 'speak', dict(text='My thinking turn failed. I remain here; the last action is unconfirmed.'), 40)
                    active = False
            problem = result.get('error') or result.get('reason') if result.get('refused') or result.get('error') else None
            if problem and decision.get('do') not in ('say', 'done', 'rest'):
                result['delivery'] = await body.call('phonation', 'speak', dict(text=f"The {decision.get('do', 'thinking')} attempt could not complete: {problem}"), 40)
                reported = bool(result['delivery'].get('delivered'))
            entry = dict(decision=decision, result=result)
            state['record'].append(entry)
            last = result
            log('outcome', **entry)
            pending = state_path.with_suffix('.pending')
            pending.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
            pending.replace(state_path)
    finally:
        body.close()
        lock.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
