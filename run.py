import ast, asyncio, copy, inspect, json, os, signal, subprocess, sys, threading, time
from pathlib import Path
from urllib.parse import urlparse

sys.dont_write_bytecode = True
from core import Bus, CFG, Exchange, Job, Mutex, NotAttempted, ROOT, ROLES, Session, atomic, execute, write
from perception import select_target, validate_target

class Cortex:
    def __init__(self, session):
        self.session, self.bus = session, Bus(session, 'cortex')
        state = session.load()
        self.owner_id, self.memory, self.screen = state['owner']['id'], state['memory'], state['screen']

    async def device(self, role, operation, arguments, seconds=30, ident=None):
        current = self.session.load()['owner']['id']
        if current != self.owner_id:
            self.owner_id = current
            raise NotAttempted('New owner input arrived; reconsider it before further dispatch')
        return await self.bus.request(role, operation, arguments, seconds, ident)

    async def native(self, source, prediction, targets=None):
        # Mouse actions must be attached to actual, current structured vision evidence.
        tree = ast.parse(source)
        pointing = {'click', 'doubleClick', 'tripleClick', 'rightClick', 'middleClick',
                    'mouseDown', 'dragTo', 'dragRel'}
        mouse = any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == 'gui'
                    and node.func.attr in pointing for node in ast.walk(tree)) or any(
                        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == 'stroke' for node in ast.walk(tree))
        state = self.session.load()
        prior = state['memory'].get('pending_action') or {}
        if prior.get('status') in ('completed', 'unknown', 'dispatching') and not (
                prior.get('post_action_look', {}).get('complete')):
            raise NotAttempted('Observe the previous native action before attempting another; pending_action gives its ID')
        if mouse:
            validate_target(targets, state['screen'])
            entry = state['operations'].get(targets['observation_id'], {})
            if entry.get('role') != 'retina' or entry.get('outcome') != 'completed' or not entry.get('response'):
                raise NotAttempted('Target is not backed by a successful canonical Retina operation')
            canonical = json.loads(Path(entry['response']).read_text('utf-8'))
            expected = select_target(canonical, targets['element_id'], targets['surface_kind'])
            if any(expected[k] != targets.get(k) for k in ('box', 'desktop', 'image', 'window_id', 'description')):
                raise NotAttempted('Target does not match its canonical visual evidence')
        plan = dict(prediction=prediction, issued_at=time.time(),
                    before_image=state['screen'].get('image'), target=targets, status='dispatching')
        def reserve(current):
            episode = current.get('episode', {})
            if episode.get('native_actions', 0) >= CFG.get('native_per_episode', 1):
                raise NotAttempted('Episode native-action budget reached. Remember the verified result, finish, and allow a fresh Luna invocation.')
            episode['native_actions'] = episode.get('native_actions', 0) + 1
            current['episode'] = episode
            current['memory']['pending_action'] = plan
        self.session.edit(reserve)
        result = await self.device('manipulator', 'execute', dict(source=source, prediction=prediction,
            targets=targets, execution=os.environ.get('TRIDENT_EXECUTION')), 30)
        def record(current):
            current['memory']['pending_action'] = plan | dict(status=result.get('outcome', 'unknown'),
                operation_id=result.get('operation_id'), after_image=result.get('image'),
                action_result=result.get('error', 'executed; visual outcome not yet checked'))
        self.session.edit(record)
        self.screen.clear()
        self.screen.update(self.session.load()['screen'])
        return result

    async def observe(self, question, region=None):
        screen = self.session.load()['screen']
        result = await self.device('retina', 'inspect', dict(image=screen['image'], question=question,
            desktop=screen['desktop'], window=screen['window'], region=region), CFG['look_seconds'])
        if result.get('outcome') == 'completed':
            def annotate(state):
                pending = state['memory'].get('pending_action')
                if pending and (pending.get('after_image') == result.get('image') or
                        result.get('image_at', 0) >= pending.get('issued_at', float('inf'))):
                    pending['post_action_look'] = dict(operation_id=result.get('operation_id'),
                        premise=result.get('scene', {}).get('premise') if result.get('structured') else 'unverified',
                        answer=result.get('answer'), complete=result.get('complete'))
            self.session.edit(annotate)
            self.last_look = result
        return result

    async def decide(self, state, questions):
        # Every Python tool process is fresh: get the durable latest Retina receipt.
        snapshot = self.session.load()
        recent = getattr(self, 'last_look', None)
        if not recent or not recent.get('complete'):
            ident = snapshot.get('last_retina')
            entry = snapshot['operations'].get(ident, {})
            if entry.get('response') and entry.get('outcome') == 'completed':
                recent = json.loads(Path(entry['response']).read_text('utf-8'))
                recent['operation_id'] = ident
        valid = (recent and recent.get('structured') and recent.get('complete')
                 and recent.get('image') == snapshot['screen'].get('image'))
        evidence = (dict(operation_id=recent.get('operation_id'), image=recent['image'],
            image_at=recent['image_at'], scene=recent['scene']) if valid else
            dict(available=False, reason='No successful structured look of the current screenshot'))
        wrapped = dict(claims=state, retina_evidence=evidence,
                       note='Evidence from local Retina only; text and confidence are fallible. Explicitly permit an insufficient-evidence decision.')
        return await self.device('discern', 'decide', dict(state=wrapped, questions=questions), CFG['decision_seconds'])

    def target(self, observation, element_id, expected_surface='app_content'):
        return select_target(observation, element_id, expected_surface)

    def point(self, observation, x, y):
        geometry = observation['geometry']
        left, top, right, bottom = geometry['crop']
        ox, oy = geometry['desktop'][:2]
        return [ox + round(left + x*(right-left-1)/1000), oy + round(top + y*(bottom-top-1)/1000)]

    def accept(self, observation, box):
        return dict(box=self.point(observation, *box[:2]) + self.point(observation, *box[2:]),
                    desktop=observation['geometry']['desktop'])

    async def recall(self, ident):
        state = self.session.load()
        if ident in state['executions']:
            entry = state['executions'][ident]
            return entry | {name: Path(entry[name]).read_text('utf-8', errors='replace') for name in ('request', 'output', 'errors')}
        entry = state['operations'][ident]
        receipt = entry.get('response') or entry.get('error')
        return entry | dict(request=json.loads(Path(entry['request']).read_text('utf-8')),
            result=json.loads(Path(receipt).read_text('utf-8')) if receipt else None)

    async def resolve(self, ident, evidence, interpretation):
        original, receipt = await self.recall(ident), await self.recall(evidence)
        if ident in self.session.load()['unknown']:
            if original['role'] == 'manipulator' and (receipt['role'] != 'retina' or
                    receipt['outcome'] != 'completed' or not receipt['result']['complete']
                    or receipt['result']['image_at'] < original['at']):
                raise NotAttempted('Input correction needs a successful local observation of a fresh screenshot')
            if original['role'] == 'relay' and (receipt['role'] != 'relay' or receipt['operation'] != 'status'
                    or receipt['outcome'] != 'completed' or receipt['at'] < original['at']):
                raise NotAttempted('Call correction needs a subsequent real relay status')
        def change(state):
            state['operations'][ident]['correction'] = dict(evidence=evidence, interpretation=interpretation, at=time.time())
            state['unknown'].pop(ident, None)
        self.session.edit(change)
        return await self.recall(ident)

    async def speak(self, text, identity=None):
        return await self.device('phonation', 'speak', dict(text=text), 55, identity)

    def remember(self, **changes):
        def update(state):
            if 'goal' in changes and state['owner']['id'] != self.owner_id:
                raise NotAttempted('New owner intent arrived before this goal update')
            state['memory'].update(changes)
        self.session.edit(update)
        self.memory.update(changes)

    async def rest(self, seconds=60):
        state = self.session.load()
        owner, inputs = state['owner']['id'], len(state['inputs'])
        deadline = time.monotonic() + seconds
        while not self.session.stopped() and time.monotonic() < deadline:
            state = self.session.load()
            if state['owner']['id'] != owner or len(state['inputs']) != inputs:
                break
            await asyncio.sleep(.1)

    async def program(self, path):
        state = self.session.load()
        baseline = copy.deepcopy(state['memory'])
        memory = copy.deepcopy(baseline)
        self.memory, self.owner_id, self.screen = memory, state['owner']['id'], state['screen']
        namespace = dict(__name__='__trident__', __file__=str(path), memory=memory, owner=state['owner'],
            screen=state['screen'], previous=state['operations'], ROOT=ROOT, RUN=self.session.directory,
            asyncio=asyncio, artifact=self.session.artifact)
        for name in ('device', 'native', 'observe', 'decide', 'target', 'point', 'accept', 'recall', 'resolve', 'speak', 'rest', 'remember'):
            namespace[name] = getattr(self, name)
        tools = self.session.path('tools.py')
        exec(compile(tools.read_text('utf-8'), str(tools), 'exec'), namespace)
        tree = ast.parse(path.read_text('utf-8-sig'))
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            tree.body[-1] = ast.Assign(targets=[ast.Name(id='__result__', ctx=ast.Store())], value=tree.body[-1].value)
            ast.fix_missing_locations(tree)
        try:
            value = eval(compile(tree, str(path), 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT), namespace)
            if inspect.isawaitable(value):
                await value
            if namespace.get('__result__') is not None:
                print(repr(namespace['__result__']), flush=True)
            definitions = [node for node in tree.body if isinstance(node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom))]
            names = {node.name for node in definitions if hasattr(node, 'name')}
            retained = [node for node in ast.parse(tools.read_text('utf-8')).body if getattr(node, 'name', None) not in names]
            merged = {getattr(node, 'name', ast.dump(node)): node for node in retained + definitions}
            atomic(tools, (ast.unparse(ast.Module(body=list(merged.values()), type_ignores=[])) + '\n').encode('utf-8'))
        finally:
            def merge(current):
                for key in baseline.keys() | memory.keys():
                    if key == 'goal' and current['owner']['id'] != state['owner']['id']:
                        continue
                    if key not in memory:
                        current['memory'].pop(key, None)
                    elif key not in baseline or memory[key] != baseline[key]:
                        current['memory'][key] = memory[key]
            self.session.edit(merge)

    async def python(self, source):
        exchange = Exchange(self.session, 'mcp', 'cortex')
        program = exchange.dump('request', source, 'py')
        return await execute([sys.executable, '-u', '-B', str(ROOT / 'run.py'), '--program', str(program)],
            exchange, CFG['execution_seconds'], cwd=ROOT, env=os.environ | {'TRIDENT_EXECUTION': exchange.id})

    def tools(self):
        text = dict(type='string')
        variants = [dict(type='object', properties=dict(type=dict(const=kind), instructions=text, criteria=criteria),
            required=['type', 'instructions'] + (['criteria'] if kind != 'noul' else [])) for kind, criteria in (
                ('choice', dict(type='object', additionalProperties=text)), ('score', dict(type='array', items=text)),
                ('noul', dict(type='object', properties={'true': text, 'false': text})))]
        return [dict(name='observe', description='Local vision alone sees the current screenshot. It is a small model '
            'that can be wrong: crop to the region that matters, request rich element descriptions with ownership, '
            'ask the inverse question too, and bring conflicting looks to decide. Returns structured scene, answer, finish reasons, '
            'usage, raw response path and geometry. Use target(observation, element_id, expected_surface) to bind a mouse target. All boxes are normalized 0..1000 in the submitted image, including '
            'crops; point/accept map to desktop pixels.',
            inputSchema=dict(type='object', properties=dict(question=text,
                region=dict(type='array', items=dict(type='number'), minItems=4, maxItems=4)), required=['question'])),
            dict(name='decide', description='Fast probabilistic TEXT judge over your evidence and your standing reasoning '
                'partner, not pixel viewing or device control. Lean on it for essentially EVERY meaning-based choice, '
                'not only conflicts: what matters next, how to read an ambiguous look, which region to inspect, which '
                'of several moves or plans is best, and whether a step is truly done. There is no hardcoded trigger; '
                'you judge by MEANING and decide helps you weigh it. Supply goal, local descriptions/boxes, geometry, '
                'artifact identities and receipts as state, and author the questions and competing alternatives yourself. '
                'Ask it repeatedly and in parallel, posing several framings and many candidate options at once; one '
                'legitimate answer is that a DIFFERENT approach is better, which sends you back with fresh approaches to '
                'score, keep the winner, then branch that winner into finer, more precise questions and consult again, '
                'recursing until the choice is sharp. questions is a named OBJECT of typed OBJECTS, never question strings: '
                'choice needs type, instructions and named criteria descriptions; score uses an ordered criteria array; '
                'noul judges a proposition. Returns complete answers, distributions and usage. Do not use it to see pixels, '
                'act, or re-ask an identical question; refine instead. Confidence is not proof or permission.',
                inputSchema=dict(type='object', properties=dict(state={}, questions=dict(type='object',
                    additionalProperties=dict(oneOf=variants))), required=['state', 'questions'])),
            dict(name='python', description='Execute arbitrary Python with top-level await and injected memory, owner, '
                'artifact, native, observe, decide, target, point, accept, remember, recall, resolve, speak, device and rest. '
                'No trident import. native receives Python source, prediction and target(...)-produced targets for clicks; gui wraps PyAutoGUI with verified mouse targeting '
                'INSIDE native source. Create/reuse tools, update yourself, converse or rest. Returns full stdout/stderr with '
                'their byte sizes and file paths; everything printed enters your paid context. Never read or return image bytes.',
                inputSchema=dict(type='object', properties=dict(source=text), required=['source']))]

