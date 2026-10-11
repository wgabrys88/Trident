import asyncio, hashlib, json, sys, time
from jsonschema import Draft202012Validator, ValidationError
from bus import Device, Frame, Refused, Server, journal, evidence

class Cli:
    def __init__(self, device):
        self.device, self.settings = device, device.settings
        self.folder = max((path for path in device.cfg.path(self.settings['versions']).iterdir() if path.is_dir()), key=lambda path: path.name)
    async def reply(self, prompt, schema):
        command = [str(self.folder / 'node.exe'), '-e', "process.argv.push(require('fs').readFileSync(0,'utf8'));require(process.argv[1]);", str(self.folder / 'index.js'), '-p', '--model', self.settings['model'], '--output-format', 'stream-json', '--workspace', str(self.device.cfg.root)]
        process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=self.device.stderr, cwd=self.device.cfg.root, limit=self.settings['event_bytes'])
        journal(self.device.run, 'model_launch', n=self.device.number, pid=process.pid, mode='agent', command=command)
        texts = []
        try:
            process.stdin.write(prompt.encode())
            await process.stdin.drain()
            process.stdin.close()
            async for line in process.stdout:
                event = json.loads(line)
                if not isinstance(event, dict) or not isinstance(event.get('type'), str): raise ValueError('Invalid CLI event')
                if event['type'] == 'tool_call': journal(self.device.run, 'agent_tool', turn=self.device.number, payload=evidence(self.device.run, f'native-{self.device.number}', line.decode()))
                if event['type'] == 'result' and event.get('is_error'): raise RuntimeError(str(event))
                if event['type'] == 'assistant': texts.append(''.join(part['text'] for part in event['message']['content'] if part['type'] == 'text'))
            if await process.wait(): raise RuntimeError('CLI failed')
            if not texts: raise RuntimeError('CLI returned no assistant text')
            return texts[-1]
        finally:
            if process.returncode is None: process.kill(); await process.wait()

class Http:
    def __init__(self, device):
        self.device = device
        device.server = Server(device, device.cfg['llama'] | device.settings)
    async def reply(self, prompt, schema):
        return await self.device.server.chat([dict(role='user', content=prompt)], response_format=dict(type='json_object', schema=schema))

