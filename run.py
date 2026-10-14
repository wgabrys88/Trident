import ast, asyncio, copy, inspect, json, os, signal, subprocess, sys, threading, time
from pathlib import Path
from urllib.parse import urlparse

sys.dont_write_bytecode = True
from core import Bus, CFG, Exchange, Job, Mutex, ROOT, ROLES, Session, atomic, execute, write

class Cortex:
    def __init__(self, session):
        self.session, self.bus = session, Bus(session, 'cortex')
        state = session.load()
        self.owner_id, self.memory, self.screen = state['owner']['id'], state['memory'], state['screen']

    async def device(self, role, operation, arguments, seconds=30, ident=None):
        return await self.bus.request(role, operation, arguments, seconds, ident)

    async def native(self, source, prediction, targets=None):
        result = await self.device('manipulator', 'execute', dict(source=source, prediction=prediction,
            targets=targets, execution=os.environ.get('TRIDENT_EXECUTION')), 30)
        return {key: result[key] for key in ('error', 'image', 'window', 'at') if key in result}

    async def observe(self, question, region=None, within=None):
        screen = self.session.load()['screen']
        if within is not None:
            # Zoom: region is in the 0..1000 space of an earlier look; map it into the full screenshot.
            left, top, right, bottom = within['geometry']['crop']
            width, height = within['geometry']['source_size']
            x0, y0, x1, y1 = region
            region = [round((left + x0*(right-left)/1000)*1000/width), round((top + y0*(bottom-top)/1000)*1000/height),
                      round((left + x1*(right-left)/1000)*1000/width), round((top + y1*(bottom-top)/1000)*1000/height)]
        return await self.device('retina', 'inspect', dict(image=screen['image'], question=question,
            desktop=screen['desktop'], window=screen['window'], region=region), CFG['look_seconds'])

    async def decide(self, state, questions):
        return await self.device('discern', 'decide', dict(state=state, questions=questions), CFG['decision_seconds'])

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

    async def speak(self, text, identity=None):
        return await self.device('phonation', 'speak', dict(text=text), 55, identity)

    def remember(self, **changes):
        def update(state):
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
        for name in ('device', 'native', 'observe', 'decide', 'point', 'accept', 'recall', 'speak', 'rest', 'remember'):
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
        return [dict(name='observe', description='Ask local vision about the current screenshot; only it sees pixels and it '
            'can be wrong. Zoom by cropping: region [x0,y0,x1,y1] in 0..1000 of the full screenshot, or of an earlier look '
            'passed as within. Returns its answer text and crop geometry; its coordinates are 0..1000 of the submitted crop.',
            inputSchema=dict(type='object', properties=dict(question=text,
                region=dict(type='array', items=dict(type='number'), minItems=4, maxItems=4),
                within=dict(type='object', description='an earlier observe result')), required=['question'])),
            dict(name='decide', description='Fast probabilistic TEXT judge (Jev). You supply state (goal, vision answers, '
                'boxes, alternatives) and named typed questions: choice {type,instructions,criteria:{name:description}}, '
                'score {type,instructions,criteria:[ordered]}, noul {type,instructions}. Returns answers and distributions. '
                'It cannot see pixels; confidence is not proof.',
                inputSchema=dict(type='object', properties=dict(state={}, questions=dict(type='object',
                    additionalProperties=dict(oneOf=variants))), required=['state', 'questions'])),
            dict(name='python', description='Execute arbitrary Python with top-level await and the injected interfaces '
                'described in your instructions. Returns full stdout/stderr with sizes and paths; everything printed '
                'enters your paid context. Never read or return image bytes.',
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
                isError=False)
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
        state = session.load()
        context = dict(owner=state['owner'], memory=state['memory'], artifacts=state['artifacts'],
            receivers=self.bus.receivers(), orientation=str(session.path('orientation.json')),
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