async def mcp(session):
    cortex = Cortex(session)
    delivered, known = 0, {}
    for raw in sys.stdin.buffer:
        exchange = Exchange(session, 'cursor', 'mcp')
        exchange.dump('request', raw, 'jsonl')
        message = json.loads(raw)
        if 'id' not in message:
            continue
        method = message['method']
        if method == 'initialize':
            result = dict(protocolVersion='2025-06-18', capabilities=dict(tools={}), serverInfo=dict(name='trident', version='2'))
        elif method == 'tools/list':
            result = dict(tools=cortex.tools())
        elif method == 'tools/call':
            try:
                call = message['params']
                value = await getattr(cortex, call['name'])(**call['arguments'])
            except Exception as error:
                value = dict(outcome='failed', error=f'{type(error).__name__}: {error}')
            state = session.load()
            inputs = [json.loads(Path(entry['path']).read_text('utf-8')) for entry in state['inputs'][delivered:]]
            delivered = len(state['inputs'])
            current = dict(owner=state['owner'], memory=state['memory'], receivers=sorted(cortex.bus.receivers()))
            payload = dict(result=value, inputs=inputs) | {key: item for key, item in current.items() if known.get(key) != item}
            known = current
            result = dict(content=[dict(type='text', text=json.dumps(payload, ensure_ascii=False))],
                isError=value.get('outcome') in ('failed', 'unknown', 'not_attempted'))
        else:
            result = {}
        reply = json.dumps(dict(jsonrpc='2.0', id=message['id'], result=result), ensure_ascii=False) + '\n'
        exchange.dump('response', reply, 'jsonl')
        sys.stdout.buffer.write(reply.encode('utf-8'))
        sys.stdout.buffer.flush()

