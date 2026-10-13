import asyncio, ctypes, hashlib, json, os, subprocess, sys, time, tomllib, uuid
import urllib.error, urllib.request
from contextlib import contextmanager
from ctypes import wintypes as W
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFG = tomllib.loads((ROOT / 'organism.toml').read_text('utf-8-sig'))
ROLES = ('manipulator', 'retina', 'discern', 'cochlea', 'phonation', 'relay')

def identity():
    return uuid.uuid4().hex

def timestamp():
    return datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%fZ')

def atomic(path, data):
    pending = path.with_name(path.name + '.' + identity() + '.pending')
    pending.write_bytes(data)
    pending.replace(path)

def write(path, value):
    atomic(path, json.dumps(value, ensure_ascii=False).encode('utf-8'))

kernel = ctypes.WinDLL('kernel32', use_last_error=True)
for name, args, result in (
    ('CreateMutexW', [ctypes.c_void_p, W.BOOL, W.LPCWSTR], W.HANDLE),
    ('WaitForSingleObject', [W.HANDLE, W.DWORD], W.DWORD),
    ('ReleaseMutex', [W.HANDLE], W.BOOL),
    ('CreateJobObjectW', [ctypes.c_void_p, W.LPCWSTR], W.HANDLE),
    ('SetInformationJobObject', [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD], W.BOOL),
    ('AssignProcessToJobObject', [W.HANDLE, W.HANDLE], W.BOOL),
    ('TerminateJobObject', [W.HANDLE, W.UINT], W.BOOL),
    ('QueryInformationJobObject', [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD, ctypes.c_void_p], W.BOOL),
    ('CloseHandle', [W.HANDLE], W.BOOL)):
    function = getattr(kernel, name)
    function.argtypes, function.restype = args, result

def checked(value):
    if not value:
        raise ctypes.WinError(ctypes.get_last_error())
    return value

class Mutex:
    def __init__(self, name, exclusive=False):
        name = 'Local\\Trident-' + hashlib.sha256(str(name).casefold().encode()).hexdigest()
        self.handle = checked(kernel.CreateMutexW(None, False, name))
        if exclusive and ctypes.get_last_error() == 183:
            self.close()
            raise RuntimeError('This workspace already has a running organism')

    @contextmanager
    def held(self):
        if kernel.WaitForSingleObject(self.handle, 5000) not in (0, 128):
            raise TimeoutError('Current-run state is busy')
        try:
            yield
        finally:
            checked(kernel.ReleaseMutex(self.handle))

    def close(self):
        if self.handle:
            checked(kernel.CloseHandle(self.handle))
            self.handle = None

