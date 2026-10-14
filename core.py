import asyncio, ctypes, hashlib, json, os, subprocess, sys, time, tomllib, uuid
import urllib.request
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
    try:
        pending.write_bytes(data)
        for attempt in range(20):
            try:
                pending.replace(path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(.05)
    finally:
        pending.unlink(missing_ok=True)

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
    def create(cls, fresh=False):
        prior = None if fresh else max(ROOT.glob('run_*/*_state.json'), key=lambda path: path.stat().st_mtime, default=None)
        previous = {}
        if prior:
            memory = json.loads(prior.read_text('utf-8'))['memory']
            previous = dict(run=prior.parent.name, goal=memory.get('goal', ''), narrative=memory.get('narrative', ''),
                            last_words=memory.get('last_words', ''), trail=memory.get('trail', []))
        directory = ROOT / ('run_' + timestamp())
        directory.mkdir()
        paths = {name: str(directory / (timestamp() + '_' + name)) for name in ('state.json', 'stop', 'vendor')}
        Path(paths['stop']).write_bytes(b'')
        write(Path(paths['state.json']), dict(paths=paths, active=True, process_state='starting',
            memory=dict(goal='', narrative='', trail=[], previous_life=previous), owner=dict(id=identity(), text='', at=time.time(), source='no-owner-goal'),
            wake_id=identity(), wake_at=0, screen={}, inputs=[]))
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

    def name(self, kind, extension):
        return self.directory / f'{timestamp()}_{kind}_{identity()}.{extension}'

    artifact = name

    def stopped(self):
        return self.paths['stop'].stat().st_size > 0

    def request_stop(self):
        with self.paths['stop'].open('ab') as marker:
            marker.write(b'1')

    def publish(self, text, source, at=None, ident=None):
        message = dict(id=ident or identity(), text=text, source=source, at=at or time.time(),
                       kind='owner' if source in ('local-owner', 'telegram-owner', 'telegram-call') else 'observation')
        def receive(state):
            state['inputs'].append(message)
            if message['kind'] == 'owner':
                state['owner'] = message
                state['memory']['goal'] = text
            if message['kind'] == 'owner' or source == 'telegram-ring':
                state['wake_id'] = message['id']
        self.edit(receive)
        Exchange(self, source, 'luna', message['id']).record('input', message)

    @property
    def vendor(self):
        vendor = self.paths['vendor']
        return dict(HOME=str(vendor / 'home'), USERPROFILE=str(vendor / 'home'), CURSOR_CONFIG_DIR=str(vendor / 'home' / '.cursor'),
            CURSOR_DATA_DIR=str(vendor / 'cursor-data'), CURSOR_AGENT_DISABLE_DEBUG_LOG='1', XDG_CACHE_HOME=str(vendor / 'cache'),
            XDG_CONFIG_HOME=str(vendor / 'config'), TEMP=str(vendor / 'temp'), TMP=str(vendor / 'temp'))

    def prepare(self):
        vendor = self.paths['vendor']
        profile = Path(self.vendor['CURSOR_CONFIG_DIR'])
        profile.mkdir(parents=True)
        Path(self.vendor['TEMP']).mkdir()
        os.environ.update(TRIDENT_RUN=str(self.directory), HF_HOME=str(vendor / 'hf'),
            PSModuleAnalysisCachePath=str(vendor / 'PowerShellModuleAnalysisCache'),
            PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
        real = {key: os.environ[key] for key in ('USERPROFILE', 'HOME', 'TEMP', 'TMP') if key in os.environ}
        write(profile / 'cli-config.json', dict(version=1,
            permissions=dict(allow=['Read(**)', 'Write(**)', 'Shell(*)', 'WebFetch(*)', 'Mcp(*:*)'],
                deny=[]),
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
        sender, receiver = (self.sender, self.receiver) if direction == 'request' else (self.receiver, self.sender)
        return self.session.directory / f'{timestamp()}_{sender}_to_{receiver}_{self.id}_{direction}.{extension}'

    def record(self, event, data):
        entry = dict(at=timestamp(), id=self.id, sender=self.sender, receiver=self.receiver, event=event, data=data)
        with self.session.mutex.held(), (self.session.directory / 'exchange.log').open('a', encoding='utf-8') as log:
            log.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def http(self, url, payload, seconds, headers=None, record=None):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.record('request', record if record is not None else payload)
        request = urllib.request.Request(url, body, headers or {'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=seconds) as response:
                result = json.load(response)
            self.record('response', result)
            return result
        except Exception as error:
            self.record('error', f'{type(error).__name__}: {error}')
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

async def execute(command, exchange, stream=False, wake_id=None, **options):
    job = Job(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
    def receive(pipe, live=False):
        chunks = []
        with pipe:
            for line in iter(pipe.readline, b''):
                chunks.append(line)
                if live:
                    output.write(line)
                    output.flush()
                    exchange.record('stream', line.decode('utf-8', errors='replace').rstrip('\r\n'))
        return b''.join(chunks).decode('utf-8', errors='replace')
    output = exchange.path('response', 'jsonl').open('wb') if stream else None
    stdout = asyncio.create_task(asyncio.to_thread(receive, job.process.stdout, stream))
    stderr = asyncio.create_task(asyncio.to_thread(receive, job.process.stderr))
    code = None
    try:
        while job.process.poll() is None:
            if exchange.session.stopped():
                raise asyncio.CancelledError()
            state = exchange.session.load() if wake_id is not None else None
            if state is not None and (state['wake_id'] != wake_id or state['wake_at'] != 0):
                break
            await asyncio.sleep(.05)
        code = job.process.poll()
    finally:
        try:
            job.drain(time.monotonic() + 5)
        finally:
            job.close()
            try:
                result = dict(id=exchange.id, outcome='interrupted' if code is None else 'completed' if code == 0 else 'failed',
                    stdout=await stdout, stderr=await stderr, returncode=code)
                exchange.record('response', {key: value for key, value in result.items() if not stream or key != 'stdout'})
            finally:
                if output:
                    output.close()
    return result

class NotAttempted(ValueError):
    pass

class Bus:
    def __init__(self, session, sender=None):
        self.session, self.sender = session, sender or os.environ.get('TRIDENT_PARTY', 'python')

    def receivers(self):
        return [path.name.split('_', 3)[3].removesuffix('.receiver') for path in self.session.directory.glob('*.receiver')]

    async def request(self, role, operation, arguments):
        exchange = Exchange(self.session, self.sender, role)
        raw = self.session.directory / f'{exchange.id}_to_{role}_request.json'
        reply = self.session.directory / f'{exchange.id}_to_{role}_reply.json'
        job = dict(id=exchange.id, sender=self.sender, op=operation, args=arguments)
        exchange.record('request', job)
        with self.session.mutex.held():
            write(reply, None)
            write(raw, job)
        try:
            while True:
                if self.session.stopped():
                    raise asyncio.CancelledError()
                with self.session.mutex.held():
                    result = json.loads(reply.read_text('utf-8'))
                if result is not None:
                    return result
                await asyncio.sleep(.05)
        finally:
            with self.session.mutex.held():
                raw.unlink(missing_ok=True)
                reply.unlink(missing_ok=True)

    async def serve(self, role, handler):
        marker = self.session.directory / f'{timestamp()}_{role}.receiver'
        write(marker, dict(pid=os.getpid()))
        try:
            while not self.session.stopped():
                for raw in sorted(self.session.directory.glob(f'*_to_{role}_request.json')):
                    try:
                        with self.session.mutex.held():
                            job = json.loads(raw.read_text('utf-8'))
                            raw.unlink()
                    except FileNotFoundError:
                        continue
                    try:
                        result = await handler(job['op'], job['args'])
                    except Exception as error:
                        result = dict(error=f'{type(error).__name__}: {error}')
                    result['operation_id'] = job['id']
                    Exchange(self.session, job['sender'], role, job['id']).record('response', result)
                    reply = self.session.directory / f'{job["id"]}_to_{role}_reply.json'
                    with self.session.mutex.held():
                        if reply.exists():
                            write(reply, result)
                await asyncio.sleep(.05)
        finally:
            marker.unlink(missing_ok=True)
