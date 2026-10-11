import asyncio, json, os, re, signal, sys, time, tomllib, urllib.error, urllib.request
from datetime import datetime, timezone
from contextlib import AsyncExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
def journal(run, kind, *, filename='events.jsonl', **fields):
    record = (json.dumps(dict(at=datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                              event=kind, process_pid=os.getpid(), **fields), ensure_ascii=False, separators=(',', ':')) + '\n').encode()
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
    reference = f'{reference}-{time.time_ns()}'
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
        temporary = path.with_name(f'{path.name}-{os.getpid()}-{time.time_ns()}.pending')
        try:
            temporary.write_text(content, encoding='utf-8')
            temporary.replace(path)
        finally: temporary.unlink(missing_ok=True)
    @staticmethod
    @contextmanager
    def lock(path):
        import msvcrt
        with path.open('a+b') as handle:
            if handle.tell() == 0: handle.write(b'\0'); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            try: yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

class Exclusive:
    """One cross-process GPU turn. The caller loads a model after enter and unloads it before exit."""
    def __init__(self, path):
        self.path = path
    async def __aenter__(self):
        import msvcrt
        self.handle = self.path.open('a+b')
        def take():
            if self.handle.tell() == 0: self.handle.write(b'\0'); self.handle.flush()
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_LOCK, 1)
        await asyncio.to_thread(take)
        return self
    async def __aexit__(self, *_):
        import msvcrt
        def give():
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            self.handle.close()
        await asyncio.to_thread(give)

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
                raw = response.read_text('utf-8')
                response.unlink()
                try:
                    reply = json.loads(raw)
                    status, trace = reply['status'], reply['trace']
                    if not isinstance(status, str) or not isinstance(trace, str): raise TypeError('Invalid reply fields')
                    expected = ('ACK', 'END_OF_READ')[frame.read]
                    if status in ('ACK', 'END_OF_READ'):
                        if status != expected: raise ValueError('Wrong reply status')
                    elif status not in ('NOT_READY', 'UNKNOWN_DATA', 'FULL') or trace != frame.trace(status=status):
                        raise ValueError('Wrong refusal trace')
                except (ValueError, KeyError, TypeError):
                    self.record(self.device.address, frame.address, 'malformed_reply', raw, sequence, frame)
                    self.fault()
                    raise ValueError('Malformed reply') from None
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
            cleanup.enter_context(self.cfg.lock(self.bus.home / 'owner.lock'))
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
def validate_server_identity(health, models, expected, *, require_image=False):
    """Validate the pinned llama.cpp server's health and advertised model.

    HTTP 200 alone can be returned by an unrelated process already on the port.
    A tools server must also advertise an image-capable model, not text-only.
    """
    try:
        healthy = json.loads(health)
        listing = json.loads(models)
        entries = listing['data']
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeError('Invalid llama server health/model metadata') from exc
    if not isinstance(healthy, dict) or healthy.get('status') != 'ok':
        raise RuntimeError('Unexpected llama server health response')
    if not isinstance(entries, list):
        raise RuntimeError('Invalid llama server model list')
    match = next((item for item in entries if isinstance(item, dict) and os.path.normcase(os.path.normpath(str(item.get('id')))) == os.path.normcase(os.path.normpath(expected))), None)
    if match is None:
        raise RuntimeError(f'Expected model {expected!r} is not exposed by this llama server')
    if require_image:
        architecture = match.get('architecture') or {}
        modalities = architecture.get('input_modalities') if isinstance(architecture, dict) else None
        if not isinstance(modalities, list) or 'image' not in modalities:
            raise RuntimeError(f'Model {expected!r} does not advertise image input support')
    return match

