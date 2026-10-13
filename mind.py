import ast, asyncio, base64, inspect, json, os, sys, time
from pathlib import Path
from core import CFG, ROOT, Bus, NotAttempted, Session, execute, token

class Cortex:
    def __init__(self, session):
        self.session, self.bus = session, Bus(session)
        self.state = session.load()
        self.memory, self.owner, self.screen = (self.state[key] for key in ('memory', 'owner', 'screen'))
        self.previous, self.records, self.targets = self.state['previous'], [], {}
        self.namespace = {}

    def save(self):
        self.state.update(memory=self.memory, screen=self.screen, previous=self.records)
        self.session.save(self.state)

    async def device(self, role, operation, arguments, seconds=30):
        if self.session.interrupted():
            raise InterruptedError('New owner input or stop; previous effects are retained')
        ident = token()
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

    async def native(self, source, prediction, window=None, targets=None):
        if not prediction.strip():
            raise ValueError('Predict the effect before native input')
        result = await self.device('manipulator', 'execute', dict(source=source,
            prediction=prediction, window=window, targets=targets or {}), 30)
        if result.get('image'):
            self.screen = result
            self.namespace['screen'] = self.screen
        self.targets.clear(); self.save()
        return result

    async def observe(self, question, region=None, mode='inspect'):
        result = await self.device('retina', 'inspect', dict(image=self.screen['image'],
            question=question, mode=mode, **(dict(region=region) if region else {})), CFG['look_seconds'])
        return result|{key:self.screen[key] for key in ('desktop', 'window', 'at')}

    def accept(self, observation, box):
        if (observation['image'] not in self.state['pixels'] or
            not (len(box)==4 and 0<=box[0]<box[2]<=1000 and 0<=box[1]<box[3]<=1000) or
            time.time()-observation['at']>CFG['target_seconds']):
            raise NotAttempted('Target requires an exact box and current native pixel Read')
        target = {key:observation[key] for key in ('at', 'desktop', 'window', 'image')}|dict(box=box)
        self.targets[observation['id']] = target
        return target

    async def ask(self, state, questions):
        return await self.device('discern', 'decide', dict(state=state, questions=questions), CFG['decision_seconds'])

    async def speak(self, text, identity=None):
        identity = identity or token()
        if identity in self.state['voice']:
            return self.state['voice'][identity]
        self.state['voice'][identity] = dict(text=text, outcome='unknown', delivered=False, hearing_verified=False)
        self.save()
        result = await self.device('phonation', 'speak', dict(text=text, narration_id=identity), 55)
        self.state['voice'][identity] = result; self.save()
        return result

    async def resolve(self, ident, evidence, interpretation):
        entry = self.state['outcomes'][ident]
        if ident in self.state['unknown'] and entry['role']=='manipulator':
            if evidence not in self.state['pixels'] or self.state['pixels'][evidence]<entry['at']:
                raise NotAttempted('Resolve unknown input with freshly delivered pixels')
        if ident in self.state['unknown'] and entry['role']=='relay':
            receipt = self.state['outcomes'][evidence]
            if receipt['at']<entry['at'] or receipt['operation']!='status' or receipt['result']['outcome']!='completed':
                raise NotAttempted('Unknown call needs an actual subsequent status receipt')
        entry['correction'] = dict(at=time.time(), evidence=evidence, interpretation=interpretation)
        self.state['unknown'].pop(ident, None); self.save()
        return entry

    async def recall(self, ident):
        return self.state['outcomes'][ident]

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
        if tools.exists():
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
                old = ast.parse(tools.read_text('utf-8') if tools.exists() else '').body
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

    def infer(self, context):
        image = Path(context['screen']['image']).resolve()
        prompt = self.session.file('prompt.txt')
        prompt.write_text('FIRST native Read '+str(image)+'. Inspect actual pixels.\n'+self.policy+
            '\nCONTEXT\n'+json.dumps(context, ensure_ascii=False)+
            '\nRead the orientation file if useful; pending means its independent description is still running.', 'utf-8')
        bootstrap = "process.argv.push(require('node:fs').readFileSync(process.argv.pop(),'utf8'));require(process.argv[1]);"
        raw = execute([str(self.folder/'node.exe'), '-e', bootstrap, str(self.folder/'index.js'), '--trust', '-p',
            '--approve-mcps', '--model', CFG['mind_model'], '--workspace', str(ROOT),
            '--output-format', 'stream-json', str(prompt)], CFG['mind_seconds'], cwd=ROOT).decode('utf-8', errors='replace')
        texts, inspected = [], False
        state = self.session.load()
        for line in raw.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get('type')=='assistant':
                texts.append(''.join(part['text'] for part in event['message']['content'] if part.get('type')=='text'))
            if event.get('type')=='tool_call' and event.get('subtype')=='completed':
                read = event.get('tool_call', {}).get('readToolCall', {})
                path = Path(read.get('args', {}).get('path', ''))
                path = (path if path.is_absolute() else ROOT/path).resolve()
                receipt = read.get('result', {}).get('success', {})
                if (receipt.get('dataBlobId') and path.is_file() and receipt.get('fileSize')==path.stat().st_size
                        and not receipt.get('exceededLimit') and path.suffix.lower()=='.png'):
                    state['pixels'][str(path)] = path.stat().st_mtime
                    if path==image:
                        inspected = True
        self.session.save(state)
        if not texts or not inspected:
            raise ValueError('No final answer or successful native PNG Read receipt')
        return texts[-1]

def mcp(session):
    for line in sys.stdin:
        message = json.loads(line)
        if 'id' not in message:
            continue
        method = message['method']; result = {}
        if method=='initialize':
            result = dict(protocolVersion='2025-06-18', capabilities=dict(tools={}), serverInfo=dict(name='trident', version='1'))
        elif method=='tools/list':
            result = dict(tools=[dict(name='python', description='Execute arbitrary Python with top-level await, memory and running devices. Create reusable tools and return actual receipts and fresh pixels.',
                inputSchema=dict(type='object', properties=dict(source=dict(type='string')), required=['source']))])
        elif method=='tools/call':
            program = session.artifact('program', 'py')
            program.write_text(message['params']['arguments']['source'], 'utf-8')
            failure = None
            try:
                output = execute([sys.executable, '-B', str(ROOT/'mind.py'), str(program)],
                                 CFG['execution_seconds'], cwd=ROOT).decode('utf-8', errors='replace')
            except Exception as error:
                output = ''; failure = f'{type(error).__name__}: {error}'
            state = session.load()
            content = [dict(type='text', text=json.dumps(dict(stdout=output, error=failure,
                operations=state['previous'], memory=state['memory']), ensure_ascii=False))]
            image = Path(state['screen']['image'])
            content.append(dict(type='image', data=base64.b64encode(image.read_bytes()).decode(), mimeType='image/png'))
            state['pixels'][str(image)] = state['screen']['at']; session.save(state)
            result = dict(content=content, isError=bool(failure))
        print(json.dumps(dict(jsonrpc='2.0', id=message['id'], result=result), ensure_ascii=False), flush=True)

if __name__=='__main__':
    session = Session.attach()
    mcp(session) if sys.argv[1]=='--mcp' else asyncio.run(Cortex(session).execute(Path(sys.argv[1])))
