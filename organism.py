import asyncio, ctypes, json, msvcrt, os, sys, threading, time
from pathlib import Path
from fabric import CFG, ROOT, RUN, Job, execute, log, publish_owner, request, token, validate, write, object_in, box_valid

DEVICES = ['manipulator', 'retina', 'phonation', 'relay', 'cochlea']
GUI = {'launch', 'browse', 'focus', 'key', 'type', 'click', 'draw'}

class Body:
    def __init__(self):
        self.jobs, self.logs, self.failures = {}, [], {}
    def spawn(self, role, command=None):
        stream = (RUN/f'{role}.log').open('ab')
        self.logs.append(stream)
        try:
            self.jobs[role] = Job(command or [sys.executable, '-B', str(ROOT/f'{role}.py')],
                                  cwd=ROOT, stdout=stream, stderr=stream)
            log('device_started', role=role, pid=self.jobs[role].process.pid)
        except Exception as error:
            self.failures[role] = str(error)
            log('device_spawn_failed', role=role, error=str(error))
    def stop(self, role):
        job = self.jobs.get(role)
        if job:
            try:
                job.close()
                job.process.wait(timeout=3)
                log('device_stopped', role=role, code=job.process.returncode)
            except Exception as error:
                log('cleanup_failed', role=role, error=str(error))
            finally:
                if role == 'manipulator':
                    ctypes.windll.user32.mouse_event(4, 0, 0, 0, 0)
                    for key in (0x10, 0x11, 0x12, 0x5B):
                        ctypes.windll.user32.keybd_event(key, 0, 2, 0)
    async def call(self, role, op, args, seconds):
        job = self.jobs.get(role)
        if not job or job.process.poll() is not None:
            return dict(error=f'{role} is unavailable; operation not attempted', verified=False, outcome='not_attempted')
        try:
            result = await request(role, op, args, seconds)
        except (OSError, ValueError, TimeoutError) as error:
            result = dict(error=f'{role} receipt unavailable: {type(error).__name__}', verified=False, outcome='unknown')
        if result.get('outcome') == 'unknown' or result.get('error') and 'deadline' in result['error']:
            self.stop(role)
            if role == 'retina':
                self.stop('vision-server')
        return result
    def close(self):
        for role in self.jobs:
            self.stop(role)
        for stream in self.logs:
            stream.close()

def infer(prompt, image=None):
    folder = Path(os.environ['LOCALAPPDATA'])/'cursor-agent'/'versions'/CFG['cursor_version']
    if image:
        image = Path(image).resolve()
        if not image.is_relative_to(ROOT) or image.read_bytes()[:8] != b'\x89PNG\r\n\x1a\n':
            raise ValueError('Pixel inspection requires a captured PNG inside the workspace')
        prompt += '\nRequired pixel inspection: use the native Read tool on '+str(image)+'. Read this image before answering; do not use shell or other tools. Actual pixels override any supplied visual description.'
    prompt_file = RUN/(token()+'.prompt')
    prompt_file.write_text(prompt, encoding='utf-8')
    bootstrap = "process.argv.push(require('node:fs').readFileSync(process.argv.pop(),'utf8'));require(process.argv[1]);"
    raw = execute([str(folder/'node.exe'), '-e', bootstrap, str(folder/'index.js'), '--mode', 'ask', '--trust',
                   '-p', '--model', CFG['mind_model'], '--workspace', str(ROOT), '--output-format', 'stream-json',
                   str(prompt_file)], CFG['mind_seconds'], cwd=ROOT).decode('utf-8', errors='replace')
    transcript = prompt_file.with_suffix('.stream.jsonl'); transcript.write_text(raw, encoding='utf-8')
    texts, inspected = [], False
    for line in raw.splitlines():
        event = json.loads(line)
        if event.get('type') == 'assistant':
            texts.append(''.join(part['text'] for part in event['message']['content'] if part.get('type') == 'text'))
        if image and event.get('type') == 'tool_call' and event.get('subtype') == 'completed':
            read = event.get('tool_call', {}).get('readToolCall', {})
            path = Path(read.get('args', {}).get('path', ''))
            if not path.is_absolute():
                path = ROOT/path
            receipt = read.get('result', {}).get('success', {})
            if (path.resolve() == image and receipt.get('dataBlobId') and
                    receipt.get('fileSize') == image.stat().st_size and not receipt.get('exceededLimit')):
                inspected = True
                log('native_image_read', image=str(image), receipt=event, transcript=str(transcript))
    if not texts or image and not inspected:
        raise ValueError('No final answer or no successful native image Read receipt')
    log('inference', prompt=str(prompt_file), transcript=str(transcript), raw=texts[-1], pixels_inspected=inspected)
    return object_in(texts[-1])

