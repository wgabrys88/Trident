import asyncio, ctypes, json, msvcrt, os, sys, threading, time
from pathlib import Path
from urllib.parse import urlparse
from fabric import ARGUMENTS, CFG, ROOT, RUN, Job, execute, log, openrouter_key, publish_owner, request, token, validate, write, object_in

DEVICES = ['manipulator', 'retina', 'phonation', 'relay', 'cochlea', 'discern']
GUI = {'launch', 'browse', 'focus', 'key', 'type', 'click', 'draw'}

class Body:
    def __init__(self):
        self.jobs, self.logs = {}, []
    def spawn(self, role, command=None):
        stream = (RUN/f'{role}.log').open('ab'); self.logs.append(stream)
        try:
            self.jobs[role] = Job(command or [sys.executable, '-B', str(ROOT/f'{role}.py')], cwd=ROOT, stdout=stream, stderr=stream)
            log('device_started', role=role, pid=self.jobs[role].process.pid)
        except Exception as error:
            log('device_spawn_failed', role=role, error=str(error))
    def stop(self, role):
        job = self.jobs.get(role)
        if job and job.handle:
            try:
                job.close(); job.process.wait(timeout=3)
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
            return dict(error=f'{role} is unavailable', verified=False, outcome='not_attempted')
        try:
            result = await request(role, op, args, seconds)
        except (OSError, ValueError, TimeoutError) as error:
            result = dict(error=f'{role} receipt unavailable: {error}', verified=False, outcome='unknown')
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
            raise ValueError('Inspection requires a captured PNG inside the workspace')
        prompt = 'FIRST call native Read on '+str(image)+'. Inspect its pixels; text alone is insufficient.\n'+prompt
    prompt_file = RUN/(token()+'.prompt'); prompt_file.write_text(prompt, encoding='utf-8')
    transcript = prompt_file.with_suffix('.stream.jsonl')
    log('prompt', path=str(prompt_file), chars=len(prompt), image=str(image) if image else None)
    bootstrap = "process.argv.push(require('node:fs').readFileSync(process.argv.pop(),'utf8'));require(process.argv[1]);"
    raw = execute([str(folder/'node.exe'), '-e', bootstrap, str(folder/'index.js'), '--trust', '-p',
                   '--model', CFG['mind_model'], '--workspace', str(ROOT), '--output-format', 'stream-json',
                   str(prompt_file)], CFG['mind_seconds'], transcript=transcript, cwd=ROOT).decode('utf-8', errors='replace')
    texts, inspected = [], False
    for line in raw.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get('type') == 'assistant':
            texts.append(''.join(part['text'] for part in event['message']['content'] if part.get('type') == 'text'))
        if image and event.get('type') == 'tool_call' and event.get('subtype') == 'completed':
            read = event.get('tool_call', {}).get('readToolCall', {})
            path = Path(read.get('args', {}).get('path', ''))
            receipt = read.get('result', {}).get('success', {})
            if ((path if path.is_absolute() else ROOT/path).resolve() == image and receipt.get('dataBlobId')
                    and receipt.get('fileSize') == image.stat().st_size and not receipt.get('exceededLimit')):
                inspected = True
                log('native_image_read', image=str(image), receipt=event, transcript=str(transcript))
    if not texts or image and not inspected:
        raise ValueError('No final answer or no successful native image Read receipt')
    log('inference', prompt=str(prompt_file), raw=texts[-1], pixels_inspected=inspected)
    return object_in(texts[-1])

def brief(entry):
    return dict(id=entry['id'], decision={k:v for k,v in entry['decision'].items() if k != 'story'},
                result={k:v for k,v in entry['result'].items() if k != 'events'}, artifact=entry['artifact'])

def save(state):
    write(ROOT/'private'/'continuity.json', state)

