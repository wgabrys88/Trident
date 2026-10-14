import ast, asyncio, inspect, io, json, os, signal, subprocess, sys, threading, time
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from urllib.parse import urlparse

sys.dont_write_bytecode = True
from core import Bus, CFG, Exchange, Job, Mutex, ROOT, ROLES, Session, execute
from devices import release_input

class Cortex:
    def __init__(self, session):
        self.session, self.bus = session, Bus(session, 'cortex')
        self.device = self.bus.request
        self.namespace = dict(__name__='__trident__', ROOT=ROOT, RUN=session.directory, asyncio=asyncio,
                              artifact=session.artifact, request_stop=session.request_stop)
        for name in ('device', 'native', 'observe', 'decide', 'point', 'accept', 'recall', 'speak', 'rest', 'remember'):
            self.namespace[name] = getattr(self, name)

    async def native(self, *, code, prediction, targets=None):
        trail = self.namespace['memory']['trail']
        trail.append(dict(goal=self.namespace['memory']['goal'], prediction=prediction, image=None))
        del trail[:-20]
        self.remember(trail=trail)
        result = await self.device('manipulator', 'execute', dict(source=code, prediction=prediction, targets=targets))
        trail[-1].update(image=result.get('image'), error=result.get('error'))
        self.remember(trail=trail)
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
        result = await self.device('retina', 'inspect', dict(image=screen['image'], question=question,
            desktop=screen['desktop'], window=screen['window'], region=region))
        return {key: result[key] for key in ('answer', 'geometry', 'error') if key in result}

    async def decide(self, state, questions):
        return await self.device('discern', 'decide', dict(state=state, questions=questions))

    def point(self, observation, x, y):
        geometry = observation['geometry']
        left, top, right, bottom = geometry['crop']
        ox, oy = geometry['desktop'][:2]
        return [ox + round(left + x*(right-left-1)/1000), oy + round(top + y*(bottom-top-1)/1000)]

    def accept(self, observation, box):
        return dict(box=self.point(observation, *box[:2]) + self.point(observation, *box[2:]),
                    desktop=observation['geometry']['desktop'])

    async def recall(self, ident):
        with self.session.mutex.held():
            entries = (self.session.directory / 'exchange.log').read_text('utf-8').splitlines()
        return [entry for line in entries if (entry := json.loads(line))['id'] == ident]

    async def speak(self, text):
        return await self.device('phonation', 'speak', dict(text=text))

    def remember(self, **changes):
        def update(state):
            state['memory'].update({key: value for key, value in changes.items()
                if key != 'goal' or state['owner']['id'] == self.namespace['owner']['id']})
        self.session.edit(update)
        self.namespace['memory'].update(changes)

    async def rest(self, seconds=None):
        self.session.edit(lambda state: state.update(wake_at=None if seconds is None else time.time() + seconds))

    async def python(self, source):
        state = self.session.load()
        self.namespace.update(memory=state['memory'], owner=state['owner'], screen=state['screen'], __result__=None)
        out, err, error = io.StringIO(), io.StringIO(), None
        with redirect_stdout(out), redirect_stderr(err):
            try:
                tree = ast.parse(source)
                if tree.body and isinstance(tree.body[-1], ast.Expr):
                    tree.body[-1] = ast.Assign(targets=[ast.Name(id='__result__', ctx=ast.Store())], value=tree.body[-1].value)
                    ast.fix_missing_locations(tree)
                value = eval(compile(tree, '<python>', 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT), self.namespace)
                if inspect.isawaitable(value):
                    await value
                if self.namespace['__result__'] is not None:
                    print(repr(self.namespace['__result__']))
            except Exception as failure:
                error = f'{type(failure).__name__}: {failure}'
        return dict(stdout=out.getvalue(), stderr=err.getvalue(), error=error)

    def tools(self):
        text = dict(type='string')
        return [dict(name='observe', description='Retina describes pixels, labels and normalized-1000 boxes. '
            'Returns answer and crop geometry. region is relative to the screen or an earlier look passed as within.',
            inputSchema=dict(type='object', properties=dict(question=text,
                region=dict(type='array', items=dict(type='number'), minItems=4, maxItems=4),
                within=dict(type='object')), required=['question'])),
            dict(name='decide', description='Use Jev for real choices, mismatched predictions, irreversible steps and final completion. '
                'Supply goal, narrative, latest Retina answer and candidate actions as one choice question; act on the top choice. '
                'choice criteria maps names to descriptions; score criteria is an ordered list; noul is a proposition. '
                'Returns probabilities; confidence is not proof.',
                inputSchema=dict(type='object', properties=dict(state={}, questions=dict(type='object')), required=['state', 'questions'])),
            dict(name='python', description='Send {"source":"Python code"}; top-level await and a live namespace for this invocation. '
                'Native uses required named arguments: await native(code="...", prediction="...", targets=...). '
                'Returns stdout/stderr and the last expression. Only Retina interprets pixels; file headers may be checked.',
                inputSchema=dict(type='object', properties=dict(source=text), required=['source']))]