def judge(look, question, locate=False):
    contract = ('Return {"observed":"literal target", "box":[xmin,ymin,xmax,ymax] or null}. '
                'Locate only the requested target, excluding surrounding controls. Coordinates are X,Y, '
                'normalized 0..1000 to the FULL supplied desktop image; Y increases downward. '
                'An unavailable target requires box=null.' if locate else
                'Return {"observed":"literal visible evidence", "contradiction":"visible conflict or empty string", '
                '"verdict":"supported|refuted|uncertain"}. Unreadable evidence is uncertain. Do not infer hidden effects.')
    answer = infer(question+'\n'+contract, look['image'] if locate else look['submitted'])
    if locate:
        if (set(answer) != {'observed', 'box'} or not isinstance(answer['observed'], str) or
                answer['box'] is not None and not box_valid(answer['box'])):
            raise ValueError('Invalid pixel location')
        answer.update(verdict='supported' if answer['box'] else 'uncertain', contradiction='')
    elif (set(answer) != {'observed', 'contradiction', 'verdict'} or
          not all(isinstance(answer[k], str) for k in answer) or answer['verdict'] not in ('supported', 'refuted', 'uncertain')):
        raise ValueError('Invalid pixel judgment')
    return look | answer | dict(local_observation={k:look.get(k) for k in answer}, pixels_inspected=True)

def compact(entry):
    decision, result = entry.get('decision', {}), entry.get('result', {})
    brief = {k:v for k,v in result.items() if k in
             {'outcome', 'error', 'reason', 'refused', 'connected', 'authorized', 'image', 'target', 'located', 'desktop',
              'window', 'windows', 'verified', 'delivered', 'hearing_verified', 'message_id', 'note', 'local_spoken', 'resolved', 'history', 'incomplete'}}
    if result.get('look'):
        brief['look'] = {k:v for k,v in result['look'].items() if k in
                        {'verdict', 'observed', 'contradiction', 'image', 'submitted', 'question', 'mode',
                         'at', 'seconds', 'pixels_delivered', 'pixels_inspected', 'action_id', 'verified',
                         'region', 'box', 'source_size', 'submitted_size'}}
    if result.get('observation'):
        brief['observation'] = result['observation']
    if result.get('delivery'):
        brief['delivery'] = {k:v for k,v in result['delivery'].items() if k in
                             {'delivered', 'message_id', 'hearing_verified', 'note', 'local_spoken', 'outcome'}}
    args = {k:v for k,v in decision.get('args', {}).items()
            if k not in {'strokes', 'points'}}
    return dict(id=entry['id'], decision={k:v for k,v in decision.items() if k != 'story'} | dict(args=args),
                result=brief, artifact=entry.get('artifact'))

def save(state, path):
    while state['record'] and len(json.dumps(state['record'], ensure_ascii=False)) > 8000:
        state['record'].pop(0)
    while len(state['unresolved']) > 16:
        state['unresolved'].pop(next(iter(state['unresolved'])))
    write(path, state)

def remember(state, entry):
    path = ROOT/'private'/'outcomes'/(entry['id']+'.json')
    entry['artifact'] = str(path)
    result = entry['result']
    entry['unresolved'] = bool(result.get('error') or result.get('refused') or result.get('unconfirmed') or result.get('outcome') == 'unknown')
    write(path, entry)
    log('outcome', **entry)
    brief = compact(entry)
    state['record'].append(brief)
    state['last'] = brief
    if entry['unresolved']:
        state['unresolved_total'] += 1
        state['unresolved'][entry['id']] = dict(op=entry['decision'].get('do'), reason=result.get('error') or result.get('reason') or 'Outcome unknown',
                                               artifact=str(path), image=result.get('image'))
        while len(state['unresolved']) > 16:
            state['unresolved'].pop(next(iter(state['unresolved'])))
    return brief

