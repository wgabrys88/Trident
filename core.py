import asyncio, ctypes, hashlib, json, os, subprocess, time, tomllib, uuid
import urllib.error, urllib.request
from ctypes import wintypes as W
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFG = tomllib.loads((ROOT/'organism.toml').read_text('utf-8-sig'))
ROLES = ('manipulator', 'retina', 'phonation', 'relay', 'cochlea', 'discern')

def token(): return uuid.uuid4().hex

def timestamp(): return datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%fZ')

def store(path, data):
    pending = path.with_name(path.name+'.'+token()+'.pending')
    pending.write_bytes(data)
    deadline = time.monotonic()+2
    while True:
        try:
            pending.replace(path)
            return
        except PermissionError:
            if time.monotonic() >= deadline: raise
            time.sleep(.05)

def write(path, value):
    store(path, json.dumps(value, ensure_ascii=False).encode('utf-8'))

class Session:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.state_path = self.file('state.json')

    @classmethod
    def create(cls):
        session = cls(ROOT/('run_'+timestamp()))
        session.directory.mkdir()
        os.environ['TRIDENT_RUN'] = str(session.directory)
        session.file('tools.py').write_text('', 'utf-8')
        session.save(dict(memory=dict(goal='', narrative=''),
            owner=dict(id=token(), text='', source='no-owner-goal', at=time.time()),
            previous=[], outcomes={}, unknown={}, screen={},
            active=True, process_state='running', run=str(session.directory)))
        return session

    @classmethod
    def attach(cls): return cls(os.environ['TRIDENT_RUN'])

    def file(self, name): return next(self.directory.glob('*_'+name), self.directory/(timestamp()+'_'+name))

    def artifact(self, kind, extension): return self.directory/f'{timestamp()}_{kind}_{token()}.{extension}'

    def load(self): return json.loads(self.state_path.read_text('utf-8'))

    def save(self, state):
        write(self.state_path, state)

    def inbox(self): return sorted(self.directory.glob('*_inbox_*.json'))

    def interrupted(self):
        return any(self.directory.glob('*_stop')) or any(
            json.loads(path.read_text('utf-8'))['kind']=='owner' for path in self.inbox())

    def prepare(self):
        vendor = self.file('vendor'); vendor.mkdir()
        home, temporary = vendor/'home', vendor/'temp'
        profile = home/'.cursor'
        profile.mkdir(parents=True); temporary.mkdir()
        os.environ.update(HOME=str(home), USERPROFILE=str(home),
            CURSOR_CONFIG_DIR=str(profile), CURSOR_DATA_DIR=str(vendor/'cursor-data'),
            CURSOR_AGENT_DISABLE_DEBUG_LOG='1', HF_HOME=str(vendor/'hf'),
            XDG_CACHE_HOME=str(vendor/'cache'), XDG_CONFIG_HOME=str(vendor/'config'),
            TEMP=str(temporary), TMP=str(temporary), PYTHONDONTWRITEBYTECODE='1')
        os.environ['PSModuleAnalysisCachePath'] = str(vendor/'PowerShellModuleAnalysisCache')
        write(profile/'cli-config.json', dict(version=1, editor=dict(vimMode=False),
            permissions=dict(allow=['Read(**)', 'Write(**)', 'Shell(*)', 'WebFetch(*)', 'Mcp(*:*)'],
                deny=['Read(**/*.png)', 'Read(**/*.jpg)', 'Read(**/*.jpeg)', 'Read(**/*.gif)', 'Read(**/*.webp)', 'Read(**/*.bmp)']),
            sandbox=dict(mode='disabled', readBoundary='system'), approvalMode='unrestricted'))
        write(profile/'mcp.json', dict(mcpServers=dict(trident=dict(type='stdio', command=os.sys.executable,
            args=['-B', str(ROOT/'mind.py'), '--mcp'], env=dict(TRIDENT_RUN=str(self.directory),
            PYTHONDONTWRITEBYTECODE='1', TEMP=str(temporary), TMP=str(temporary))))))
        os.environ['OPENROUTER_API_KEY'] = os.environ.get('OPENROUTER_API_KEY') or (ROOT.parent/'.env').read_text('utf-8-sig').strip()

class NotAttempted(ValueError):
    pass

