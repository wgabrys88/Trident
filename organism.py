import asyncio, ctypes, json, msvcrt, os, sys, threading, time
from pathlib import Path
from fabric import CFG, ROOT, RUN, Job, execute, log, publish_owner, request, token, validate, write, object_in

DEVICES = ['manipulator', 'retina', 'phonation', 'relay', 'cochlea', 'discern']
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
            result = dict(error=f'{role} receipt unavailable: {type(error).__name__}: {error}', verified=False, outcome='unknown')
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
    raw = execute([str(folder/'node.exe'), '-e', bootstrap, str(folder/'index.js'), '--trust',
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


def compact(entry):
    result = {k:v for k,v in entry['result'].items() if k not in {'events', 'look'}}
    if 'look' in entry['result']:
        result['look'] = {k:v for k,v in entry['result']['look'].items() if k not in {'raw', 'local_observation'}}
    return dict(id=entry['id'], decision={k:v for k,v in entry['decision'].items() if k != 'story'},
                result=result, artifact=entry['artifact'])

def save(state):
    while state['record'] and len(json.dumps(state['record'], ensure_ascii=False)) > 8000:
        state['record'].pop(0)
    while len(state['unresolved']) > 16:
        state['unresolved'].pop(next(iter(state['unresolved'])))
    write(ROOT/'private'/'continuity.json', state)

def remember(state, entry):
    entry['artifact'] = str(ROOT/'private'/'outcomes'/(entry['id']+'.json'))
    result = entry['result']
    unresolved = bool(result.get('error') or result.get('processing_error') or result.get('refused') or result.get('unconfirmed') or result.get('outcome') == 'unknown')
    state['unresolved_total'] += int(unresolved)-int(entry.get('unresolved', False))
    entry['unresolved'] = unresolved
    write(Path(entry['artifact']), entry)
    log('outcome', **entry)
    if unresolved:
        state['unresolved'][entry['id']] = dict(reason=result.get('reason') or result.get('error') or result.get('processing_error') or 'Unconfirmed effect', artifact=entry['artifact'])
    else:
        state['unresolved'].pop(entry['id'], None)
    state['last'] = compact(entry)
    state['record'] = [r for r in state['record'] if r.get('id') != entry['id']]+[state['last']]
    save(state)

def compare(state, decision, image):
    if not image:
        return
    entry = json.loads(Path(state['last']['artifact']).read_text('utf-8'))
    result, action, judgment = entry['result'], entry['decision'], decision['compare']
    supported = judgment['verdict'] == 'supported' and not judgment['contradiction'] and len(judgment['observed'].strip()) > 12
    result['judgment'] = judgment | dict(image=image, pixels_inspected=True, action_id=entry['id'], question=action.get('check'))
    if action['do'] in GUI:
        result.update(verified=supported and not result.get('error'), unconfirmed=not supported)
        if not supported:
            result['reason'] = judgment['contradiction'] or judgment['observed']
        correction = action.get('corrects')
        if correction and result['verified']:
            prior = json.loads((ROOT/'private'/'outcomes'/(correction+'.json')).read_text('utf-8'))
            prior.update(unresolved=False, resolved_by=entry['id'])
            write(Path(prior['artifact']), prior)
            state['unresolved'].pop(correction, None)
            state['unresolved_total'] -= 1
    elif action['do'] == 'look' and action['args'].get('mode') == 'locate':
        box = judgment['box']
        result['located'] = bool(box)
        if result['located']:
            state['targets'][entry['id']] = dict(box=box, at=result['at'], desktop=result['desktop'], window=result['window'])
    remember(state, entry)
    log('comparison', id=entry['id'], judgment=result['judgment'])

async def speak(body, text, ident):
    path = ROOT/'private'/'voice'/(ident+'.json')
    if path.exists():
        return json.loads(path.read_text('utf-8'))
    write(path, dict(text=text, outcome='unknown', delivered=False, hearing_verified=False))
    result = await body.call('phonation', 'speak', dict(text=text, narration_id=ident), 55)
    if not result.get('delivered') and not result.get('local_spoken'):
        script = "Add-Type -AssemblyName System.Speech; $v=New-Object System.Speech.Synthesis.SpeechSynthesizer; $v.Speak($env:TRIDENT_TEXT); $v.Dispose()"
        try:
            await asyncio.to_thread(execute, ['powershell.exe', '-NoProfile', '-Command', script], 15,
                                    env=os.environ | {'TRIDENT_TEXT':'Voice delivery is unavailable. '+text})
            result['local_spoken'] = True
        except Exception as error:
            result['local_error'] = str(error)
    result = dict(text=text, hearing_verified=False) | result
    write(path, result); log('narration', id=ident, result=result)
    return result

async def main():
    private = ROOT/'private'; private.mkdir(exist_ok=True)
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
    state = dict(story='', record=[], goal='', unresolved={}, unresolved_total=0, last={},
                 owner=dict(id=token(), text='', source='no-owner-goal', at=time.time()))
    if state_path.exists():
        state.update(json.loads(state_path.read_text('utf-8')))
    state.pop('blocked', None)
    state.update(targets={}, active=True, process_state='running', run=str(run))
    if state.get('pending'):
        pending = state.pop('pending')
        remember(state, dict(id=pending['id'], decision=pending['decision'], result=dict(
            error='Process stopped during action; inspect before repeating', outcome='unknown' if pending['phase']=='attempted' else 'not_attempted')))
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
            '-m', str(models/'LFM2.5-VL-3B-Q4_K_M.gguf'), '--mmproj', str(models/'mmproj-LFM2.5-VL-3B-Q8_0.gguf'),
            '--device', 'CUDA0', '--mmproj-device', 'CUDA0', '-ngl', '999', '--fit', 'off',
            '--ctx-size', '4096', '--parallel', '1', '--reasoning', 'off',
            '--host', '127.0.0.1', '--port', '8188', '--alias', 'retina'])
        for role in DEVICES:
            body.spawn(role)
        print(f'Trident alive. Run: {RUN}\nType or send owner Telegram text/voice. Ctrl+C stops.', flush=True)
        if len(sys.argv)>1:
            publish_owner(' '.join(sys.argv[1:]), 'local-owner')
        state['record'].append(dict(arrival=await speak(body, 'Trident is here.', 'arrival-'+run.name)))
        policy = (ROOT/'instinct.txt').read_text('utf-8')
        announced, wake = set(), 0
        while not (RUN/'stop').exists():
            for role, job in body.jobs.items():
                if job.process.poll() is not None and role not in announced:
                    announced.add(role); body.stop(role)
                    ident = token()
                    remember(state, dict(id=ident, decision=dict(do='device_failure'), result=dict(
                        error=f'{role} exited with {job.process.returncode}', outcome='failed',
                        delivery=await speak(body, f'My {role} device stopped. I remain here, but need to recover it before using it.', 'failure-'+ident))))
            for path in sorted((private/'inbox').glob('*.json')):
                message = json.loads(path.read_text('utf-8')); path.unlink()
                state['record'].append(dict(message=message))
                if message['source'] in ('local-owner','telegram-owner'):
                    state.update(owner=message, goal=message['text'])
                    write(private/'owner.json', message)
                wake = 0
            if time.time()<wake:
                await asyncio.sleep(.1); continue
            state['targets'] = {k:v for k,v in state['targets'].items() if time.time()-v['at']<CFG['target_seconds']}
            context = dict(goal=state['goal'], owner=state['owner'], story_summary=state['story'],
                           record=list(state['record']), previous=state['last'], targets=state['targets'],
                           unresolved=list(state['unresolved'].items()), unresolved_total=state['unresolved_total'],
                           outcome_archive=str(private/'outcomes'), capabilities=CFG['capabilities'], output_directory=str(outputs))
            while context['record'] and len(json.dumps(context, ensure_ascii=False))>CFG['context_chars']:
                context['record'].pop(0)
            decision, result, ident, attempted = {}, {}, token(), False
            try:
                if len(json.dumps(context, ensure_ascii=False))>CFG['context_chars']:
                    raise ValueError('Working context is full; consult the archived outcomes')
                image = state['last'].get('result',{}).get('image')
                if image and Path(image).parent != RUN:
                    image = None  # Archived screenshots do not describe this process's live desktop.
                decision = validate(await asyncio.to_thread(infer, policy+'\n'+json.dumps(context,ensure_ascii=False), image),state['last'])
                compare(state, decision, image)
                if (RUN/'stop').exists():
                    break
                if any(json.loads(p.read_text('utf-8'))['source'] in ('local-owner','telegram-owner') for p in (private/'inbox').glob('*.json')):
                    log('superseded', id=ident, decision=decision); continue
                op, args = decision['do'], decision['args']
                if state['last'].get('result',{}).get('outcome')=='unknown' and op in GUI:
                    raise ValueError('Inspect the unknown outcome before any further desktop input')
                if decision.get('corrects'):
                    prior = json.loads((private/'outcomes'/(decision['corrects']+'.json')).read_text('utf-8'))
                    if not prior.get('unresolved') or prior['decision']['do'] not in GUI or prior['decision']['check']!=decision['check']:
                        raise ValueError('Correction must reuse the unresolved action\'s exact pointed question')
                state['story'] = decision['story']
                log('prediction', id=ident, decision=decision)
                state['pending'] = dict(id=ident,decision=decision,phase='prepared'); save(state)
                if op in ('click','draw'):
                    target = state['targets'].get(args['target'])
                    if not target:
                        raise ValueError('That target is unavailable; locate it again')
                    args = args | dict(target=target)
                if op in ('say','done'):
                    reference = args['outcome']
                    record = state['owner'] if reference==state['owner']['id'] else json.loads((private/'outcomes'/(reference+'.json')).read_text('utf-8'))
                    audit=await body.call('discern','decide',dict(state=dict(sentence=args['text'],reference=record),questions=dict(
                        speech=dict(type='choice',instructions='Classify this proposed sentence against the actual attributed record.',criteria=dict(
                            effect='Claims a completed GUI effect, needing its exact fresh pixel proof.',
                            status='Conversation or honest status, failure, call outcome, or delivery report supported by the attributed record.',
                            unsupported='Unsupported fact, incorrect speaker attribution, or insufficient evidence.')))),CFG['decision_seconds'])
                    speech=audit.get('answers',{}).get('speech',{}).get('choice')
                    if speech not in ('effect','status'):
                        raise ValueError('The proposed spoken claim lacks support in the actual record')
                    if speech=='effect' or args.get('evidence'):
                        if not args.get('evidence'):
                            raise ValueError('A completed GUI claim requires its exact action evidence')
                        proof = json.loads((private/'outcomes'/(args['evidence']+'.json')).read_text('utf-8'))
                        if proof['decision']['do'] not in GUI or not proof['result'].get('verified') or time.time()-proof['result'].get('at',0)>120:
                            raise ValueError('That completed effect has no fresh specific pixel proof')
                    if (RUN/'stop').exists() or any((private/'inbox').glob('*.json')):
                        state.pop('pending',None); continue
                    attempted=True; state['pending']['phase']='attempted'; save(state)
                    result=await speak(body,args['text'],state['owner']['id']+'-'+reference)
                    if op=='done':
                        state['goal']=''
                elif op=='rest':
                    result=dict(outcome='completed',resting=True); wake=time.time()+60
                elif op=='recall':
                    matches=[]; history=[]; deadline=time.monotonic()+5
                    for path in sorted((private/'outcomes').glob('*.json')):
                        if args['query'].casefold() in path.read_text('utf-8').casefold():
                            matches.append(str(path))
                            brief=compact(json.loads(path.read_text('utf-8')))
                            if len(json.dumps(history+[brief],ensure_ascii=False))<=8000:
                                history.append(brief)
                        if len(matches)==16 or time.monotonic()>deadline:
                            break
                    result=dict(outcome='completed',history=history,artifacts=matches,archive=str(private/'outcomes'),incomplete=True)
                else:
                    if op=='authorize':
                        permission=await body.call('discern','decide',dict(state=state['owner'],questions=dict(
                            intent=dict(type='choice',instructions='Does this current owner message explicitly request an outgoing call now?',criteria=dict(
                                explicit='A current explicit request to call now.',absent='No request, a historical request or quoted scenario.',
                                ambiguous='Calling intent is unclear.')))),CFG['decision_seconds'])
                        if permission.get('answers',{}).get('intent',{}).get('choice')!='explicit':
                            raise ValueError('Explicit current calling intent is absent')
                        args=args|dict(intent_confirmed=True)
                        if (RUN/'stop').exists() or any((private/'inbox').glob('*.json')):
                            state.pop('pending',None); continue
                    attempted=True; state['pending']['phase']='attempted'; save(state)
                    result=await body.call('relay' if op in ('authorize','dial') else 'manipulator',
                        'capture' if op=='look' else op, args|dict(owner_id=state['owner']['id'],action_id=ident),15)
                    if result.get('image'):
                        region=args.get('region') if op=='look' else None if op in ('launch','browse') else result['window']['normalized_box']
                        look_args=dict(image=result['image'],question=args['question'] if op=='look' else decision['check'],
                                       action_id=ident,mode=args.get('mode','inspect') if op=='look' else 'inspect')
                        if region:
                            look_args['region']=region
                        result['look']=await body.call('retina','inspect',look_args,CFG['look_seconds'])
                        if op=='look' and args.get('mode')=='locate':
                            result['target']=ident; result['located']='awaiting mind pixel comparison'
                result['decisions']=await body.call('discern','decide',dict(questions=decision['questions'],state=dict(
                    goal=state['goal'],prediction=decision['expect'],result={k:v for k,v in result.items() if k!='events'},
                    previous_comparison=decision['compare'])),CFG['decision_seconds'])
            except Exception as error:
                result['processing_error' if 'outcome' in result else 'error']=f'{type(error).__name__}: {error}'
                result.setdefault('outcome','unknown' if attempted else 'not_attempted')
                log('turn_failed',id=ident,error=str(error),decision=decision)
                if not decision:
                    wake=time.time()+60
            remember(state,dict(id=ident,decision=decision,result=result))
            state.pop('pending',None); save(state)
    finally:
        body.close(); state.update(active=False,process_state='stopped'); save(state); lock.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