def remember(state, entry):
    entry['artifact'] = str(ROOT/'private'/'outcomes'/(entry['id']+'.json'))
    entry.setdefault('owner_id', state['owner']['id'])
    result, op = entry['result'], entry['decision'].get('do')
    entry['unresolved'] = not entry.get('resolved_by') and bool(result.get('outcome') == 'unknown' or
        op in GUI and result.get('outcome') != 'not_attempted' and not result.get('verified') or
        op in ('say', 'done') and not result.get('delivered'))
    write(Path(entry['artifact']), entry); log('outcome', **entry)
    if entry['unresolved']:
        state['unresolved'][entry['id']] = dict(owner_id=entry['owner_id'], op=op,
            unknown=result.get('outcome') == 'unknown', reason=result.get('reason') or result.get('error') or
            result.get('processing_error') or 'Unconfirmed effect', artifact=entry['artifact'])
    else:
        state['unresolved'].pop(entry['id'], None)
    state['last'] = brief(entry); save(state)

def compare(state, decision, image):
    if not image:
        return
    entry = json.loads(Path(state['last']['artifact']).read_text('utf-8'))
    result, action, judgment = entry['result'], entry['decision'], decision['compare']
    supported = judgment['verdict'] == 'supported' and not judgment['contradiction'] and bool(judgment['observed'].strip())
    result['judgment'] = judgment | dict(image=image, pixels_inspected=True, action_id=entry['id'], question=action.get('check'))
    if action['do'] in GUI:
        result.update(verified=supported and not result.get('error'), unconfirmed=not supported)
    elif action['do'] == 'look' and action['args'].get('mode') == 'locate':
        box = result.get('look', {}).get('box')
        result['located'] = bool(supported and box and judgment['box'] == box)
        if result['located']:
            state['targets'][entry['id']] = dict(box=box, at=result['at'], desktop=result['desktop'], window=result['window'])
    correction = action.get('corrects')
    if correction and supported and not result.get('error'):
        prior = json.loads((ROOT/'private'/'outcomes'/(correction+'.json')).read_text('utf-8'))
        prior.update(unresolved=False, resolved_by=entry['id'])
        write(Path(prior['artifact']), prior); state['unresolved'].pop(correction, None)
    remember(state, entry)

async def speak(body, text, ident):
    path = ROOT/'private'/'voice'/(ident+'.json')
    if path.exists():
        return json.loads(path.read_text('utf-8'))
    write(path, dict(text=text, outcome='unknown', delivered=False, hearing_verified=False))
    result = dict(text=text, hearing_verified=False) | await body.call('phonation', 'speak', dict(text=text, narration_id=ident), 55)
    write(path, result); log('narration', id=ident, result=result)
    return result

async def judge(body, state, questions):
    return await body.call('discern', 'decide', dict(state=state, questions=questions), CFG['decision_seconds'])

def waiting(private):
    return any(json.loads(p.read_text('utf-8'))['source'] in ('local-owner', 'telegram-owner') for p in (private/'inbox').glob('*.json'))

