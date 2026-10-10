import asyncio, json, sys, time
from jsonschema import Draft202012Validator, ValidationError
from bus import Device, Frame, Refused, Server

class Cli:
    def __init__(self, device):
        self.device, self.settings = device, device.settings
        self.folder = max((path for path in device.cfg.path(self.settings['versions']).iterdir() if path.is_dir()), key=lambda path: path.name)
    async def reply(self, prompt, schema):
        command = [str(self.folder / 'node.exe'), '-e', "process.argv.push(require('fs').readFileSync(0,'utf8'));require(process.argv[1]);", str(self.folder / 'index.js'), '-p', '--mode', self.settings['mode'], '--model', self.settings['model'], '--output-format', 'stream-json', '--workspace', str(self.device.run), '--trust', '--exclude-tools', self.settings['excluded']]
        process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=self.device.stderr, cwd=self.device.run, limit=self.settings['event_bytes'])
        events, texts = self.device.file(f'turn-{self.device.number}.events.jsonl'), []
        try:
            process.stdin.write(prompt.encode())
            await process.stdin.drain()
            process.stdin.close()
            with events.open('wb') as log:
                async for line in process.stdout:
                    log.write(line)
                    log.flush()
                    event = json.loads(line)
                    if event['type'] == 'tool_call': raise RuntimeError('Native CLI tool call violates register-only transport')
                    if event['type'] == 'assistant': texts.append(''.join(part['text'] for part in event['message']['content'] if part['type'] == 'text'))
            if await process.wait(): raise RuntimeError('CLI failed; see mind engine log')
            return texts[-1]
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

class Http:
    def __init__(self, device):
        self.device = device
        device.server = Server(device, device.cfg['llama'] | device.settings)
    async def reply(self, prompt, schema):
        return await self.device.server.chat([dict(role='user', content=prompt)], response_format=dict(type='json_object', schema=schema))

class Mind(Device):
    async def start(self):
        self.settings = self.cfg['mind'][sys.argv[2]]
        self.server, self.number, self.used, self.busy, self.generation, self.stopped = None, 0, 0, False, 0, False
        self.history, self.queue = [], asyncio.PriorityQueue()
        self.stderr = self.file('engine.log').open('ab')
        self.cleanup.callback(self.stderr.close)
        self.engine = {'cli': Cli, 'http': Http}[self.settings['transport']](self)
        if self.server is not None: await self.server.start()
        choices = [dict(type='object', additionalProperties=False, required=['action'], properties=dict(action=dict(const='nothing'))), dict(type='object', additionalProperties=False, required=['action', 'text'], properties=dict(action=dict(const='say'), text=dict(type='string', minLength=1)))]
        for name, registers in self.cfg['registers'].items():
            for direction in ('read', 'write'):
                allowed = [key for key, value in registers.items() if direction in value.partition(':')[0].split('/')]
                if allowed: choices.append(dict(type='object', additionalProperties=False, required=['action', 'address', 'register', 'text'], properties=dict(action=dict(const=direction), address=dict(const=self.cfg['address'][name]), register=dict(enum=allowed), text=dict(type='string'))))
        self.schema, self.validator = {'oneOf': choices}, Draft202012Validator({'oneOf': choices})
    async def receive(self, source, frame):
        if frame.read: return (f'{int(self.busy):02d}' if frame.register == '00' else self.settings['identity']).encode()
        owner = source in (self.cfg['address']['telegram'], self.cfg['address']['ears'], self.cfg['address']['timer'])
        if source == self.cfg['address']['timer'] and (self.busy or not self.queue.empty()): raise Refused('NOT_READY')
        if not owner and self.busy: raise Refused('NOT_READY')
        if owner: self.used, self.stopped, self.generation = 0, False, self.generation + 1
        if not owner:
            cost = (1, -(-self.cfg['bus']['self_turn_cap'] // self.cfg['bus']['fault_turns']))[frame.text.startswith('Fault:')]
            if self.used + cost > self.cfg['bus']['self_turn_cap']: raise Refused('FULL')
            self.used += cost
        self.queue.put_nowait((int(not owner), time.time_ns(), self.generation, source, frame))
        return b''
    async def worker(self):
        while True:
            _, _, generation, source, frame = await self.queue.get()
            if source != self.address or generation == self.generation: await self.work(source, frame)
    async def work(self, source, frame):
        self.number, self.busy = self.number + 1, True
        generation = self.generation
        context = dict(registers={self.cfg['address'][name]: dict(role=name, registers=registers) for name, registers in self.cfg['registers'].items()}, schema=self.schema, proposals=str(self.cfg.root / 'life' / 'proposals'))
        prompt = '\n'.join((json.dumps(context), *self.history, f'Controller {source}: {frame.text}', (self.cfg.root / 'prompt.txt').read_text('utf-8')))
        try: raw = await asyncio.wait_for(self.engine.reply(prompt, self.schema), self.settings['turn_seconds'])
        except RuntimeError as error:
            self.busy = False
            if generation != self.generation: return
            self.history.append(f'Controller {source}: {frame.text}\nFault: {error}')
            await self.follow(f'Fault: {error}. Decide one valid next action; do not repeat unknown effects.')
            return
        self.file(f'turn-{self.number}.txt').write_text(prompt + '\nReply:\n' + raw, encoding='utf-8')
        self.busy = False
        if generation != self.generation: return
        try:
            action = json.loads(raw, object_pairs_hook=self.object)
            self.validator.validate(action)
            self.history.append(f'Controller {source}: {frame.text}\nDispatched: {json.dumps(action)}')
            while sum(map(len, self.history)) > self.settings['context_chars']: self.history.pop(0)
            if action['action'] == 'say': await self.bus.transfer(Frame(self.cfg['address']['telegram'], '10', action['text']))
            if action['action'] in ('read', 'write'):
                result = await self.bus.transfer(Frame(action['address'], action['register'], action['text'], action['action'] == 'read'))
                if action['action'] == 'read': await self.follow(f'Read {action["address"]}/{action["register"]}: {result.decode()}')
        except (ValueError, ValidationError, Refused, TimeoutError) as error:
            self.history.append(f'Controller {source}: {frame.text}\nFault: {error}')
            await self.follow(f'Fault: {error}. Decide one valid next action; do not repeat unknown effects.')
    async def follow(self, text):
        try: await self.bus.transfer(Frame(self.address, '01', text))
        except Refused as error:
            if str(error) == 'FULL' and not self.stopped:
                self.stopped = True
                await self.send('telegram', 'I stopped. I need your help to continue.', '10', once=True)
            else: print(f'Continuation refused: {error}', flush=True)

    @staticmethod
    def object(pairs):
        result = dict(pairs)
        if len(result) != len(pairs): raise ValueError('Duplicate action property')
        return result
Mind('mind').launch()
