import asyncio, json, os, re, signal, sys, time, tomllib, urllib.error, urllib.request
from datetime import datetime, timezone
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
def journal(run, kind, *, filename='events.jsonl', **fields):
    record = (json.dumps(dict(at=datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                              event=kind, **fields), ensure_ascii=False, separators=(',', ':')) + '\n').encode()
    with (Path(run) / filename).open('ab', buffering=0) as stream:
        descriptor = stream.fileno()
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        try: os.write(descriptor, record)
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(descriptor, fcntl.LOCK_UN)

def evidence(run, reference, text):
    if len(text) <= 1000: return text
    journal(run, 'detail', filename='details.jsonl', ref=reference, text=text)
    return dict(preview=text[:160], characters=len(text), detail=reference)

class Refused(Exception):
    pass
class Config(dict):
    def __init__(self):
        self.root = Path(__file__).resolve().parent
        super().__init__(tomllib.loads((self.root / 'config.toml').read_text('utf-8')))
    def path(self, value):
        return self.root / os.path.expandvars(str(value))
    @staticmethod
    def publish(path, content):
        temporary = path.with_suffix('.pending')
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(path)
@dataclass(frozen=True)
class Frame:
    address: str
    register: str
    text: str = ''
    read: bool = False
    def trace(self, data=b'', status='ACK'):
        tokens = ['S', self.address, 'W', 'A']
        for byte in bytes.fromhex(self.register) + self.text.encode():
            tokens.extend((f'{byte:02x}', 'A'))
        if status == 'NOT_READY': return f'S {self.address} W NA P'
        if status == 'UNKNOWN_DATA': return f'S {self.address} W A {self.register} NA P'
        if status == 'FULL': return ' '.join(tokens[:-1] + ['NA', 'P'])
        if self.read:
            tokens.extend(('Sr', self.address, 'R', 'A'))
            for index, byte in enumerate(data): tokens.extend((f'{byte:02x}', ('A', 'NA')[index == len(data) - 1]))
        return ' '.join(tokens + ['P'])
    @classmethod
    def parse(cls, trace):
        match = re.fullmatch(r'S ([0-7][0-9a-f]) W A((?: [0-9a-f]{2} A)+)( Sr \1 R A)? P', trace)
        if match is None: raise ValueError('Malformed transaction')
        payload = bytes.fromhex(match[2].replace(' A', ''))
        return cls(match[1], f'{payload[0]:02x}', payload[1:].decode(), bool(match[3]))
    def result(self, trace):
        prefix = re.escape(self.trace()[:-2])
        suffix = r'((?: [0-9a-f]{2} A)* [0-9a-f]{2} NA)? P' if self.read else r' P'
        match = re.fullmatch(prefix + suffix, trace)
        if match is None: raise ValueError('Malformed acknowledgement')
        return bytes.fromhex((match[1] or '').replace(' A', '').replace(' NA', '')) if self.read else b''