class Session:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.state_path = next(self.directory.glob('*_state.json'))
        self.mutex = Mutex(self.directory)
        self.paths = {name: Path(path) for name, path in self.load()['paths'].items()}

    @classmethod
    def create(cls):
        directory = ROOT / ('run_' + timestamp())
        directory.mkdir()
        paths = {name: str(directory / (timestamp() + '_' + name))
                 for name in ('state.json', 'tools.py', 'stop', 'vendor')}
        Path(paths['tools.py']).write_text('', 'utf-8')
        Path(paths['stop']).write_bytes(b'')
        write(Path(paths['state.json']), dict(paths=paths, active=True, process_state='starting',
            memory=dict(goal='', narrative=''), owner=dict(id=identity(), text='', at=time.time(), source='no-owner-goal'),
            operations={}, executions={}, artifacts={}, unknown={}, screen={}, inputs=[]))
        return cls(directory)

    @classmethod
    def attach(cls):
        return cls(os.environ['TRIDENT_RUN'])

    def load(self):
        with self.mutex.held():
            return json.loads(self.state_path.read_text('utf-8'))

    def edit(self, change):
        with self.mutex.held():
            state = json.loads(self.state_path.read_text('utf-8'))
            result = change(state)
            write(self.state_path, state)
            return result

    def path(self, name):
        return self.paths[name]

    def name(self, label, extension):
        return self.directory / f'{timestamp()}_{label}_{identity()}.{extension}'

    def artifact(self, kind, extension, producer=None):
        path = self.name(kind, extension)
        self.edit(lambda state: state['artifacts'].update({str(path): dict(kind=kind,
            producer=producer or os.environ.get('TRIDENT_EXECUTION', 'python'), allocated=time.time())}))
        return path

    def stopped(self):
        return self.path('stop').stat().st_size > 0

    def request_stop(self):
        with self.path('stop').open('ab') as marker:
            marker.write(b'1')

    def publish(self, text, source, at=None, ident=None):
        message = dict(id=ident or identity(), text=text, source=source, at=at or time.time(),
                       kind='owner' if source in ('local-owner', 'telegram-owner', 'telegram-call') else 'observation')
        write(self.name('inbox', 'json'), message)

    def prepare(self):
        vendor = self.path('vendor')
        profile, temporary = vendor / 'home' / '.cursor', vendor / 'temp'
        profile.mkdir(parents=True)
        temporary.mkdir()
        os.environ.update(TRIDENT_RUN=str(self.directory), HF_HOME=str(vendor / 'hf'),
            PSModuleAnalysisCachePath=str(vendor / 'PowerShellModuleAnalysisCache'),
            PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
        real = {key: os.environ[key] for key in ('USERPROFILE', 'HOME', 'TEMP', 'TMP') if key in os.environ}
        self.vendor = dict(HOME=str(profile.parent), USERPROFILE=str(profile.parent), CURSOR_CONFIG_DIR=str(profile),
            CURSOR_DATA_DIR=str(vendor / 'cursor-data'), CURSOR_AGENT_DISABLE_DEBUG_LOG='1', XDG_CACHE_HOME=str(vendor / 'cache'),
            XDG_CONFIG_HOME=str(vendor / 'config'), TEMP=str(temporary), TMP=str(temporary))
        write(profile / 'cli-config.json', dict(version=1, editor=dict(vimMode=False),
            permissions=dict(allow=['Read(**)', 'Write(**)', 'Shell(*)', 'WebFetch(*)', 'Mcp(*:*)'],
                deny=['Read(runtime/**)', 'Read(run_*/**)'] + [f'Read(**/*.{ext})' for ext in ('png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp')]),
            sandbox=dict(mode='disabled', readBoundary='system'), approvalMode='unrestricted'))
        write(profile / 'mcp.json', dict(mcpServers=dict(trident=dict(type='stdio', command=sys.executable,
            args=['-u', '-B', str(ROOT / 'run.py'), '--mcp'], env=real | {key: os.environ[key] for key in
                ('TRIDENT_RUN', 'PYTHONUTF8', 'PYTHONIOENCODING', 'PYTHONUNBUFFERED')}))))
        os.environ['OPENROUTER_API_KEY'] = os.environ.get('OPENROUTER_API_KEY') or (ROOT.parent / '.env').read_text('utf-8-sig').strip()

class Exchange:
    def __init__(self, session, sender, receiver, ident=None):
        self.session, self.sender, self.receiver = session, sender, receiver
        self.id = ident or identity()

    def path(self, direction, extension):
        parties = (self.sender, self.receiver) if direction == 'request' else (self.receiver, self.sender)
        return self.session.directory / f'{timestamp()}_{parties[0]}_to_{parties[1]}_{self.id}_{direction}.{extension}'

    def dump(self, direction, data, extension='json'):
        path = self.path(direction, extension)
        atomic(path, data if isinstance(data, bytes) else data.encode('utf-8'))
        return path

    def http(self, url, payload, seconds, headers=None, record=None):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.dump('request', json.dumps(record, ensure_ascii=False) if record else body)
        self.response = self.path('response', 'json')
        request = urllib.request.Request(url, body, headers or {'Content-Type': 'application/json'})
        received = bytearray()
        try:
            try:
                response = urllib.request.urlopen(request, timeout=seconds)
            except urllib.error.HTTPError as error:
                response = error
            with response, self.response.open('wb') as output:
                while chunk := response.read1(65536):
                    output.write(chunk)
                    output.flush()
                    received.extend(chunk)
                if response.status >= 400:
                    raise ValueError(f'HTTP {response.status}: ' + received.decode('utf-8', errors='replace'))
            return json.loads(received)
        except Exception as error:
            self.dump('error', f'{type(error).__name__}: {error}', 'txt')
            raise

class Job:
    def __init__(self, command, **options):
        class Basic(ctypes.Structure):
            _fields_ = [('times', ctypes.c_int64 * 2), ('flags', W.DWORD), ('minimum', ctypes.c_size_t),
                       ('maximum', ctypes.c_size_t), ('active', W.DWORD), ('affinity', ctypes.c_size_t),
                       ('priority', W.DWORD), ('scheduling', W.DWORD)]
        class Limits(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', ctypes.c_uint64 * 6), ('memory', ctypes.c_size_t * 4)]
        self.handle, self.process = checked(kernel.CreateJobObjectW(None, None)), None
        try:
            limits = Limits()
            limits.basic.flags = 0x2000
            checked(kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
            flags = options.pop('creationflags', subprocess.CREATE_NO_WINDOW)
            self.process = subprocess.Popen(command, creationflags=flags | 4, **options)
            checked(kernel.AssignProcessToJobObject(self.handle, W.HANDLE(int(self.process._handle))))
            resume = ctypes.WinDLL('ntdll').NtResumeProcess
            resume.argtypes, resume.restype = [W.HANDLE], ctypes.c_long
            if resume(W.HANDLE(int(self.process._handle))) != 0:
                raise OSError('Owned process could not resume')
        except BaseException:
            if self.process:
                self.process.kill()
            self.close()
            raise

    def close(self):
        if self.handle:
            checked(kernel.CloseHandle(self.handle))
            self.handle = None

    def drain(self, deadline):
        class Accounting(ctypes.Structure):
            _fields_ = [('times', ctypes.c_int64 * 4), ('counts', W.DWORD * 4)]
        checked(kernel.TerminateJobObject(self.handle, 130))
        accounting = Accounting()
        while True:
            checked(kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None))
            if accounting.counts[2] == 0:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError('Owned process tree did not finish shutdown within five seconds')
            time.sleep(.01)

async def execute(command, exchange, seconds=None, extension='txt', **options):
    output, stderr = exchange.path('response', extension), exchange.path('stderr', 'txt')
    requests = list(exchange.session.directory.glob(f'*_{exchange.sender}_to_{exchange.receiver}_{exchange.id}_request.*'))
    entry = dict(request=str(requests[0]), output=str(output), errors=str(stderr), outcome='running',
                 sender=exchange.sender, receiver=exchange.receiver, at=time.time(),
                 owner_id=exchange.session.load()['owner']['id'])
    exchange.session.edit(lambda state: state['executions'].update({exchange.id: entry}))
    job, error, code = None, None, None
    try:
        with output.open('wb') as out, stderr.open('wb') as err:
            job = Job(command, stdout=out, stderr=err, **options)
            async with asyncio.timeout(seconds):
                while job.process.poll() is None:
                    if exchange.session.stopped():
                        raise asyncio.CancelledError()
                    await asyncio.sleep(.05)
            code = job.process.returncode
    except BaseException as failure:
        error = f'{type(failure).__name__}: {failure}'
        exchange.dump('error', error + '; execution completion not established', 'txt')
        if isinstance(failure, asyncio.CancelledError):
            raise
    finally:
        if job:
            job.close()
        outcome = 'unknown' if error else 'completed' if code == 0 else 'failed'
        exchange.session.edit(lambda state: state['executions'][exchange.id].update(
            outcome=outcome, returncode=code, error=error))
    return dict(id=exchange.id, outcome=outcome, stdout=output.read_text('utf-8', errors='replace'),
                stderr=stderr.read_text('utf-8', errors='replace'), returncode=code, error=error,
                stdout_path=str(output), stdout_bytes=output.stat().st_size, stderr_path=str(stderr), stderr_bytes=stderr.stat().st_size)

class NotAttempted(ValueError):
    pass

class Bus:
    def __init__(self, session, sender=None):
        self.session, self.sender = session, sender or os.environ.get('TRIDENT_PARTY', 'python')

    def receivers(self):
        return [path.name.split('_', 3)[3].removesuffix('.receiver') for path in self.session.directory.glob('*.receiver')]

    def begin(self, job, path, role):
        def change(state):
            if job['id'] in state['operations']:
                state['operations'][job['id']]['request'] = str(path)
                return
            effect = role == 'manipulator' and job['op'] != 'capture' or role == 'relay' and job['op'] == 'dial'
            state['operations'][job['id']] = dict(role=role, operation=job['op'], sender=job['sender'],
                at=time.time(), request=str(path), outcome='unknown', owner_id=state['owner']['id'],
                execution=job.get('execution'))
            if effect and role in state['unknown'].values():
                return 'Resolve the prior uncertain effect before repeating this device'
            if effect:
                state['unknown'][job['id']] = role
        reason = self.session.edit(change)
        if reason:
            raise NotAttempted(reason)

    def finish(self, ident, result, path):
        def change(state):
            field = 'error' if '_error.' in path.name else 'response'
            entry = state['operations'][ident]
            entry[field] = str(path)
            if field == 'response' or 'response' not in entry:
                entry['outcome'] = result['outcome']
            if state['operations'][ident]['role'] == 'manipulator' and result.get('image'):
                state['screen'] = {key: result[key] for key in ('image', 'desktop', 'window', 'windows', 'at')}
            if entry['outcome'] != 'unknown':
                state['unknown'].pop(ident, None)
        self.session.edit(change)

    async def request(self, role, operation, arguments, seconds=30, ident=None):
        if ident and ident in self.session.load()['operations']:
            entry = self.session.load()['operations'][ident]
            return json.loads(Path(entry['response']).read_text('utf-8')) if 'response' in entry else dict(operation_id=ident, outcome='unknown')
        exchange = Exchange(self.session, self.sender, role, ident)
        job = dict(id=exchange.id, sender=self.sender, op=operation, args=arguments, expires=time.time() + seconds,
                   execution=os.environ.get('TRIDENT_EXECUTION'))
        raw = exchange.dump('request', json.dumps(job, ensure_ascii=False), 'prepared.json')
        try:
            self.begin(job, raw, role)
            if self.session.stopped() or role not in self.receivers():
                raise NotAttempted('No available receiver ' + role)
        except NotAttempted as error:
            cancelled = raw.with_name(raw.name.replace('.prepared.json', '.cancelled.json'))
            raw.rename(cancelled)
            self.begin(job, cancelled, role)
            result = dict(operation_id=job['id'], outcome='not_attempted', error=str(error))
            reply = exchange.dump('response', json.dumps(result, ensure_ascii=False))
            if job['id'] in self.session.load()['operations']:
                self.finish(job['id'], result, reply)
            return result
        queued = raw.with_name(raw.name.replace('.prepared.json', '.json'))
        def publish(state):
            raw.rename(queued)
            state['operations'][job['id']]['request'] = str(queued)
        self.session.edit(publish)
        raw = queued
        try:
            async with asyncio.timeout(seconds):
                while True:
                    if self.session.stopped():
                        raise asyncio.CancelledError()
                    replies = list(self.session.directory.glob(f'*_{role}_to_{self.sender}_{job["id"]}_response.json'))
                    if replies:
                        result = json.loads(replies[0].read_text('utf-8'))
                        self.finish(job['id'], result, replies[0])
                        return result
                    await asyncio.sleep(.05)
        except (TimeoutError, asyncio.CancelledError) as error:
            try:
                cancelled = raw.with_name(raw.stem + '.cancelled.json')
                raw.rename(cancelled)
                result = dict(operation_id=job['id'], outcome='not_attempted', error='Request cancelled before claiming')
                self.begin(job, cancelled, role)
            except FileNotFoundError:
                result = dict(operation_id=job['id'], outcome='unknown', error='Claimed request interrupted; effect uncertain')
            receipt = exchange.dump('error', json.dumps(result, ensure_ascii=False))
            self.finish(job['id'], result, receipt)
            if isinstance(error, asyncio.CancelledError):
                raise
            return result

    async def serve(self, role, handler):
        marker = self.session.directory / f'{timestamp()}_{role}.receiver'
        write(marker, dict(pid=os.getpid()))
        try:
            while not self.session.stopped():
                for raw in sorted(self.session.directory.glob(f'*_to_{role}_*_request.json')):
                    active = raw.with_name(raw.stem + '.active.json')
                    try:
                        raw.rename(active)
                    except FileNotFoundError:
                        continue
                    job = json.loads(active.read_text('utf-8'))
                    try:
                        self.begin(job, active, role)
                        if time.time() >= job['expires']:
                            raise NotAttempted('Request expired before execution')
                        async with asyncio.timeout(job['expires'] - time.time()):
                            result = await handler(job['op'], job['args'])
                        result.setdefault('outcome', 'completed')
                    except Exception as error:
                        result = dict(error=f'{type(error).__name__}: {error}', outcome='not_attempted' if isinstance(error, NotAttempted)
                            else 'unknown' if role in ('manipulator', 'relay') and not isinstance(error, ValueError) else 'failed')
                    result['operation_id'] = job['id']
                    reply = Exchange(self.session, job['sender'], role, job['id']).dump('response', json.dumps(result, ensure_ascii=False))
                    if job['id'] in self.session.load()['operations']:
                        self.finish(job['id'], result, reply)
                    if result['outcome'] == 'unknown':
                        marker.unlink(missing_ok=True)
                        os._exit(1)
                await asyncio.sleep(.05)
        finally:
            marker.unlink(missing_ok=True)
