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
            result = await request(role, op, args, seconds)
            if result.get('outcome') == 'unknown':
                self.stop(role)
            return result
        except TimeoutError:
            self.stop(role)
            if role == 'retina' and 'vision-server' in self.processes:
                self.stop('vision-server')
            return dict(error=f'{role} timed out; outcome unknown; device stopped', verified=False)
        except (OSError, ValueError) as error:
            return dict(error=f'{role}: {error}', verified=False, outcome='unknown')
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

def save(state, path):
    while state['record'] and len(json.dumps(state['record'], ensure_ascii=False)) > CFG['context_chars']:
        state['record'].pop(0)
    write(path, state)

async def speak(body, text):
    result = await body.call('phonation', 'speak', dict(text=text), 55)
    if not result.get('delivered') and not result.get('local_spoken'):
        print('VOICE FAILURE:', text, result, flush=True)
        script = "Add-Type -AssemblyName System.Speech; $v=New-Object System.Speech.Synthesis.SpeechSynthesizer; $v.Speak($env:TRIDENT_TEXT); $v.Dispose()"
        try:
            await asyncio.to_thread(execute, ['powershell.exe', '-NoProfile', '-Command', script], 15,
                                    env=os.environ | {'TRIDENT_TEXT':'Voice delivery is unavailable. '+text})
            result['local_spoken'] = True
        except Exception as error:
            result['local_error'] = str(error)
    return dict(text=text) | result

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
    for role in DEVICES:
        (RUN/role).mkdir()
    outputs = ROOT / 'runtime' / 'output'
    outputs.mkdir(parents=True, exist_ok=True)
    state_path = private / 'continuity.json'
    state = json.loads(state_path.read_text('utf-8')) if state_path.exists() else dict(story='', record=[], goal='', targets={})
    state['targets'] = {}
    body = Body()
    owner = state.get('owner', {})
    last = state.get('last', state['record'][-1] if state['record'] else {})
    active, reported = state.get('active', False), state.get('reported', False)
    if state.get('pending'):
        last = dict(decision=state.pop('pending'), result=dict(error='Process stopped during this action; outcome unknown. Inspect before any repeat.', outcome='unknown'))
        state['record'].append(last)
        active, reported = True, False
    state.update(last=last, active=active, owner=owner, reported=reported)
    save(state, state_path)
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
        policy = (ROOT/'instinct.txt').read_text('utf-8')
        arrival = await speak(body, 'Trident is here. I have preserved the continuing record.')
        state['record'].append(dict(arrival=arrival))
        save(state, state_path)
        announced = set()
        while not (RUN/'stop').exists():
            failures = body.failures | {role:f'exited with {process.returncode}' for role,process in body.processes.items() if process.poll() is not None}
            for role, detail in failures.items():
                if role not in announced:
                    announced.add(role)
                    failure = dict(error=f'{role}: {detail}', verified=False)
                    state['record'].append(failure)
                    log('device_failure', role=role, **failure)
                    delivery = await speak(body, f'My {role} device stopped: {detail}. Its pending outcome is unconfirmed.')
                    state['record'].append(dict(delivery=delivery))
                    save(state, state_path)
            messages = sorted((private/'inbox').glob('*.json'))
            if messages:
                message_path = messages[0]
                owner = json.loads(message_path.read_text('utf-8'))
                write(private/'owner.json', owner)
                state['goal'], active, reported = owner['text'], True, False
                state['record'].append(dict(owner=owner))
                state.update(owner=owner, active=active, reported=reported)
                save(state, state_path)
                message_path.unlink()
            if not active:
                await asyncio.sleep(.1)
                continue
            context = dict(goal=state['goal'], story=state['story'], record=state['record'],
                           previous=last, capabilities=CFG['capabilities'], output_directory=str(outputs),
                           reported=reported, owner=owner)
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
                state['pending'] = decision
                save(state, state_path)
                if op in ('say', 'done'):
                    audit_prompt = ('Audit this proposed spoken sentence against the actual record. '
                        'Classify completed desktop effects only as gui_claim. Call refusals, provider connection states, '
                        'voice delivery receipts, conversation, intentions and blocker reports are NONVISUAL. '
                        'For a GUI claim name the exact latest supporting screenshot from the record; pixels will be judged separately. '
                        'Check who did what: an owner saying they received a note does not mean the assistant received an owner voice note. '
                        'Do not repeat an already delivered narration unless the owner asked for it again. '
                        'Return one JSON object: {"allowed":boolean,"gui_claim":boolean,"image":"exact path or empty","reason":"literal evidence"}.\n'
                        +json.dumps(dict(sentence=args['text'], story=state['story'], last=last, record=state['record']), ensure_ascii=False))
                    audit = await asyncio.to_thread(infer, audit_prompt)
                    if audit.get('allowed') is not True or not isinstance(audit.get('gui_claim'), bool):
                        raise ValueError('Spoken claim withheld: '+audit['reason'])
                    if audit['gui_claim']:
                        latest = next((entry for entry in reversed(state['record']) if entry.get('decision', {}).get('do') in GUI | {'look'}), {})
                        proof = latest.get('result', {}).get('look', {})
                        if not proof.get('verified') or audit['image'] != proof.get('image') or time.time()-proof['at'] > 120:
                            raise ValueError('No fresh specific GUI proof; inspect the effect again')
                        judgment = await body.call('retina', 'inspect', dict(image=audit['image'],
                            question='Do these pixels support this exact completed GUI claim: '+args['text']+'? Cite literal visible evidence and contradictions.'), CFG['look_seconds']+1)
                        log('claim_judgment', sentence=args['text'], result=judgment)
                        if judgment.get('verdict') != 'supported' or judgment.get('contradiction') or len(judgment.get('observed', '').strip()) <= 12:
                            raise ValueError('Pixels do not establish the spoken claim: '+json.dumps(judgment))
                    delivered = next((entry.get('result', {}) for entry in reversed(state['record'])
                        if entry.get('result', {}).get('delivered') and entry.get('result', {}).get('owner_id') == owner.get('id')
                        and entry['result'].get('text', '').strip().casefold() == args['text'].strip().casefold()), None)
                    result = delivered or await speak(body, args['text'])
                    result['owner_id'] = owner.get('id')
                    reported = bool(result.get('delivered'))
                    print(args['text'], result, flush=True)
                    if op == 'done' and result.get('delivered'):
                        active = False
                elif op == 'rest':
                    if not reported and last.get('decision', {}).get('do') not in ('say', 'done', 'rest', None):
                        raise ValueError('Report the current outcome aloud before resting')
                    result, active = dict(resting=True), False
                elif op == 'recall':
                    query = args['query'].casefold()
                    def recall():
                        history, deadline = '', time.monotonic()+5
                        for path in private.glob('*/*.jsonl'):
                            for line in path.read_text('utf-8').splitlines():
                                if query in line.casefold():
                                    history = (history+line+'\n')[-CFG['context_chars']:]
                                if time.monotonic() >= deadline:
                                    return dict(history=history, incomplete=True)
                        return dict(history=history, incomplete=False)
                    result = await asyncio.to_thread(recall)
                elif op in ('authorize', 'dial'):
                    reported = False
                    if op == 'authorize':
                        permission = await asyncio.to_thread(infer,
                            'Does this CURRENT owner message explicitly request an outgoing call now? '
                            'Quoted examples, history, policies and hypothetical scenarios do not authorize a call. '
                            'Return {"authorized":true or false}. Message: '+json.dumps(owner))
                        if permission.get('authorized') is not True:
                            raise ValueError('Current explicit calling intent is absent')
                        args = args | dict(intent_confirmed=True)
                    result = await body.call('relay', op, args | dict(owner_id=owner['id']), 15)
                elif op == 'look' or op in GUI:
                    reported = False
                    if op in GUI and not decision['check'].strip():
                        raise ValueError('A GUI action requires a pointed check before execution')
                    if op in ('click', 'draw'):
                        args = args | dict(target=state['targets'][args['target']])
                    if op in GUI:
                        state['targets'] = {}
                    result = await body.call('manipulator', 'capture' if op == 'look' else op, args, 10)
                    if result.get('image'):
                        look_args = dict(image=result['image'], question=args['question'] if op == 'look' else decision['check'])
                        if op == 'look' and 'region' in args:
                            look_args['region'] = args['region']
                        look = await body.call('retina', 'inspect', look_args, CFG['look_seconds']+1)
                        look['verified'] = (look.get('verdict') == 'supported' and
                             len(look.get('observed', '').strip()) > 12 and not look.get('contradiction') and 'error' not in result)
                        result['look'] = look
                        if (op in GUI and not look['verified']) or look.get('error'):
                            result.update(unconfirmed=True, reason=look.get('error') or
                                'The pointed visual check is '+look.get('verdict', 'uncertain')+': '+look.get('observed', 'No readable evidence'))
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
                    result['delivery'] = await speak(body, 'My thinking turn failed. I remain here; the last action is unconfirmed.')
                    active = False
            problem = result.get('error') or result.get('reason') if result.get('refused') or result.get('unconfirmed') or result.get('error') else None
            if problem and not result.get('delivery') and not result.get('local_spoken'):
                result['delivery'] = await speak(body, f"The {decision.get('do', 'thinking')} attempt could not complete: {str(problem)[:500]}")
                reported = bool(result['delivery'].get('delivered'))
            if decision.get('do') in ('say', 'done') and not result.get('delivered') and result.get('outcome') == 'unknown':
                active = False  # Upload may have happened; never resend blindly.
            entry = dict(decision=decision, result=result)
            state['record'].append(entry)
            last = entry
            log('outcome', **entry)
            state.pop('pending', None)
            state.update(last=last, active=active, owner=owner, reported=reported)
            save(state, state_path)
    finally:
        body.close()
        lock.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