class Bus:
    def __init__(self, device):
        self.device, self.cfg = device, device.cfg
        self.home = self.cfg.root / 'wire' / device.address
        self.state = json.loads((self.home / 'state.json').read_text())
        self.lock = asyncio.Lock()
    def save(self):
        self.cfg.publish(self.home / 'state.json', json.dumps(self.state))
    def record(self, source, target, status, trace, sequence=None, frame=None, data=b''):
        fields = dict(src=source, dst=target, seq=sequence, status=status)
        if frame is not None: fields.update(reg=frame.register, op=('W', 'R')[frame.read])
        if data: fields['read_bytes'] = len(data)
        if status in ('malformed', 'malformed_reply'): fields['trace'] = evidence(self.device.run, f'bus-{source}-{sequence}', trace)
        journal(self.device.run, 'bus', **fields)
    def present(self, address):
        return (self.home.parent / address / 'alive').exists()
    def fault(self):
        self.state['tx'] += 8
        self.save()
        if self.state['tx'] >= 256:
            (self.home / 'busoff').touch()
            raise RuntimeError('Bus off')
    async def listen(self):
        while True:
            for path in self.home.glob('q-*.frame'):
                claimed = path.with_suffix('.active')
                path.rename(claimed)
                self.device.tasks.create_task(self.receive(claimed))
            await asyncio.sleep(self.cfg['bus']['poll'])
    async def receive(self, path):
        source, sequence = path.stem.split('-')[1:]
        trace, held = path.read_text('utf-8'), None
        try:
            frame = Frame.parse(trace)
            if frame.address != self.device.address or not (self.home.parent / source).is_dir(): raise ValueError('Wrong endpoint')
        except ValueError:
            self.state['rx'] += 1
            self.save()
            self.record(source, self.device.address, 'malformed', trace, sequence)
            return path.rename(path.with_suffix('.rejected'))
        try:
            try:
                if any(self.home.glob('hold-*')): raise Refused('NOT_READY')
                contract = self.cfg['registers'][self.device.name].get(frame.register, '')
                if ('write', 'read')[frame.read] not in contract.partition(':')[0].split('/'): raise Refused('UNKNOWN_DATA')
                if self.device.name in self.cfg['holds'] and frame.read:
                    held = self.home / f'hold-{source}-{sequence}-{time.time()}'
                    held.touch()
                data = await self.device.receive(source, frame)
                status = ('ACK', 'END_OF_READ')[frame.read]
                self.state['rx'] = 119 if self.state['rx'] >= 128 else max(0, self.state['rx'] - 1)
                self.save()
            except Refused as error:
                status, data = str(error), b''
            reply = frame.trace(data, status)
            self.record(source, self.device.address, status, reply, sequence, frame, data)
            self.cfg.publish(self.home.parent / source / f'r-{sequence}.frame', json.dumps({'status': status, 'trace': reply}))
            path.unlink()
        finally:
            if held is not None: held.unlink(missing_ok=True)
    async def transfer(self, frame, once=False):
        async with self.lock:
            retries = 0
            while True:
                if max(self.state['tx'], self.state['rx']) >= 128: await asyncio.sleep(self.cfg['bus']['frame_timeout'])
                if not self.present(frame.address):
                    self.record(self.device.address, frame.address, 'NO_RECEIVER', '', frame=frame)
                    raise Refused('NO_RECEIVER')
                self.state['sequence'] += 1
                self.save()
                sequence, target = self.state['sequence'], self.home.parent / frame.address
                self.cfg.publish(target / f'q-{self.device.address}-{sequence}.frame', frame.trace())
                response, deadline = self.home / f'r-{sequence}.frame', time.time() + self.cfg['bus']['frame_timeout']
                while not response.exists():
                    for hold in target.glob(f'hold-{self.device.address}-{sequence}-*'):
                        role = self.cfg['roles'][frame.address]
                        deadline = float(hold.name.rsplit('-', 1)[1]) + self.cfg['holds'][role]
                    if time.time() >= deadline:
                        self.record(self.device.address, frame.address, 'timeout', '', sequence, frame)
                        self.fault()
                        raise TimeoutError(f'{frame.address}: execution outcome unknown; do not repeat')
                    await asyncio.sleep(self.cfg['bus']['poll'])
                reply = json.loads(response.read_text('utf-8'))
                response.unlink()
                status = reply['status']
                if status == 'NOT_READY' and not once:
                    await asyncio.sleep(self.cfg['bus']['frame_timeout'])
                    continue
                if status == 'UNKNOWN_DATA' and retries < self.cfg['bus']['data_retries']:
                    retries += 1
                    continue
                if status not in ('ACK', 'END_OF_READ'): raise Refused(status)
                try: result = frame.result(reply['trace'])
                except ValueError:
                    self.record(self.device.address, frame.address, 'malformed_reply', reply['trace'], sequence, frame)
                    self.fault()
                    raise
                self.state['tx'] = max(0, self.state['tx'] - 1)
                self.save()
                return result