class Server:
    def __init__(self, device, settings):
        self.device, self.settings, self.process = device, settings, None
        self.required, self.ready, self.intent, self.watched, self.hooked = True, False, False, None, False
    def request(self, endpoint, body=None):
        request = urllib.request.Request(self.settings['url'] + endpoint, None if body is None else json.dumps(body).encode(), {'Content-Type': 'application/json'})
        seconds = self.device.cfg['limits'][('http_seconds', 'health_seconds')[endpoint == '/health']]
        with urllib.request.urlopen(request, timeout=seconds) as response: return response.read()
    async def start(self, *, required=True):
        if self.process is not None and self.process.returncode is None: await self.close()
        self.required, self.ready, self.intent = required, False, False
        models = str(self.device.cfg.path(self.device.cfg['models']['path']))
        self.settings = dict(self.settings)
        self.settings['model'] = os.path.normpath(os.path.expandvars(str(self.settings['model'])).format(**self.settings, models=models))
        command = [os.path.expandvars(str(value)).format(**self.settings, models=models) for value in self.settings['command']]
        try:
            self.process = await asyncio.create_subprocess_exec(*command, cwd=self.device.cfg.root,
                                                               stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        except OSError as error:
            journal(self.device.run, 'server_spawn_failed', role=self.device.name, executable=command[0], error=str(error))
            if required: raise
            return
        self.command, self.output = command, bytearray()
        journal(self.device.run, 'server_start', role=self.device.name, pid=self.process.pid, executable=command[0])
        if not self.hooked:
            self.device.cleanup.push_async_callback(self.close)
            self.hooked = True
        self.reader = self.device.tasks.create_task(self.capture(self.process))
        self.watched = self.device.tasks.create_task(self.watch(self.process, self.reader))
        if required: await self.healthy()
        else: self.device.tasks.create_task(self.healthy())
    async def healthy(self):
        try:
            async with asyncio.timeout(self.settings['start_seconds']):
                while True:
                    if self.process is None or self.process.returncode is not None: raise RuntimeError('server exited before ready')
                    try:
                        health = await asyncio.to_thread(self.request, '/health')
                        models = await asyncio.to_thread(self.request, '/v1/models')
                        model = validate_server_identity(health, models, self.settings['model'],
                                                         require_image=self.device.name == 'tools')
                        if self.process.returncode is not None:
                            raise RuntimeError('server exited before model verification completed')
                        self.ready = True
                        journal(self.device.run, 'server_verified', role=self.device.name,
                                model=model['id'], image_capable=self.device.name == 'tools')
                        return
                    except urllib.error.HTTPError as error:
                        if error.code != 503: raise
                    except urllib.error.URLError as error:
                        if not isinstance(error.reason, ConnectionRefusedError): raise
                    await asyncio.sleep(self.device.cfg['limits']['health_poll'])
        except Exception as error:
            self.ready = False
            journal(self.device.run, 'server_health_failed', role=self.device.name, error=f'{type(error).__name__}: {error}',
                    output=evidence(self.device.run, f'{self.device.name}-health', self.output.decode(errors='replace')))
            if self.required: raise
    async def capture(self, process):
        limit, stream = self.device.cfg['limits']['server_tail_bytes'], process.stdout
        while chunk := await stream.read(4096):
            self.output.extend(chunk)
            if len(self.output) > limit: del self.output[:-limit]
    async def watch(self, process, reader):
        code = await process.wait()
        await reader
        if process is not self.process: return
        self.ready = False
        journal(self.device.run, 'server_exit', role=self.device.name, code=code, command=self.command,
                output=evidence(self.device.run, f'{self.device.name}-server-exit', self.output.decode(errors='replace')))
        if self.required and not self.intent: raise RuntimeError(f'{self.device.name} server exited {code}; see server_exit in events.jsonl')
    async def close(self):
        self.intent = True
        process, watched = self.process, self.watched
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        if watched is not None: await watched
        self.ready, self.intent = False, False
    async def chat(self, messages, **options):
        body = dict(model=self.settings['model'], messages=messages, max_tokens=self.settings['max_tokens'], **self.device.cfg['sampling']) | options
        response = json.loads(await asyncio.to_thread(self.request, '/v1/chat/completions', body))
        choice = response['choices'][0]
        reason = choice.get('finish_reason')
        content = choice['message'].get('content')
        usage = response.get('usage') or {}
        journal(self.device.run, 'model_http_response', role=self.device.name,
                finish_reason=reason, prompt_tokens=usage.get('prompt_tokens'),
                completion_tokens=usage.get('completion_tokens'),
                total_tokens=usage.get('total_tokens'),
                content_chars=len(content) if isinstance(content, str) else None)
        if reason == 'length':
            raise RuntimeError('Model reply truncated at maximum tokens; result must not be treated as complete')
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError('Model reply has no text content (check response type/reasoning format)')
        return content