class Mind(Device):
    async def start(self):
        self.settings = self.cfg['mind'][sys.argv[2]]
        self.number, self.used, self.busy, self.generation = 0, 0, False, 0
        self.goal, self.ledger, self.server = '', [], None
        self.queue = asyncio.PriorityQueue()
        self.stderr = (self.run / 'mind.log').open('ab')
        self.cleanup.callback(self.stderr.close)
        self.engine = {'cli': Cli, 'http': Http}[self.settings['transport']](self)
        if self.server is not None: await self.server.start()
        self.by_name, shapes = {}, {}
        for role, registers in self.cfg['registers'].items():
            for register, value in registers.items():
                direction, _, rest = value.partition(':')
                action, arguments, returns = [part.strip() for part in rest.split('|')]
                if action == '-': continue
                if action in self.by_name: raise ValueError(f'Duplicate action {action}')
                self.by_name[action] = (self.cfg['address'][role], register, 'read' in direction.split('/'))
                shapes[action] = dict(arguments=arguments, returns=returns)
        for action, spec in self.cfg['actions'].items():
            arguments, _, returns = spec.partition('|')
            shapes[action] = dict(arguments=arguments.strip(), returns=returns.strip())
        self.template = json.dumps(shapes, ensure_ascii=False)
        self.schema = dict(type='object', additionalProperties=False, required=['do', 'with', 'expect', 'check'], properties=dict(do=dict(enum=list(shapes)), **{'with': dict(type='string'), 'expect': dict(type='string', minLength=1), 'check': dict(type='string')}))
        self.validator = Draft202012Validator(self.schema)
        journal(self.run, 'model_contract', identity=self.settings['identity'], capabilities=evidence(self.run, 'capabilities', self.template), schema=evidence(self.run, 'schema', json.dumps(self.schema)))
    async def receive(self, source, frame):
        if frame.read: return (f'{int(self.busy):02d}' if frame.register == '00' else self.settings['identity']).encode()
        owner = source in (self.cfg['address']['telegram'], self.cfg['address']['ears'])
        if source == self.cfg['address']['timer'] and (self.busy or not self.queue.empty()): raise Refused('NOT_READY')
        if owner:
            self.used, self.generation = 0, self.generation + 1
            (self.run / 'mind-activity').touch()
        else:
            if self.used >= self.cfg['bus']['self_turn_cap']: raise Refused('FULL')
            self.used += 1
        self.queue.put_nowait((int(not owner), time.time_ns(), self.generation, source, frame))
        return b''
    async def worker(self):
        while True:
            _, _, generation, source, frame = await self.queue.get()
            if generation == self.generation: await self.work(source, frame)
            else: journal(self.run, 'turn_skipped', src=source, generation=generation)
    def compose(self, policy):
        while len(self.ledger) > 1 and len(json.dumps(self.ledger)) > self.settings['context_chars']: self.ledger.pop(0)
        return policy + '\n\n' + json.dumps(dict(capabilities=json.loads(self.template), action_schema=self.schema, record=self.ledger, goal=self.goal), ensure_ascii=False)
    async def work(self, source, frame):
        self.number, self.busy = self.number + 1, True
        generation = self.generation
        if source in (self.cfg['address']['telegram'], self.cfg['address']['ears']): self.goal = frame.text
        goal = self.goal
        policy = (self.cfg.root / 'prompt.txt').read_text('utf-8')
        prompt = self.compose(policy)
        journal(self.run, 'turn_input', n=self.number, src=source, generation=generation, goal=self.goal, policy=hashlib.sha256(policy.encode()).hexdigest()[:16], text=evidence(self.run, f'input-{self.number}', prompt))
        action, actual, answer, shot, follow = {}, '', '', '', True
        try:
            raw = await asyncio.wait_for(self.engine.reply(prompt, self.schema), self.settings['turn_seconds'])
            journal(self.run, 'turn_output', n=self.number, reply=evidence(self.run, f'output-{self.number}', raw))
            if generation != self.generation: return
            action = json.loads(raw)
            self.validator.validate(action)
            if action['do'] == 'nothing': follow = False
            elif action['do'] in ('say', 'done'):
                if not action['with']: raise ValueError('Empty owner message')
                await self.bus.transfer(Frame(self.cfg['address']['telegram'], '10', action['with']))
                actual = json.dumps(dict(accepted=True, delivery_verified=False))
                if action['do'] == 'done': self.goal, follow = '', False
            else:
                address, register, read = self.by_name[action['do']]
                result = await self.bus.transfer(Frame(address, register, action['with'], read))
                actual = result.decode() if read else json.dumps(dict(accepted=True, effect_verified=False))
                if action['check']:
                    target = self.cfg['address']['tools']
                    shot = (await self.bus.transfer(Frame(target, '01', f'check-{time.time_ns()}.png', True))).decode()
                    answer = (await self.bus.transfer(Frame(target, '03', shot + '\ntext\n' + action['check'], True))).decode()
                    journal(self.run, 'action_check', n=self.number, expect=action['expect'], question=action['check'], answer=evidence(self.run, 'answer', answer), shot=shot)
        except (RuntimeError, ValueError, ValidationError, Refused, TimeoutError, KeyError, TypeError) as error:
            actual = json.dumps(dict(error=type(error).__name__, detail=str(error), result=actual), ensure_ascii=False)
            journal(self.run, 'turn_fault', n=self.number, error=evidence(self.run, 'fault', actual))
        finally: self.busy = False
        if generation != self.generation:
            journal(self.run, 'turn_superseded', n=self.number)
            return
        entry = dict(goal=goal, decision=action, actual=actual, check_image=shot, check_answer=answer)
        self.ledger.append(entry)
        journal(self.run, 'comparison', n=self.number, record=evidence(self.run, 'comparison', json.dumps(entry, ensure_ascii=False)))
        if follow:
            try: await self.bus.transfer(Frame(self.address, '01', ''))
            except Refused as error: journal(self.run, 'continuation_refused', error=str(error))

if __name__ == '__main__': Mind('mind').launch()