class Exchange:
    """Native boundary bytes, correlated without inventing provider messages."""
    def __init__(self, session, sender, receiver, ident=None):
        self.session, self.sender, self.receiver = session, sender, receiver
        self.ident = ident or token()

    def path(self, direction, extension):
        sender, receiver = (self.receiver, self.sender) if direction!='request' else (self.sender, self.receiver)
        return self.session.directory/f'{timestamp()}_{sender}_to_{receiver}_{self.ident}_{direction}.{extension}'

    def dump(self, direction, data, extension='json'):
        path = self.path(direction, extension)
        store(path, data if isinstance(data, bytes) else data.encode('utf-8'))
        return path

    def http(self, url, payload, seconds, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.dump('request', body)
        request = urllib.request.Request(url, body, headers or {'Content-Type':'application/json'})
        output = bytearray()
        try:
            try:
                response = urllib.request.urlopen(request, timeout=seconds)
            except urllib.error.HTTPError as error:
                response = error
            with response, self.path('response', 'json').open('wb') as destination:
                while chunk := response.read1(65536):
                    destination.write(chunk); destination.flush(); output.extend(chunk)
                if response.status>=400:
                    raise RuntimeError(f'HTTP {response.status}: '+output.decode('utf-8', errors='replace'))
            return json.loads(output)
        except Exception as error:
            self.dump('error', f'{type(error).__name__}: {error}', 'txt')
            raise

kernel = ctypes.WinDLL('kernel32', use_last_error=True)
for name, arguments, result in (
    ('CreateMutexW', [ctypes.c_void_p, W.BOOL, W.LPCWSTR], W.HANDLE),
    ('CreateJobObjectW', [ctypes.c_void_p, W.LPCWSTR], W.HANDLE),
    ('SetInformationJobObject', [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD], W.BOOL),
    ('AssignProcessToJobObject', [W.HANDLE, W.HANDLE], W.BOOL),
    ('CloseHandle', [W.HANDLE], W.BOOL)):
    function = getattr(kernel, name); function.argtypes, function.restype = arguments, result

def checked(value):
    if not value: raise ctypes.WinError(ctypes.get_last_error())
    return value

class ProcessGuard:
    def __init__(self):
        name = 'Local\\Trident-'+hashlib.sha256(str(ROOT).casefold().encode()).hexdigest()
        self.handle = checked(kernel.CreateMutexW(None, False, name))
        if ctypes.get_last_error()==183:
            self.close(); raise RuntimeError('Trident is already running in this workspace')

    def close(self):
        if self.handle:
            checked(kernel.CloseHandle(self.handle)); self.handle = None

class Job:
    """Assign the suspended root to a kill-on-close job before resuming it."""
    def __init__(self, argv, **options):
        class Basic(ctypes.Structure):
            _fields_ = [('times', ctypes.c_int64*2), ('flags', W.DWORD),
                ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t), ('active', W.DWORD),
                ('affinity', ctypes.c_size_t), ('priority', W.DWORD), ('scheduling', W.DWORD)]
        class Limits(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', ctypes.c_uint64*6), ('memory', ctypes.c_size_t*4)]
        self.handle, self.process = checked(kernel.CreateJobObjectW(None, None)), None
        try:
            limits = Limits(); limits.basic.flags = 0x2000
            checked(kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
            flags = options.pop('creationflags', subprocess.CREATE_NO_WINDOW)
            self.process = subprocess.Popen(argv, creationflags=flags|4, **options)
            checked(kernel.AssignProcessToJobObject(self.handle, W.HANDLE(int(self.process._handle))))
            resume = ctypes.WinDLL('ntdll').NtResumeProcess
            resume.argtypes, resume.restype = [W.HANDLE], ctypes.c_long
            if resume(W.HANDLE(int(self.process._handle)))!=0: raise OSError('Could not resume owned process')
        except Exception:
            if self.process:
                self.process.kill()
            self.close(); raise

    def close(self):
        if self.handle:
            checked(kernel.CloseHandle(self.handle)); self.handle = None

async def run_process(argv, seconds, exchange, extension='txt', **options):
    output, errors = exchange.path('response', extension), exchange.path('stderr', 'txt')
    job = None
    try:
        with output.open('wb') as out, errors.open('wb') as err:
            job = Job(argv, stdout=out, stderr=err, **options)
            async with asyncio.timeout(seconds):
                while job.process.poll() is None:
                    await asyncio.sleep(.05)
            if job.process.returncode:
                raise RuntimeError(errors.read_text('utf-8', errors='replace') or f'Process exit {job.process.returncode}')
        return output.read_bytes()
    except BaseException as error:
        exchange.dump('error', f'{type(error).__name__}: {error}; completion not established', 'txt')
        raise
    finally:
        if job:
            job.close()

class Bus:
    def __init__(self, session, sender=None):
        self.session, self.sender = session, sender or os.environ.get('TRIDENT_PARTY', 'python')

    def receivers(self):
        return [path.name.split('_', 3)[3].removesuffix('.receiver')
            for path in self.session.directory.glob('*.receiver')]

    async def request(self, role, operation, arguments, seconds):
        exchange = Exchange(self.session, self.sender, role); ident = exchange.ident
        job = dict(id=ident, sender=self.sender, op=operation, args=arguments, expires=time.time()+seconds)
        raw = exchange.dump('request', json.dumps(job, ensure_ascii=False))
        if any(self.session.directory.glob('*_stop')) or role not in self.receivers():
            raw.rename(raw.with_name(raw.stem+'.cancelled.json'))
            result = dict(outcome='not_attempted', error='No available receiver '+role, receivers=self.receivers())
            exchange.dump('response', json.dumps(result, ensure_ascii=False))
            return result
        path = raw
        try:
            async with asyncio.timeout(seconds):
                while True:
                    try:
                        reply = next(self.session.directory.glob(f'*_{role}_to_{self.sender}_{ident}_response.json'))
                        return json.loads(reply.read_text('utf-8'))
                    except (StopIteration, FileNotFoundError, PermissionError, json.JSONDecodeError):
                        await asyncio.sleep(.05)
        except (TimeoutError, asyncio.CancelledError) as error:
            try:
                path.rename(path.with_name(path.stem+'.cancelled.json'))
                outcome = 'not_attempted'
            except FileNotFoundError:
                outcome = 'unknown'
            result = dict(error=f'{role} request interrupted' if isinstance(error, asyncio.CancelledError) else f'{role} deadline exceeded',
                          outcome=outcome, verified=False, request_id=ident)
            exchange.dump('error', json.dumps(result, ensure_ascii=False))
            if isinstance(error, asyncio.CancelledError): raise
            return result

    async def serve(self, handler, role):
        receiver = self.session.file(role+'.receiver')
        write(receiver, dict(pid=os.getpid()))
        try:
            while True:
                for path in sorted(self.session.directory.glob(f'*_to_{role}_*_request.json')):
                    try:
                        claimed = path.with_name(path.stem+'.active.json'); path.rename(claimed)
                    except FileNotFoundError:
                        continue
                    job = json.loads(claimed.read_text('utf-8'))
                    if time.time()>=job['expires']:
                        result = dict(error='Deadline expired in queue', outcome='not_attempted', verified=False)
                    else:
                        try:
                            async with asyncio.timeout(job['expires']-time.time()):
                                result = await handler(job['op'], job['args'])
                            result.setdefault('outcome', 'failed' if result.get('error') else 'completed')
                        except Exception as error:
                            result = dict(error=f'{type(error).__name__}: {error}', verified=False,
                                outcome='not_attempted' if isinstance(error, NotAttempted) else
                                        'failed' if isinstance(error, ValueError) else 'unknown')
                    result['request_id'] = job['id']
                    Exchange(self.session, job['sender'], role, job['id']).dump('response', json.dumps(result, ensure_ascii=False))
                    if result['outcome']=='unknown':
                        receiver.unlink(missing_ok=True)
                        os._exit(1)
                await asyncio.sleep(.05)
        finally:
            receiver.unlink(missing_ok=True)

    def publish_owner(self, text, source, at=None, ident=None):
        ident = ident or token()
        write(self.session.file(f'inbox_{time.time_ns()}_{ident}.json'),
            dict(id=ident, text=text, source=source, at=time.time() if at is None else at,
                 kind='owner' if source in ('local-owner', 'telegram-owner', 'telegram-call') else 'observation'))
