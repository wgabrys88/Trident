import ast, asyncio, inspect, json, os, sys, time
from pathlib import Path
from core import CFG, ROOT, Bus, Exchange, NotAttempted, Session, run_process, token

class Cortex:
    def __init__(self, session):
        self.session, self.bus = session, Bus(session, 'cortex')
        self.state = session.load()
        self.memory, self.owner, self.screen = (self.state[key] for key in ('memory', 'owner', 'screen'))
        self.previous, self.records = self.state['previous'], []
        self.namespace = {}

    def save(self):
        self.state.update(memory=self.memory, screen=self.screen, previous=self.records)
        self.session.save(self.state)

    async def device(self, role, operation, arguments, seconds=30, identity=None):
        if self.session.interrupted():
            raise InterruptedError('New owner input or stop; previous effects are retained')
        ident = identity or token()
        entry = dict(id=ident, at=time.time(), role=role, operation=operation,
                     arguments=arguments, owner_id=self.owner['id'], outcome='unknown', phase='attempted')
        if role=='manipulator' and operation!='capture' or role=='relay' and operation=='dial':
            if any(value['role']==role for value in self.state['unknown'].values()):
                raise NotAttempted('Resolve the prior unknown effect before repeating this device side effect')
            self.state['unknown'][ident] = dict(role=role, at=entry['at'])
        self.state['outcomes'][ident] = entry; self.save()
        result = await self.bus.request(role, operation, arguments, seconds)
        entry.update(result=result, outcome=result['outcome'], phase='returned')
        if result['outcome']!='unknown':
            self.state['unknown'].pop(ident, None)
        self.records.append(entry); self.save()
        return result|dict(id=ident, artifact=str(self.session.state_path))

    async def native(self, source, prediction, targets=None):
        result = await self.device('manipulator', 'execute', dict(source=source,
            prediction=prediction, targets=targets or {}), 30)
        if result.get('image'):
            self.screen = result
            self.namespace['screen'] = self.screen
        self.save()
        return result

    async def observe(self, question, region=None, mode='inspect'):
        result = await self.device('retina', 'inspect', dict(image=self.screen['image'],
            question=question, mode=mode, **(dict(region=region) if region else {})), CFG['look_seconds'])
        return result|{key:self.screen[key] for key in ('desktop', 'window', 'at')}

    def accept(self, observation, box):
        return {key:observation[key] for key in ('at', 'desktop', 'window', 'image')}|dict(box=box)

    async def ask(self, state, questions):
        return await self.device('discern', 'decide', dict(state=state, questions=questions), CFG['decision_seconds'])

    async def speak(self, text, identity=None):
        identity = identity or token()
        if identity in self.state['outcomes']:
            return self.state['outcomes'][identity].get('result', dict(outcome='unknown', delivered=False, hearing_verified=False))
        return await self.device('phonation', 'speak', dict(text=text, narration_id=identity), 55, identity)

    async def resolve(self, ident, evidence, interpretation):
        entry = self.state['outcomes'][ident]
        if ident in self.state['unknown'] and entry['role']=='manipulator':
            receipt = self.state['outcomes'][evidence]
            if receipt['role']!='retina' or receipt['result']['outcome']!='completed' or receipt['result']['image_at']<entry['at']:
                raise NotAttempted('Unknown input needs a local-vision receipt from a fresh screenshot')
        if ident in self.state['unknown'] and entry['role']=='relay':
            receipt = self.state['outcomes'][evidence]
            if receipt['at']<entry['at'] or receipt['operation']!='status' or receipt['result']['outcome']!='completed':
                raise NotAttempted('Unknown call needs an actual subsequent status receipt')
        entry['correction'] = dict(at=time.time(), evidence=evidence, interpretation=interpretation)
        self.state['unknown'].pop(ident, None); self.save()
        return entry

    async def recall(self, ident): return self.state['outcomes'][ident]

    async def rest(self, seconds=60):
        deadline = time.monotonic()+seconds
        while time.monotonic()<deadline and not self.session.interrupted():
            await asyncio.sleep(.1)

    async def execute(self, program):
        tools = self.session.file('tools.py')
        namespace = dict(__name__='__trident__', __file__=str(program),
                         memory=self.memory, owner=self.owner, screen=self.screen, previous=self.previous,
                         ROOT=ROOT, RUN=self.session.directory, asyncio=asyncio, artifact=self.session.artifact)
        self.namespace = namespace
        for name in ('native', 'observe', 'accept', 'ask', 'speak', 'device', 'resolve', 'recall', 'rest'):
            namespace[name] = getattr(self, name)
        exec(compile(tools.read_text('utf-8'), str(tools), 'exec'), namespace)
        tree = ast.parse(program.read_text('utf-8-sig'))
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            tree.body[-1] = ast.Assign(targets=[ast.Name(id='__value__', ctx=ast.Store())], value=tree.body[-1].value)
            ast.fix_missing_locations(tree)
        definitions = [node for node in tree.body if isinstance(node,
                       (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom))]
        try:
            result = eval(compile(tree, str(program), 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT), namespace)
            if inspect.isawaitable(result):
                await result
            if '__value__' in namespace:
                print(repr(namespace['__value__']), flush=True)
            if definitions:
                old = ast.parse(tools.read_text('utf-8')).body
                replaced = {node.name for node in definitions if hasattr(node, 'name')}
                merged = [node for node in old if getattr(node, 'name', None) not in replaced]+definitions
                tools.write_text(ast.unparse(ast.Module(body=merged, type_ignores=[]))+'\n', 'utf-8')
        finally:
            self.save()