async def mcp(session):
    cortex = Cortex(session)
    delivered, known = 0, {}
    for raw in sys.stdin.buffer:
        exchange = Exchange(session, 'cursor', 'mcp')
        message = json.loads(raw)
        exchange.record('request', message)
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
            inputs = state['inputs'][delivered:]
            delivered = len(state['inputs'])
            current = dict(owner=state['owner'], memory=state['memory'], receivers=sorted(cortex.bus.receivers()))
            payload = dict(result=value, inputs=inputs) | {key: item for key, item in current.items() if known.get(key) != item}
            known = current
            result = dict(content=[dict(type='text', text=json.dumps(payload, ensure_ascii=False))],
                isError=False)
        else:
            result = {}
        reply = json.dumps(dict(jsonrpc='2.0', id=message['id'], result=result), ensure_ascii=False) + '\n'
        exchange.record('response', result)
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
        job = self.jobs.pop(role)
        try:
            job.drain(time.monotonic() + 5)
        finally:
            job.close()
        for path in self.session.directory.glob('*_' + role + '.receiver'):
            path.unlink(missing_ok=True)
        with self.session.mutex.held():
            for path in self.session.directory.glob(f'*_to_{role}_*.json*'):
                path.unlink(missing_ok=True)

    def console(self):
        for line in sys.stdin:
            if line.strip():
                self.session.publish(line.strip(), 'local-owner')

    async def maintain(self):
        while not self.session.stopped():
            restarts = self.session.edit(lambda state: state['memory'].pop('restart_devices')) if self.session.load()['memory'].get('restart_devices') else []
            for role, job in list(self.jobs.items()):
                if job.process.poll() is not None or role in restarts:
                    self.stop(role)
                    self.session.publish(f'{role} restarting (exit {job.process.returncode})', 'device-observation')
                    self.spawn(role)
            await asyncio.sleep(1)

    async def infer(self):
        session = self.session
        screen = await self.bus.request('manipulator', 'capture', {})
        if not screen.get('image'):
            raise RuntimeError(str(screen))
        look = await self.bus.request('retina', 'inspect', dict(image=screen['image'], desktop=screen['desktop'],
            window=screen['window'], question='Describe the visible desktop, objects, controls and readable labels. '
            'Give normalized-1000 boxes and describe uncertainty without assuming a task.'))
        state = session.load()
        context = dict(owner=state['owner'], memory=state['memory'], look=look,
            receivers=self.bus.receivers(), output_directory=str(session.directory))
        exchange = Exchange(session, 'luna', 'cursor')
        text = (ROOT / 'instinct.txt').read_text('utf-8-sig') + '\nCURRENT RUN\n' + json.dumps(context, ensure_ascii=False)
        exchange.record('request', text)
        prompt = exchange.path('request', 'txt')
        prompt.write_text(text, 'utf-8')
        try:
            result = await execute(cursor(prompt, '--approve-mcps'), exchange, stream=True, wake_id=state['wake_id'],
                cwd=ROOT, env=os.environ | session.vendor)
        finally:
            prompt.unlink(missing_ok=True)
        if session.load()['wake_id'] != state['wake_id']:
            self.stop('manipulator')
            release_input()
            self.spawn('manipulator')
            return state['wake_id']
        if result['outcome'] == 'interrupted':
            return state['wake_id']
        if result['outcome'] != 'completed':
            raise RuntimeError(f"Cursor exited {result['returncode']}: {result['stderr']}")
        final = next((event for line in reversed(result['stdout'].splitlines()) if line.strip()
            and (event := json.loads(line)).get('type') == 'result'), None)
        if final is None or final.get('is_error'):
            raise RuntimeError('Cursor did not finish successfully; its stream is retained')
        answer = final.get('result', '')
        session.edit(lambda state: state['memory'].update(last_words=answer))
        if answer:
            print(answer, flush=True)
        return state['wake_id']

    async def pause(self, wake_id):
        while not self.session.stopped():
            state = self.session.load()
            if state['wake_id'] != wake_id or (state['wake_at'] is not None and time.time() >= state['wake_at']):
                return
            await asyncio.sleep(.1)

    async def live(self):
        try:
            for role in ('vision-server', *ROLES):
                self.spawn(role)
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(self.maintain())
                while not {'manipulator', 'retina'}.issubset(self.bus.receivers()):
                    if self.session.stopped():
                        return
                    await asyncio.sleep(.05)
                threading.Thread(target=self.console, daemon=True).start()
                if len(sys.argv) > 1:
                    self.session.publish(' '.join(sys.argv[1:]), 'local-owner')
                self.session.edit(lambda state: state.update(process_state='running'))
                print(f'Trident alive. Run: {self.session.directory}\nOwner input: stdin, Telegram text, voice note or call.', flush=True)
                while not self.session.stopped():
                    self.session.edit(lambda state: state.update(wake_at=0))
                    wake_id = self.session.load()['wake_id']
                    try:
                        wake_id = await self.infer()
                    except Exception as error:
                        self.session.publish(f'Mind invocation failed: {type(error).__name__}: {error}', 'device-observation')
                    await self.pause(wake_id)
        finally:
            self.session.request_stop()
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and any(job.process.poll() is None for role, job in self.jobs.items() if role in ROLES):
                await asyncio.sleep(.05)
            for role in list(self.jobs):
                self.stop(role)