async def speak(body, state, text, ident):
    receipts = ROOT/'private'/'voice'
    path = receipts/(ident+'.json')
    if path.exists():
        return json.loads(path.read_text('utf-8'))
    # Durable pre-upload checkpoint: an interrupted delivery must not be resent.
    result = dict(text=text, outcome='unknown', delivered=False, hearing_verified=False)
    write(path, result)
    result = await body.call('phonation', 'speak', dict(text=text, narration_id=ident), 55)
    if not result.get('delivered') and not result.get('local_spoken'):
        print('VOICE FAILURE:', text, result, flush=True)
        script = "Add-Type -AssemblyName System.Speech; $v=New-Object System.Speech.Synthesis.SpeechSynthesizer; $v.Speak($env:TRIDENT_TEXT); $v.Dispose()"
        try:
            await asyncio.to_thread(execute, ['powershell.exe', '-NoProfile', '-Command', script], 15,
                                    env=os.environ | {'TRIDENT_TEXT':'Voice delivery is unavailable. '+text})
            result['local_spoken'] = True
        except Exception as error:
            result['local_error'] = type(error).__name__
    result = dict(text=text, hearing_verified=False) | result
    write(path, result)
    log('narration', id=ident, result=result)
    return result

def supported(look):
    return (look.get('pixels_delivered') is True and look.get('pixels_inspected') is True and look.get('mode') == 'inspect' and
            look.get('verdict') == 'supported' and not look.get('contradiction') and
            len(look.get('observed', '').strip()) > 12 and not look.get('error'))

def owner_waiting(private):
    for path in (private/'inbox').glob('*.json'):
        if json.loads(path.read_text('utf-8')).get('source') in ('local-owner', 'telegram-owner'):
            return True
    return False