class Device:
    def __init__(self, name):
        self.name, self.cfg, self.run = name, Config(), Path(sys.argv[1])
        self.cfg['roles'] = {address: role for role, address in self.cfg['address'].items()}
        self.address = self.cfg['address'][name]
        self.bus, self.queue = Bus(self), asyncio.Queue()
    def file(self, suffix):
        return self.run / f'{self.name}-{suffix}'
    async def send(self, role, text, register, read=False, once=False):
        try: return await self.bus.transfer(Frame(self.cfg['address'][role], register, text, read), once)
        except (Refused, TimeoutError) as error: print(f'{role}/{register}: {error}', flush=True)
    async def receive(self, source, frame):
        self.queue.put_nowait((source, frame))
        return b''
    async def worker(self):
        while True:
            source, frame = await self.queue.get()
            await self.work(source, frame)
            journal(self.run, 'work_done', role=self.name, src=source, reg=frame.register)
    async def close(self):
        pass
    async def execute(self):
        task, loop = asyncio.current_task(), asyncio.get_running_loop()
        def request_stop(*_):
            self._requested_stop = True
            task.cancel()
            loop.call_soon_threadsafe(lambda: None)
        signal.signal(signal.SIGBREAK, request_stop)
        async with AsyncExitStack() as cleanup, asyncio.TaskGroup() as self.tasks:
            self.cleanup = cleanup
            await self.start()
            cleanup.push_async_callback(self.close)
            alive = self.bus.home / 'alive'
            alive.write_text(str(os.getpid()))
            cleanup.callback(alive.unlink, missing_ok=True)
            self.tasks.create_task(self.bus.listen())
            self.tasks.create_task(self.worker())
    def launch(self):
        try:
            asyncio.run(self.execute())
        except asyncio.CancelledError:
            if not getattr(self, '_requested_stop', False):
                raise
            print('Stopped by CTRL_BREAK_EVENT', flush=True)
class Server:
    def __init__(self, device, settings):
        self.device, self.settings, self.process = device, settings, None
    def request(self, endpoint, body=None):
        request = urllib.request.Request(self.settings['url'] + endpoint, None if body is None else json.dumps(body).encode(), {'Content-Type': 'application/json'})
        seconds = self.device.cfg['limits'][('http_seconds', 'health_seconds')[endpoint == '/health']]
        with urllib.request.urlopen(request, timeout=seconds) as response: return response.read()
    async def start(self):
        command = [os.path.expandvars(str(value)).format(**self.settings, models=str(self.device.cfg.path(self.device.cfg['models']['path']))) for value in self.settings['command']]
        with (self.device.run / 'diagnostics.log').open('ab') as log:
            self.process = await asyncio.create_subprocess_exec(*command, cwd=self.device.cfg.root, stdout=log, stderr=log)
        self.device.cleanup.push_async_callback(self.close)
        self.device.tasks.create_task(self.watch())
        async with asyncio.timeout(self.settings['start_seconds']):
            while True:
                try:
                    await asyncio.to_thread(self.request, '/health')
                    return
                except urllib.error.HTTPError as error:
                    if error.code != 503: raise
                except urllib.error.URLError as error:
                    if not isinstance(error.reason, ConnectionRefusedError): raise
                await asyncio.sleep(self.device.cfg['limits']['health_poll'])
    async def watch(self):
        raise RuntimeError(f'Server exited: {await self.process.wait()}')
    async def close(self):
        if self.process is not None and self.process.returncode is None:
            self.process.kill()
            await self.process.wait()
    async def chat(self, messages, **options):
        body = dict(model=self.settings['model'], messages=messages, max_tokens=self.settings['max_tokens'], **self.device.cfg['sampling']) | options
        return json.loads(await asyncio.to_thread(self.request, '/v1/chat/completions', body))['choices'][0]['message']['content']