async def main():
    os.environ['HF_HOME'] = str(ROOT/'runtime'/'hf-cache')
    os.environ['TEMP'] = os.environ['TMP'] = str(ROOT/'runtime'/'temp')
    os.environ['OPENROUTER_API_KEY'] = openrouter_key()
    private = ROOT/'private'; private.mkdir(exist_ok=True)
    lock = (private/'living.lock').open('a+b')
    if lock.tell() == 0:
        lock.write(b'0'); lock.flush()
    lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    run = private/token(); run.mkdir(); os.environ['TRIDENT_RUN'] = str(run)
    import fabric
    global RUN
    RUN = fabric.RUN = run
    for role in DEVICES:
        (RUN/role).mkdir()
    outputs = ROOT/'runtime'/'output'; outputs.mkdir(parents=True, exist_ok=True)
    state_path = private/'continuity.json'
    state = dict(story='', updates=[], goal='', unresolved={}, last={}, owner=dict(id=token(), text='', source='no-owner-goal', at=time.time()))
    if state_path.exists():
        state.update(json.loads(state_path.read_text('utf-8')))
    state.update(targets={}, active=True, process_state='running', run=str(run))
    if state.get('pending'):
        pending = state.pop('pending')
        remember(state, dict(id=pending['id'], decision=pending['decision'], result=dict(
            error='Process stopped during action; inspect before repeating', outcome='unknown' if pending['phase']=='attempted' else 'not_attempted')))
    save(state); body = Body()
    def console():
        for text in sys.stdin:
            if text.strip():
                publish_owner(text.strip(), 'local-owner')
    threading.Thread(target=console, daemon=True).start()
    try:
        models = ROOT/'runtime'/'models'
        body.spawn('vision-server', [str(ROOT/'runtime'/'llama'/'llama-server.exe'), '-m', str(models/CFG['vision_model']),
            '--mmproj', str(models/CFG['vision_projector']), '--device', 'CUDA0', '--mmproj-device', 'CUDA0', '-ngl', '999',
            '--fit', 'off', '--ctx-size', str(CFG['vision_context']), '--parallel', '1', '--reasoning', 'off',
            '--host', '127.0.0.1', '--port', str(urlparse(CFG['vision_url']).port), '--alias', CFG['vision_alias']])
        for role in DEVICES:
            body.spawn(role)
        print(f'Trident alive. Run: {RUN}\nType or send owner Telegram text/voice. Ctrl+C stops.', flush=True)
        if len(sys.argv)>1:
            publish_owner(' '.join(sys.argv[1:]), 'local-owner')
        state['updates'].append(dict(arrival=await speak(body, 'Trident is here.', 'arrival-'+run.name)))
        policy = (ROOT/'instinct.txt').read_text('utf-8'); announced, wake = set(), 0
        while not (RUN/'stop').exists():
            for role, job in body.jobs.items():
                if job.process.poll() is not None and role not in announced:
                    announced.add(role); body.stop(role)
                    state['updates'].append(dict(device=role, error='Device stopped', code=job.process.returncode))
            for path in sorted((private/'inbox').glob('*.json')):
                message = json.loads(path.read_text('utf-8')); path.unlink(); state['updates'].append(dict(message=message))
                if message['source'] in ('local-owner','telegram-owner'):
                    state.update(owner=message, goal=message['text']); write(private/'owner.json', message)
                wake = 0
            if time.time()<wake:
                await asyncio.sleep(.1); continue
            state['targets'] = {k:v for k,v in state['targets'].items() if time.time()-v['at']<CFG['target_seconds']}
            context = dict(goal=state['goal'], owner=state['owner'], story_summary=state['story'], updates=state['updates'], previous=state['last'],
                targets=state['targets'], unresolved={k:v for k,v in state['unresolved'].items() if v['unknown'] or
                    v['owner_id']==state['owner']['id'] or k in state['story']}, outcome_archive=str(private/'outcomes'),
                output_directory=str(outputs), capabilities={op:dict(required_args=sorted(ARGUMENTS[op][0]),
                    optional_args=sorted(ARGUMENTS[op][1]), description=text) for op,text in CFG['capabilities'].items()})
            decision, result, ident, attempted = {}, {}, token(), False
            try:
                image = state['last'].get('result',{}).get('image')
                if image and Path(image).parent != RUN:
                    image = None
                decision = validate(await asyncio.to_thread(infer, policy+'\n'+json.dumps(context,ensure_ascii=False), image))
                compare(state, decision, image)
                if (RUN/'stop').exists():
                    break
                if waiting(private):
                    log('superseded', id=ident, decision=decision); continue
                op, args = decision['do'], decision['args']
                if op in GUI and any(v['unknown'] and v['op'] in GUI for v in state['unresolved'].values()):
                    raise ValueError('Inspect unresolved unknown desktop effects before further input')
                if op in ('authorize','dial') and any(v['unknown'] and v['op']=='dial' and v['owner_id']==state['owner']['id'] for v in state['unresolved'].values()):
                    raise ValueError('The current call outcome is unknown; a new owner request is required')
                if decision.get('corrects'):
                    prior = json.loads((private/'outcomes'/(decision['corrects']+'.json')).read_text('utf-8'))
                    if (not prior.get('unresolved') or prior['decision']['do'] not in GUI or prior['decision']['check']!=decision['check']
                            or op=='look' and (prior['result'].get('outcome')!='unknown' or args['question']!=decision['check'])):
                        raise ValueError('Correction must reuse the exact failed check; inspection alone resolves only unknown effects')
                state['story'] = decision['story']; state['updates'] = []
                log('prediction', id=ident, decision=decision, summary_chars=len(state['story']))
                state['pending'] = dict(id=ident,decision=decision,phase='prepared'); save(state)
                if op in ('click','draw'):
                    target = state['targets'].get(args['target'])
                    if not target:
                        raise ValueError('That target is unavailable; locate it again')
                    args = args | dict(target=target)
                if op in ('say','done'):
                    reference = args['outcome']
                    record = state['owner'] if reference==state['owner']['id'] else json.loads((private/'outcomes'/(reference+'.json')).read_text('utf-8'))
                    audit = await judge(body, dict(sentence=args['text'], reference=record), dict(speech=dict(type='choice',
                        instructions='What does this sentence claim? Use only the attributed record, separating intention from actual outcome.',
                        criteria=dict(effect='A supported completed desktop effect requiring fresh pixels.', status='Supported conversation, failure, call or delivery status.',
                                      unsupported='Unsupported or contradicted fact or speaker attribution.'))))
                    speech = audit.get('answers',{}).get('speech',{}).get('choice')
                    if speech not in ('effect','status'):
                        raise ValueError('The spoken claim lacks support in its record')
                    if speech=='effect' or args.get('evidence'):
                        proof = json.loads((private/'outcomes'/(args.get('evidence','missing')+'.json')).read_text('utf-8'))
                        if proof['decision']['do'] not in GUI or not proof['result'].get('verified') or time.time()-proof['result'].get('at',0)>120:
                            raise ValueError('A completed effect requires fresh specific pixel proof')
                    if (RUN/'stop').exists() or waiting(private):
                        state.pop('pending',None); continue
                    state['pending']['phase']='attempted'; save(state); attempted=True
                    result=await speak(body,args['text'],state['owner']['id']+'-'+reference)
                    if op=='done' and result.get('delivered'):
                        state['goal']=''
                elif op=='rest':
                    result=dict(outcome='completed',resting=True); wake=time.time()+60
                elif op=='recall':
                    paths=[]; deadline=time.monotonic()+5
                    for path in (private/'outcomes').glob('*.json'):
                        if args['query'].casefold() in path.read_text('utf-8').casefold():
                            paths.append(path)
                        if time.monotonic()>deadline:
                            break
                    result=dict(outcome='completed', artifacts=[str(p) for p in paths], incomplete=time.monotonic()>deadline,
                        history=[json.loads(p.read_text('utf-8')) for p in paths if p.stem==args['query']])
                else:
                    if op=='authorize':
                        permission=await judge(body, state['owner'], dict(intent=dict(type='choice',
                            instructions='Does the owner directly request an outgoing call now? Quoted or historical intent is insufficient.',
                            criteria=dict(explicit='Direct current request to call.', absent='No current request.', ambiguous='Unclear current intent.'))))
                        if permission.get('answers',{}).get('intent',{}).get('choice')!='explicit':
                            raise ValueError('Explicit current calling intent is absent')
                        args=args|dict(intent_confirmed=True)
                    if (RUN/'stop').exists() or waiting(private):
                        state.pop('pending',None); continue
                    state['pending']['phase']='attempted'; save(state); attempted=True
                    result=await body.call('relay' if op in ('authorize','dial') else 'manipulator', 'capture' if op=='look' else op,
                        args|dict(owner_id=state['owner']['id'],action_id=ident),15)
                    if op in GUI:
                        state['targets'].clear()
                    if result.get('image'):
                        region=args.get('region') if op=='look' else None if op in ('launch','browse') else result['window']['normalized_box']
                        look_args=dict(image=result['image'],question=args['question'] if op=='look' else decision['check'],
                            action_id=ident,mode=args.get('mode','inspect') if op=='look' else 'inspect')
                        if region:
                            look_args['region']=region
                        result['look']=await body.call('retina','inspect',look_args,CFG['look_seconds'])
                result['decisions']=await judge(body, dict(goal=state['goal'], prediction=decision['expect'],
                    result={k:v for k,v in result.items() if k!='events'}, previous_comparison=decision['compare']), decision['questions'])
            except Exception as error:
                result['processing_error' if 'outcome' in result else 'error']=f'{type(error).__name__}: {error}'
                result.setdefault('outcome','unknown' if attempted else 'not_attempted'); log('turn_failed',id=ident,error=str(error),decision=decision)
                if not decision:
                    wake=time.time()+60
            remember(state,dict(id=ident,decision=decision,result=result)); state.pop('pending',None); save(state)
    finally:
        body.close(); state.update(active=False,process_state='stopped'); save(state); lock.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