async def main():
    private = ROOT/'private'
    private.mkdir(exist_ok=True)
    lock = (private/'living.lock').open('a+b')
    if lock.tell() == 0:
        lock.write(b'0'); lock.flush()
    lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    run = private/token(); run.mkdir()
    os.environ['TRIDENT_RUN'] = str(run)
    import fabric
    global RUN
    RUN = fabric.RUN = run
    for role in DEVICES:
        (RUN/role).mkdir()
    outputs = ROOT/'runtime'/'output'; outputs.mkdir(parents=True, exist_ok=True)
    state_path = private/'continuity.json'
    state = json.loads(state_path.read_text('utf-8')) if state_path.exists() else dict(story='', record=[], goal='')
    state.setdefault('unresolved', {})
    state.setdefault('unresolved_total', len(state['unresolved']))
    state.setdefault('blocked', {})
    state.update(targets={}, story='')
    owner, last = state.get('owner', {}), state.get('last', {})
    active, reported = state.get('active', False), state.get('reported', False)
    if state.get('pending'):
        pending = state.pop('pending')
        decision = pending.get('decision', pending)
        result = dict(error='Process stopped during action; inspect before repeating',
                      outcome='unknown' if pending.get('phase', 'attempted') == 'attempted' else 'not_attempted', verified=False)
        last = remember(state, dict(id=pending.get('id', token()), decision=decision, result=result))
        active, reported = True, False
    state.update(last=last, active=active, owner=owner, reported=reported, process_state='running', run=str(run))
    save(state, state_path)
    body = Body()
    def console():
        for text in sys.stdin:
            if text.strip():
                publish_owner(text.strip(), 'local-owner')
    threading.Thread(target=console, daemon=True).start()
    try:
        models = Path(os.environ['LOCALAPPDATA'])/'Trident'/'models'
        body.spawn('vision-server', [str(ROOT/'runtime'/'llama'/'llama-server.exe'),
            '-m', str(models/'LFM2.5-VL-3B-Q4_K_M.gguf'), '--mmproj', str(models/'mmproj-LFM2.5-VL-3B-Q8_0.gguf'),
            '--device', 'CUDA0', '--mmproj-device', 'CUDA0', '-ngl', '999', '--fit', 'off',
            '--ctx-size', '4096', '--parallel', '1', '--reasoning', 'off',
            '--host', '127.0.0.1', '--port', '8188', '--alias', 'retina'])
        for role in DEVICES:
            body.spawn(role)
        print(f'Trident alive. Run: {RUN}\nType or send owner Telegram text/voice. Ctrl+C stops.', flush=True)
        if len(sys.argv) > 1:
            publish_owner(' '.join(sys.argv[1:]), 'local-owner')
        policy = (ROOT/'instinct.txt').read_text('utf-8')
        arrival = await speak(body, state, 'Trident is here.', 'arrival-'+run.name)
        state['record'].append(dict(arrival=arrival)); save(state, state_path)
        announced = set()
        while not (RUN/'stop').exists():
            failures = body.failures | {role:f'exited with {job.process.returncode}' for role,job in body.jobs.items() if job.process.poll() is not None}
            for role, detail in failures.items():
                if role not in announced:
                    announced.add(role); body.stop(role)
                    ident = token()
                    failure = dict(error=f'{role}: {detail}', outcome='failed', verified=False)
                    failure['delivery'] = await speak(body, state, f'My {role} device stopped. I remain here. {detail}.', 'failure-'+ident)
                    remember(state, dict(id=ident, decision=dict(do='device_failure'), result=failure))
                    save(state, state_path)
            messages = sorted((private/'inbox').glob('*.json'))
            if messages:
                path = messages[0]; message = json.loads(path.read_text('utf-8'))
                if message.get('source') in ('local-owner', 'telegram-owner'):
                    owner = message; write(private/'owner.json', owner)
                    state['goal'], active, reported = owner['text'], True, False
                    state['record'].append(dict(owner=owner))
                else:
                    result = dict(observation=message, outcome='failed', error=message['text'])
                    result['delivery'] = await speak(body, state, message['text'], 'observation-'+message['id'])
                    remember(state, dict(id=message['id'], decision=dict(do='observation'), result=result))
                path.unlink()
                state.update(owner=owner, active=active, reported=reported); save(state, state_path)
            if not active:
                await asyncio.sleep(.1); continue
            failures_view = list(state['unresolved'].items())[-8:]
            context = dict(goal=state['goal'], story_summary=state['story'], record=list(state['record']), previous=last,
                           unresolved=failures_view, unresolved_total=state['unresolved_total'],
                           outcome_archive=str(private/'outcomes'), capabilities=CFG['capabilities'],
                           targets=state['targets'], output_directory=str(outputs), reported=reported, owner=owner)
            while len(json.dumps(context, ensure_ascii=False)) > CFG['context_chars'] and context['record']:
                context['record'].pop(0)
            decision, result, ident, attempted, signature = {}, {}, token(), False, ''
            try:
                if len(json.dumps(context, ensure_ascii=False)) > CFG['context_chars']:
                    raise ValueError('Bounded context is full; consult archived outcomes')
                candidate = await asyncio.to_thread(infer, policy+'\n'+json.dumps(context, ensure_ascii=False),
                                                   last.get('result', {}).get('image'))
                decision = validate(candidate, last)
                op, args = decision['do'], decision['args']
                if (RUN/'stop').exists():
                    break
                if owner_waiting(private):
                    log('superseded', id=ident, decision=decision); continue
                signature = json.dumps(dict(do=op, args=args), sort_keys=True)
                blocked = state['blocked'].get(signature)
                if blocked and blocked['owner'] == owner.get('id'):
                    log('repeat_prevented', id=ident, previous=blocked['id'], decision=decision)
                    state.update(active=False, reported=True); save(state, state_path)
                    active = False
                    continue  # The exact failure was already delivered; do not retry or narrate it again.
                state['story'] = decision['story']  # Summary only; canonical outcomes remain outside it.
                log('prediction', id=ident, decision=decision)
                state['pending'] = dict(id=ident, decision=decision, phase='prepared'); save(state, state_path)
                if op in ('say', 'done'):
                    reference = args['outcome']
                    if not reference.isalnum() or len(reference) != 32:
                        raise ValueError('Outcome must be a current owner or action UUID')
                    if reference != owner.get('id') and not (private/'outcomes'/(reference+'.json')).exists():
                        raise ValueError('Narration must reference the current owner or a recorded outcome')
                    reference_record = owner if reference == owner.get('id') else compact(json.loads((private/'outcomes'/(reference+'.json')).read_text('utf-8')))
                    audit = await asyncio.to_thread(infer,
                        'Audit the proposed sentence against actual outcomes. Check speaker attribution. A call refusal, '
                        'connection result, owner receipt or voice delivery is nonvisual. For a completed desktop effect, '
                        'require its exact action ID, supporting screenshot and pointed postcondition. A later general look '
                        'cannot overrule a refuted action. Return {"allowed":boolean,"gui_claim":boolean,"reason":"concise text"}.\n'
                        +json.dumps(dict(sentence=args['text'], reference=reference_record, evidence=args.get('evidence'),
                                        unresolved=failures_view, last=last, record=state['record']), ensure_ascii=False))
                    if audit.get('allowed') is not True or type(audit.get('gui_claim')) is not bool:
                        raise ValueError('Spoken claim lacks support in the actual outcome record')
                    if audit['gui_claim']:
                        proof_id = args.get('evidence', '')
                        if not proof_id.isalnum() or len(proof_id) != 32:
                            raise ValueError('A desktop claim needs an exact action evidence ID')
                        proof_entry = json.loads((private/'outcomes'/(proof_id+'.json')).read_text('utf-8'))
                        proof = proof_entry['result'].get('look', {})
                        if (proof_entry['decision']['do'] not in GUI or not proof.get('verified') or
                                proof.get('action_id') != proof_id or proof_entry.get('unresolved') or
                                time.time()-proof.get('at', 0) > 120):
                            raise ValueError('No fresh specific action proof supports this claim')
                        judgment = await asyncio.to_thread(judge, proof, 'Do these pixels support this exact completed effect: '+args['text']+'?')
                        log('claim_judgment', narration_id=ident, evidence_id=proof_id, result=judgment)
                        if not supported(judgment):
                            proof_entry['result']['look']['verified'] = False
                            proof_entry['result']['claim_judgment'] = judgment
                            if not proof_entry.get('unresolved'):
                                state['unresolved_total'] += 1
                            proof_entry['unresolved'] = True
                            write(private/'outcomes'/(proof_id+'.json'), proof_entry)
                            state['unresolved'][proof_id] = dict(op=proof_entry['decision']['do'],
                                reason='Independent pixel judgment did not support the completed claim',
                                artifact=str(private/'outcomes'/(proof_id+'.json')), image=proof['image'])
                            raise ValueError('Fresh pixels do not establish the proposed completed effect')
                    if (RUN/'stop').exists():
                        state.pop('pending', None); break
                    if owner_waiting(private):
                        log('superseded', id=ident, decision=decision)
                        state.pop('pending', None); save(state, state_path); continue
                    attempted = True; state['pending']['phase'] = 'attempted'; save(state, state_path)
                    result = await speak(body, state, args['text'], owner['id']+'-'+reference)
                    reported = bool(result.get('delivered') or result.get('local_spoken'))
                    if op == 'done' or not result.get('delivered'):
                        active = False
                elif op == 'rest':
                    if not reported and last.get('decision', {}).get('do') not in ('say', 'done', 'rest', None):
                        raise ValueError('Narrate the actual outcome before resting')
                    result, active = dict(resting=True, outcome='completed'), False
                elif op == 'recall':
                    query = args['query'].casefold()
                    def recall():
                        history, deadline = '', time.monotonic()+5
                        for path in private.glob('*/*.jsonl'):
                            for line in path.read_text('utf-8').splitlines():
                                if query in line.casefold():
                                    history = (history+line+'\n')[-8000:]
                                if time.monotonic() >= deadline:
                                    return dict(history=history, incomplete=True, outcome='completed')
                        return dict(history=history, incomplete=False, outcome='completed')
                    result = await asyncio.to_thread(recall)
                else:
                    if op == 'authorize':
                        permission = await asyncio.to_thread(infer,
                            'Does this CURRENT owner message explicitly request a call now? Quotes, scenarios and '
                            'historical permissions do not authorize. Return {"authorized":boolean}. '+json.dumps(owner))
                        if permission.get('authorized') is not True:
                            raise ValueError('Current explicit calling intent is absent')
                        args = args | dict(intent_confirmed=True)
                    if op in ('click', 'draw'):
                        args = args | dict(target=state['targets'][args['target']])
                    if (RUN/'stop').exists():
                        state.pop('pending', None); break
                    if owner_waiting(private):
                        log('superseded', id=ident, decision=decision)
                        state.pop('pending', None); save(state, state_path); continue
                    attempted, reported = True, False
                    state['pending']['phase'] = 'attempted'; save(state, state_path)
                    if op in ('authorize', 'dial'):
                        result = await body.call('relay', op, args | dict(owner_id=owner['id']), 15)
                    else:
                        if op in GUI:
                            state['targets'] = {}
                        result = await body.call('manipulator', 'capture' if op == 'look' else op, args | dict(action_id=ident), 10)
                        if result.get('image'):
                            region = args.get('region') if op == 'look' else None if op in ('launch', 'browse') else result['window']['normalized_box']
                            look_args = dict(image=result['image'], question=args['question'] if op == 'look' else decision['check'],
                                             action_id=ident, mode=args.get('mode', 'inspect') if op == 'look' else 'inspect')
                            if region:
                                look_args['region'] = region
                            look = await body.call('retina', 'inspect', look_args, CFG['look_seconds'])
                            locating = op == 'look' and args.get('mode') == 'locate'
                            if (op in GUI or locating) and look.get('submitted') and not look.get('error'):
                                look = await asyncio.to_thread(judge, look, args['question'] if locating else decision['check'], locating)
                            result.update(look=look, proof_region=region)
                            if op in GUI:
                                look['verified'] = result['verified'] = supported(look) and not result.get('error')
                            else:
                                result.pop('verified', None); look.pop('verified', None)
                            if op in GUI and not result['verified'] or look.get('error'):
                                result.update(unconfirmed=True, reason='The pointed visual check is '+look.get('verdict', 'unavailable')+'. '+look.get('observed', look.get('error', 'No readable visual evidence')))
                            if (op == 'look' and args.get('mode') == 'locate' and look.get('verdict') == 'supported'
                                    and not look.get('contradiction') and look.get('box') and look.get('pixels_delivered')):
                                target_id = token()
                                state['targets'][target_id] = dict(box=look['box'], at=look['at'], desktop=result['desktop'], window=result['window'])
                                result.update(target=target_id, located=True)
                            state['targets'] = {k:v for k,v in state['targets'].items() if time.time()-v['at'] < CFG['target_seconds']}
                        if decision.get('corrects') and result.get('verified'):
                            old_id = decision['corrects']
                            if not old_id.isalnum() or len(old_id) != 32:
                                raise ValueError('Correction needs an action UUID')
                            old_path = private/'outcomes'/(old_id+'.json')
                            old = json.loads(old_path.read_text('utf-8'))
                            if not old.get('unresolved') or old['decision'].get('do') not in GUI:
                                raise ValueError('Correction does not reference an unresolved desktop action')
                            correction = await asyncio.to_thread(judge, look, old['decision']['check'])
                            result['correction_judgment'] = correction
                            if supported(correction):
                                old.update(unresolved=False, resolved_by=ident)
                                write(old_path, old)
                                state['unresolved'].pop(old_id, None)
                                state['unresolved_total'] -= 1
                                result['resolved'] = old_id
            except Exception as error:
                log('turn_failed', id=ident, error=str(error), decision=decision, previous=result)
                result = result | dict(error=f'{type(error).__name__}: {error}', verified=False,
                                       outcome='unknown' if attempted else 'not_attempted')
                if not decision:
                    active = False  # One blocker and wait; no automatic malformed-output loop.
            problem = result.get('error') or (result.get('reason') if result.get('refused') or result.get('unconfirmed') else None)
            if problem:
                if not decision:
                    detail = str(problem)
                    if detail.lstrip().startswith(('{', '[')):
                        detail = 'The reply was malformed; its full text is preserved.'
                    sentence = 'I could not obtain a valid next decision. '+detail+'. No desktop action was attempted. I remain here.'
                elif result.get('refused'):
                    sentence = 'The call could not be placed. '+result.get('reason', 'The provider refused the attempt')+'. I remain here.'
                elif result.get('unconfirmed'):
                    sentence = 'The '+decision['do']+' action has no confirmed visual proof. '+result['reason']
                else:
                    detail = str(problem)
                    if detail.lstrip().startswith(('{', '[')):
                        detail = 'The device response was unreadable; its full answer is preserved.'
                    status = {'not_attempted':'was not attempted.', 'failed':'failed.', 'unknown':'has an unknown outcome.'}.get(result.get('outcome'), 'could not be confirmed.')
                    sentence = 'The '+decision.get('do', 'thinking')+' action '+status+' '+detail+' I remain here.'
                result['delivery'] = await speak(body, state, sentence, owner.get('id', 'system')+'-'+ident)
                reported = bool(result['delivery'].get('delivered') or result['delivery'].get('local_spoken'))
                if signature and reported:
                    state['blocked'][signature] = dict(id=ident, owner=owner.get('id'))
                    while len(state['blocked']) > 16:
                        state['blocked'].pop(next(iter(state['blocked'])))
                if result.get('outcome') == 'unknown':
                    active = False
            entry = dict(id=ident, decision=decision, result=result)
            if result.get('verified'):
                state['blocked'].clear()  # A confirmed desktop change supplies a new prerequisite.
            last = remember(state, entry)
            state.pop('pending', None)
            state.update(last=last, active=active, owner=owner, reported=reported)
            save(state, state_path)
    finally:
        body.close()
        state.update(active=False, process_state='stopped')
        save(state, state_path)
        lock.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