def launch():
    python = ROOT / 'runtime' / 'python' / 'Scripts' / 'python.exe'
    if not python.is_file():
        raise RuntimeError('Install first with Python 3.11: python install.py')
    guard, job, session, deadline = Mutex(ROOT, exclusive=True), None, None, None
    process_state = 'shutdown_failed'
    def stop(signum, frame):
        nonlocal deadline
        deadline = deadline or time.monotonic() + 5
        if session:
            session.request_stop()
    previous = signal.signal(signal.SIGINT, stop)
    try:
        session = Session.create('--fresh' in sys.argv[1:])
        job = Job([str(python), '-u', '-B', str(ROOT / 'run.py'), *[arg for arg in sys.argv[1:] if arg != '--fresh']], cwd=ROOT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, env=os.environ | {'TRIDENT_BOOT_RUN': str(session.directory),
            'TRIDENT_RUN': str(session.directory), 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'})
        while job.process.poll() is None:
            if session.stopped():
                deadline = deadline or time.monotonic() + 5
            if deadline and time.monotonic() >= deadline:
                break
            time.sleep(.05)
        job.drain(deadline or time.monotonic() + 5)
        process_state = 'stopped'
        return job.process.poll()
    finally:
        if job:
            job.close()
        if session:
            release_input()
            for marker in session.directory.glob('*.receiver'):
                marker.unlink(missing_ok=True)
            session.edit(lambda state: state.update(active=False if process_state == 'stopped' else None, process_state=process_state))
            session.mutex.close()
        signal.signal(signal.SIGINT, previous)
        guard.close()

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    if mode == '--mcp':
        asyncio.run(mcp(Session.attach()))
    elif os.environ.pop('TRIDENT_BOOT_RUN', None):
        session = Session.attach()
        session.prepare()
        try:
            asyncio.run(Organism(session).live())
        except asyncio.CancelledError:
            pass
    else:
        raise SystemExit(launch())