def cursor(prompt, *options):
    folder = Path(os.environ['LOCALAPPDATA']) / 'cursor-agent' / 'versions' / CFG['cursor_version']
    bootstrap = "process.argv.push(require('node:fs').readFileSync(process.argv.pop(),'utf8'));require(process.argv[1]);"
    return [str(folder / 'node.exe'), '-e', bootstrap, str(folder / 'index.js'), '--trust', '-p', *options, '--model', CFG['mind_model'],
            '--workspace', str(ROOT), '--output-format', 'stream-json', '--stream-partial-output', str(prompt)]

class Organism:
    def __init__(self, session):
        self.session, self.bus, self.jobs = session, Bus(session, 'organism'), {}
        self.capture = {}

    def spawn(self, role):
        models = ROOT / 'runtime' / 'models'
        command = [sys.executable, '-u', '-B', str(ROOT / 'devices.py'), role]
        if role == 'vision-server':
            command = [str(ROOT / 'runtime' / 'llama' / 'llama-server.exe'), '-m', str(models / CFG['vision_model']),
                '--mmproj', str(models / CFG['vision_projector']), '--device', 'CUDA0', '--mmproj-device', 'CUDA0',
                '-ngl', '999', '--fit', 'off', '--ctx-size', str(CFG['vision_context']), '--parallel', '1',
                '--reasoning', 'off', '--host', '127.0.0.1', '--port', str(urlparse(CFG['vision_url']).port),
                '--alias', CFG['vision_alias']]
        self.jobs[role] = Job(command, cwd=ROOT, stdout=sys.stdout, stderr=sys.stderr)

    def stop(self, role):
        self.jobs.pop(role).close()
        for path in self.session.directory.glob('*_' + role + '.receiver'):
            path.unlink(missing_ok=True)

    def console(self):
        for line in sys.stdin:
            if line.strip():
                self.session.publish(line.strip(), 'local-owner')

    def consume_inputs(self):
        for path in sorted(self.session.directory.glob('*_inbox_*.json')):
            message = json.loads(path.read_text('utf-8'))
            received = path.with_name(path.name.replace('_inbox_', '_input_', 1))
            path.rename(received)
            def consume(state):
                state['inputs'].append(dict(id=message['id'], path=str(received), kind=message['kind']))
                if message['kind'] == 'owner':
                    state['owner'] = message
                    state['memory']['goal'] = message['text']
            self.session.edit(consume)

    async def maintain(self):
        while not self.session.stopped():
            self.consume_inputs()
            def requested(state):
                return state['memory'].pop('restart_devices', [])
            restarts = self.session.edit(requested) if self.session.load()['memory'].get('restart_devices') else []
            for role in restarts:
                if role in ROLES:
                    if role in self.jobs:
                        self.stop(role)
                    self.spawn(role)
            for role, job in list(self.jobs.items()):
                if job.process.poll() is not None:
                    self.stop(role)
                    self.session.publish(f'{role} process exited {job.process.returncode}', 'device-observation')
            await asyncio.sleep(.1)

    async def sense(self):
        observed = None
        while not self.session.stopped():
            screen = self.session.load()['screen']
            if screen and screen['image'] != observed:
                observed = screen['image']
                write(self.session.path('orientation.json'), dict(status='pending', image=observed))
                result = await self.bus.request('retina', 'inspect', dict(image=observed,
                    desktop=screen['desktop'], window=screen['window'], question='Describe the visible desktop, '
                    'applications, controls, objects and spatial relations without assuming a task. '
                    'Give normalized-1000 boxes in the submitted image and describe uncertainty.'), CFG['look_seconds'])
                entry = self.session.load()['operations'][result['operation_id']]
                write(self.session.path('orientation.json'), dict(status=result['outcome'], image=observed,
                    id=result['operation_id'], response=entry.get('response') or entry.get('error')))
                self.session.edit(lambda state: state['observations'].update({observed: result['operation_id']}))
            await asyncio.sleep(.1)

    async def infer(self):
        session = self.session
        from core import identity
        session.edit(lambda current: current.update(episode=dict(id=identity(), native_actions=0, at=time.time())))
        state = session.load()
        context = dict(owner=state['owner'], memory=state['memory'], episode=state['episode'], artifacts=state['artifacts'],
            unknown=state['unknown'], receivers=self.bus.receivers(), orientation=str(session.path('orientation.json')),
            screen={key: state['screen'][key] for key in ('desktop', 'window', 'windows', 'at') if key in state['screen']},
            capture={key: self.capture[key] for key in ('outcome', 'operation_id', 'error') if key in self.capture},
            state=str(session.state_path), tools=str(session.path('tools.py')), output_directory=str(session.directory))
        exchange = Exchange(session, 'luna', 'cursor')
        prompt = exchange.dump('request', (ROOT / 'instinct.txt').read_text('utf-8-sig') +
                               '\nCURRENT RUN\n' + json.dumps(context, ensure_ascii=False), 'txt')
        result = await execute(cursor(prompt, '--approve-mcps'), exchange, CFG.get('mind_seconds', 360), extension='jsonl', cwd=ROOT, env=os.environ | session.vendor)
        session.edit(lambda state: state['executions'][result['id']].update(model_completion='incomplete'))
        if result['outcome'] != 'completed':
            raise RuntimeError(f"Cursor process {result['outcome']} (returncode {result['returncode']}, {result['error']}); "
                f"stderr {result['stderr_path']} ({result['stderr_bytes']} bytes), output {result['stdout_path']} ({result['stdout_bytes']} bytes)")
        final = None
        for line in result['stdout'].splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get('type') == 'result':
                final = event
        completion = 'incomplete' if final is None else 'failed' if final.get('is_error') else 'completed'
        session_ids = list(dict.fromkeys(json.loads(line).get('session_id') for line in result['stdout'].splitlines()
            if line.strip() and 'session_id' in line))
        session.edit(lambda state: state['executions'][result['id']].update(
            model_completion=completion, cursor_session_ids=[sid for sid in session_ids if sid]))
        if completion != 'completed':
            raise RuntimeError('Cursor ' + completion + '; inspect its retained JSONL response')
        answer = final.get('result', '')
        session.edit(lambda state: state['memory'].update(last_words=answer))
        if answer:
            print(answer, flush=True)

    async def rest(self, seconds):
        inputs, deadline = len(self.session.load()['inputs']), time.monotonic() + seconds
        while not self.session.stopped() and time.monotonic() < deadline and len(self.session.load()['inputs']) == inputs:
            await asyncio.sleep(.1)

    async def live(self):
        try:
            for role in ('vision-server', *ROLES):
                self.spawn(role)
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(self.maintain())
                async with asyncio.timeout(30):
                    while not {'manipulator', 'retina'}.issubset(self.bus.receivers()):
                        await asyncio.sleep(.05)
                threading.Thread(target=self.console, daemon=True).start()
                if len(sys.argv) > 1:
                    self.session.publish(' '.join(sys.argv[1:]), 'local-owner')
                self.consume_inputs()
                self.session.edit(lambda state: state.update(process_state='running'))
                print(f'Trident alive. Run: {self.session.directory}\nOwner input: stdin, Telegram text, voice note or call.', flush=True)
                tasks.create_task(self.sense())
                while not self.session.stopped():
                    self.capture = await self.bus.request('manipulator', 'capture', {}, 10)
                    if not self.capture.get('image'):
                        print(self.capture, file=sys.stderr, flush=True)
                    try:
                        await self.infer()
                    except Exception as error:
                        self.session.publish(f'Mind invocation failed: {type(error).__name__}: {error}', 'device-observation')
                        self.consume_inputs()
                        await self.rest(30)
        finally:
            self.session.request_stop()
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and any(job.process.poll() is None for role, job in self.jobs.items() if role in ROLES):
                time.sleep(.05)
            for role in list(self.jobs):
                self.stop(role)