class Mind:
    def __init__(self, session):
        self.session = session
        self.folder = Path(os.environ['LOCALAPPDATA'])/'cursor-agent'/'versions'/CFG['cursor_version']
        self.policy = (ROOT/'instinct.txt').read_text('utf-8-sig')

    async def infer(self, context):
        exchange = Exchange(self.session, 'luna', 'cursor')
        prompt = exchange.dump('request', self.policy+'\nCONTEXT\n'+json.dumps(context, ensure_ascii=False), 'txt')
        bootstrap = "process.argv.push(require('node:fs').readFileSync(process.argv.pop(),'utf8'));require(process.argv[1]);"
        raw = (await run_process([str(self.folder/'node.exe'), '-e', bootstrap, str(self.folder/'index.js'), '--trust', '-p',
            '--approve-mcps', '--model', CFG['mind_model'], '--workspace', str(ROOT),
            '--output-format', 'stream-json', '--stream-partial-output', str(prompt)], CFG['mind_seconds'], exchange,
            extension='jsonl', cwd=ROOT)).decode('utf-8', errors='replace')
        texts = []
        for line in raw.splitlines():
            if not line.strip(): continue
            event = json.loads(line)
            if event.get('type')=='assistant':
                texts.append(''.join(part['text'] for part in event['message']['content'] if part.get('type')=='text'))
        if not texts: raise ValueError('No final answer from Cursor')
        return texts[-1]

async def mcp(session):
    for line in sys.stdin.buffer:
        message = json.loads(line)
        exchange = Exchange(session, 'cursor', 'mcp')
        exchange.dump('request', line, 'jsonl')
        if 'id' not in message: continue
        method = message['method']; result = {}
        if method=='initialize':
            result = dict(protocolVersion='2025-06-18', capabilities=dict(tools={}), serverInfo=dict(name='trident', version='1'))
        elif method=='tools/list':
            result = dict(tools=[dict(name='python', description='Execute arbitrary Python with injected native(Python_source, prediction), observe(question, region=None), ask(state, questions), memory and devices. LFM alone sees images; Jev answers your text decision questions. No trident import is needed. gui exists inside native source. Return text receipts, never images.',
                inputSchema=dict(type='object', properties=dict(source=dict(type='string')), required=['source']))])
        elif method=='tools/call':
            execution = Exchange(session, 'mcp', 'cortex')
            program = execution.dump('request', message['params']['arguments']['source'], 'py')
            failure = None
            try:
                output = (await run_process([sys.executable, '-u', '-B', str(ROOT/'mind.py'), str(program)],
                                 CFG['execution_seconds'], execution, cwd=ROOT)).decode('utf-8', errors='replace')
            except Exception as error:
                output = ''; failure = f'{type(error).__name__}: {error}'
            state = session.load()
            content = [dict(type='text', text=json.dumps(dict(stdout=output, error=failure,
                operations=state['previous'], memory=state['memory']), ensure_ascii=False))]
            result = dict(content=content, isError=bool(failure))
        response = json.dumps(dict(jsonrpc='2.0', id=message['id'], result=result), ensure_ascii=False)
        exchange.dump('response', response+'\n', 'jsonl')
        sys.stdout.buffer.write((response+'\n').encode('utf-8')); sys.stdout.buffer.flush()

if __name__=='__main__':
    session = Session.attach()
    asyncio.run(mcp(session) if sys.argv[1]=='--mcp' else Cortex(session).execute(Path(sys.argv[1])))
