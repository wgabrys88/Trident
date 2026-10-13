import asyncio, ctypes, hashlib, json, os, subprocess, time, tomllib, uuid
from ctypes import wintypes as W
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFG = tomllib.loads((ROOT/'organism.toml').read_text('utf-8-sig'))
ROLES = ('manipulator', 'retina', 'phonation', 'relay', 'cochlea', 'discern')

def token():
    return uuid.uuid4().hex

def write(path, value):
    pending = path.with_name(path.name+'.'+token()+'.pending')
    pending.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    deadline = time.monotonic()+2
    while True:
        try:
            pending.replace(path)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.05)

class Session:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.timestamp = self.directory.name.removeprefix('run_')
        self.state_path = self.file('state.json')

    @classmethod
    def create(cls):
        session = cls(ROOT/('run_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%fZ')))
        session.directory.mkdir()
        os.environ['TRIDENT_RUN'] = str(session.directory)
        session.save(dict(memory=dict(goal='', narrative=''),
            owner=dict(id=token(), text='', source='no-owner-goal', at=time.time()),
            previous=[], outcomes={}, unknown={}, pixels={}, voice={}, screen={},
            active=True, process_state='running', run=str(session.directory)))
        return session

    @classmethod
    def attach(cls):
        return cls(os.environ['TRIDENT_RUN'])

    def file(self, name):
        return self.directory/(self.timestamp+'_'+name)

    def artifact(self, kind, extension):
        return self.file(f'{kind}_{token()}.{extension}')

    def load(self):
        return json.loads(self.state_path.read_text('utf-8'))

    def save(self, state):
        write(self.state_path, state)

    def inbox(self):
        return sorted(self.directory.glob(self.timestamp+'_inbox_*.json'))

    def interrupted(self):
        return self.file('stop').exists() or any(
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
        write(profile/'cli-config.json', dict(version=1, editor=dict(vimMode=False),
            permissions=dict(allow=['Read(**)', 'Write(**)', 'Shell(*)', 'WebFetch(*)', 'Mcp(*:*)'], deny=[]),
            sandbox=dict(mode='disabled', readBoundary='system'), approvalMode='unrestricted'))
        write(profile/'mcp.json', dict(mcpServers=dict(trident=dict(type='stdio', command=os.sys.executable,
            args=['-B', str(ROOT/'mind.py'), '--mcp']))))
        key = os.environ.get('OPENROUTER_API_KEY') or (ROOT.parent/'.env').read_text('utf-8-sig').strip()
        if not key:
            raise ValueError('OpenRouter credentials are required')
        os.environ['OPENROUTER_API_KEY'] = key

class NotAttempted(ValueError):
    pass

class ProcessGuard:
    def __init__(self):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, W.BOOL, W.LPCWSTR]
        self.kernel.CreateMutexW.restype = W.HANDLE
        self.kernel.CloseHandle.argtypes = [W.HANDLE]
        name = 'Local\\Trident-'+hashlib.sha256(str(ROOT).casefold().encode()).hexdigest()
        self.handle = self.kernel.CreateMutexW(None, False, name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error()==183:
            self.close()
            raise RuntimeError('Trident is already running in this workspace')

    def close(self):
        if self.handle:
            if not self.kernel.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None

class Job:
    """Own a process tree before its suspended root starts executing."""
    def __init__(self, argv, **options):
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        class Basic(ctypes.Structure):
            _fields_ = [('times', ctypes.c_int64*2), ('flags', W.DWORD),
                ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t),
                ('active', W.DWORD), ('affinity', ctypes.c_size_t),
                ('priority', W.DWORD), ('scheduling', W.DWORD)]
        class Limits(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', ctypes.c_uint64*6), ('memory', ctypes.c_size_t*4)]
        k.CreateJobObjectW.restype = W.HANDLE
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, W.LPCWSTR]
        k.SetInformationJobObject.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD]
        k.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
        k.CloseHandle.argtypes = [W.HANDLE]
        self.kernel, self.handle, self.process = k, k.CreateJobObjectW(None, None), None
        try:
            limits = Limits(); limits.basic.flags = 0x2000
            if not self.handle or not k.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.process = subprocess.Popen(argv, creationflags=subprocess.CREATE_NO_WINDOW|4, **options)
            if not k.AssignProcessToJobObject(self.handle, W.HANDLE(int(self.process._handle))):
                raise ctypes.WinError(ctypes.get_last_error())
            resume = ctypes.WinDLL('ntdll').NtResumeProcess
            resume.argtypes, resume.restype = [W.HANDLE], ctypes.c_long
            if resume(W.HANDLE(int(self.process._handle)))!=0:
                raise OSError('Could not resume owned process')
        except Exception:
            if self.process is not None:
                self.process.kill()
            self.close()
            raise

    def close(self):
        if self.handle:
            if not self.kernel.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None

def execute(argv, seconds, **options):
    job = Job(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
    try:
        output, errors = job.process.communicate(timeout=seconds)
        if job.process.returncode:
            raise RuntimeError(errors.decode(errors='replace'))
        return output
    except subprocess.TimeoutExpired:
        raise TimeoutError('Process deadline exceeded; outcome unknown') from None
    finally:
        job.close()

class Bus:
    def __init__(self, session):
        self.session = session

    def receivers(self):
        prefix = self.session.timestamp+'_'
        return [path.name.removeprefix(prefix).removesuffix('.receiver')
            for path in self.session.directory.glob(prefix+'*.receiver')]

    async def request(self, role, operation, arguments, seconds):
        if not self.session.file(role+'.receiver').exists():
            return dict(outcome='not_attempted', error='No registered receiver '+role, receivers=self.receivers())
        ident = token(); path = self.session.file(f'{role}_{ident}.request')
        reply = path.with_suffix('.reply')
        write(path, dict(id=ident, op=operation, args=arguments, expires=time.time()+seconds))
        try:
            async with asyncio.timeout(seconds):
                while True:
                    try:
                        return json.loads(reply.read_text('utf-8'))
                    except (FileNotFoundError, PermissionError):
                        await asyncio.sleep(.05)
        except TimeoutError:
            try:
                path.rename(path.with_suffix('.cancelled'))
                outcome = 'not_attempted'
            except FileNotFoundError:
                outcome = 'unknown'
            return dict(error=f'{role} deadline exceeded', outcome=outcome, verified=False, request_id=ident)

    async def serve(self, handler, role):
        receiver = self.session.file(role+'.receiver')
        write(receiver, dict(pid=os.getpid()))
        try:
            while True:
                for path in sorted(self.session.directory.glob(self.session.timestamp+f'_{role}_*.request')):
                    try:
                        claimed = path.with_suffix('.active'); path.rename(claimed)
                    except FileNotFoundError:
                        continue
                    job = json.loads(claimed.read_text('utf-8')); claimed.unlink()
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
                    result['request_id'] = job['id']; write(path.with_suffix('.reply'), result)
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