def launch():
    python = ROOT / 'runtime' / 'python' / 'Scripts' / 'python.exe'
    if not python.is_file():
        raise RuntimeError('Install first with Python 3.11: python install.py')
    guard, job, session, interrupted = Mutex(ROOT, exclusive=True), None, None, None
    process_state = 'stopped'
    def stop(signum, frame):
        nonlocal interrupted
        interrupted = interrupted or time.monotonic()
        if session:
            session.request_stop()
    previous = signal.signal(signal.SIGINT, stop)
    fresh = '--fresh' in sys.argv[1:]
    try:
        session = Session.create(fresh)
        job = Job([str(python), '-u', '-B', str(ROOT / 'run.py'), *[arg for arg in sys.argv[1:] if arg != '--fresh']], cwd=ROOT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, env=os.environ | {'TRIDENT_BOOT_RUN': str(session.directory),
            'TRIDENT_RUN': str(session.directory), 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'})
        while job.process.poll() is None:
            if interrupted and time.monotonic()-interrupted >= 5:
                break
            time.sleep(.05)
        job.drain(interrupted + 5 if interrupted else time.monotonic() + 5)
        return job.process.poll()
    except BaseException:
        process_state = 'shutdown_failed'
        raise
    finally:
        if job:
            job.close()
        if session:
            from devices import release_input
            release_input()
            for marker in session.directory.glob('*.receiver'):
                marker.unlink(missing_ok=True)
            def finish(state):
                state.update(active=False if process_state == 'stopped' else None, process_state=process_state)
                for entry in state['executions'].values():
                    if entry['outcome'] == 'running':
                        entry['outcome'] = 'unknown'
            session.edit(finish)
            session.mutex.close()
        signal.signal(signal.SIGINT, previous)
        guard.close()

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    if mode == '--mcp':
        asyncio.run(mcp(Session.attach()))
    elif mode == '--program':
        asyncio.run(Cortex(Session.attach()).program(Path(sys.argv[2])))
    elif os.environ.pop('TRIDENT_BOOT_RUN', None):
        session = Session.attach()
        session.prepare()
        try:
            asyncio.run(Organism(session).live())
        except asyncio.CancelledError:
            pass
    else:
        raise SystemExit(launch())
