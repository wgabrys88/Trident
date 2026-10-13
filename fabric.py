import asyncio, ctypes, json, os, subprocess, time, tomllib, uuid
from ctypes import wintypes as W
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFG = tomllib.loads((ROOT / 'organism.toml').read_text('utf-8-sig'))
RUN = Path(os.environ.get('TRIDENT_RUN', ROOT / 'private'))

def openrouter_key():
    key = (ROOT.parent / '.env').read_text('utf-8-sig').strip()
    if not key:
        raise ValueError('The parent .env must contain the OpenRouter key')
    return key

def token():
    return uuid.uuid4().hex

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(token() + '.pending')
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

def log(event, **data):
    RUN.mkdir(parents=True, exist_ok=True)
    with (RUN / f'{os.getpid()}.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(dict(at=time.time(), event=event) | data, ensure_ascii=False) + '\n')

class NotAttempted(ValueError):
    pass

class Job:
    """Assign a suspended process to a kill-on-close job before resuming it."""
    def __init__(self, argv, **options):
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        class Basic(ctypes.Structure):
            _fields_ = [('times', ctypes.c_int64 * 2), ('flags', W.DWORD),
                       ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t),
                       ('active', W.DWORD), ('affinity', ctypes.c_size_t),
                       ('priority', W.DWORD), ('scheduling', W.DWORD)]
        class Limits(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', ctypes.c_uint64 * 6), ('memory', ctypes.c_size_t * 4)]
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
            self.process = subprocess.Popen(argv, creationflags=subprocess.CREATE_NO_WINDOW | 4, **options)
            if not k.AssignProcessToJobObject(self.handle, W.HANDLE(int(self.process._handle))):
                raise ctypes.WinError(ctypes.get_last_error())
            resume = ctypes.WinDLL('ntdll').NtResumeProcess
            resume.argtypes, resume.restype = [W.HANDLE], ctypes.c_long
            if resume(W.HANDLE(int(self.process._handle))) != 0:
                raise OSError('Could not resume owned process')
        except Exception:
            if self.process is not None:
                self.process.kill()
            self.close()
            raise

    def close(self):
        if self.handle:
            if not self.kernel.CloseHandle(self.handle):
                log('cleanup_failed', error='Could not close process job', code=ctypes.get_last_error())
            else:
                self.handle = None

def execute(argv, seconds, transcript=None, **options):
    job = Job(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
    try:
        output, errors = job.process.communicate(timeout=seconds)
        if transcript:
            transcript.write_bytes(output)
            transcript.with_suffix('.stderr').write_bytes(errors)
        if job.process.returncode:
            raise RuntimeError(errors.decode(errors='replace'))
        return output
    except subprocess.TimeoutExpired as error:
        if transcript:
            transcript.write_bytes(error.output or b'')
            transcript.with_suffix('.stderr').write_bytes(error.stderr or b'')
        raise TimeoutError('Process deadline exceeded; outcome unknown') from None
    finally:
        job.close()

async def request(device, op, args, seconds):
    ident, folder = token(), RUN / device
    if not folder.is_dir():
        return dict(outcome='not_attempted',error='No registered receiver '+device,receivers=[p.name for p in RUN.iterdir() if p.is_dir()])
    path, reply = folder / f'{ident}.request', folder / f'{ident}.reply'
    write(path, dict(id=ident, op=op, args=args, expires=time.time() + seconds))
    try:
        async with asyncio.timeout(seconds):
            while True:
                try:
                    value = json.loads(reply.read_text('utf-8'))
                    break
                except (FileNotFoundError, PermissionError):
                    await asyncio.sleep(.05)
    except TimeoutError:
        try:
            path.rename(path.with_suffix('.cancelled'))
            outcome = 'not_attempted'
        except FileNotFoundError:
            outcome = 'unknown'
        log('deadline', id=ident, device=device, op=op, outcome=outcome)
        return dict(error=f'{device} deadline exceeded', outcome=outcome, verified=False, request_id=ident)
    return value

async def serve(handler, role):
    (RUN/role).mkdir(parents=True,exist_ok=True)
    log('ready', device=role)
    while True:
        for path in sorted((RUN / role).glob('*.request'), key=lambda p: p.name):
            try:
                claimed = path.with_suffix('.active')
                path.rename(claimed)
            except FileNotFoundError:
                continue
            path = claimed
            job = json.loads(path.read_text('utf-8'))
            path.unlink()
            if time.time() >= job['expires']:
                result = dict(error='Deadline expired in queue; operation not attempted', outcome='not_attempted', verified=False)
            else:
                try:
                    async with asyncio.timeout(job['expires'] - time.time()):
                        result = await handler(job['op'], job['args'])
                    result.setdefault('outcome', 'completed' if not result.get('error') else 'failed')
                except Exception as error:
                    result = dict(error=f'{type(error).__name__}: {error}', verified=False,
                                  reason=str(error) if isinstance(error, ValueError) else 'The device stopped without a confirmed outcome; its details are recorded',
                                  outcome='not_attempted' if isinstance(error, NotAttempted) else
                                          'failed' if isinstance(error, ValueError) else 'unknown')
            result['request_id'] = job['id']
            write(path.with_suffix('.reply'), result)
            log('operation', id=job['id'], device=role, op=job['op'], outcome=result['outcome'], receipt=str(path.with_suffix('.reply')))
            if result.get('outcome') == 'unknown':
                os._exit(1)
        await asyncio.sleep(.05)

def publish_owner(text, source, at=None, ident=None):
    ident = ident or token()
    owner = source in ('local-owner', 'telegram-owner', 'telegram-call')
    write(ROOT / 'private' / 'inbox' / f'{time.time_ns()}-{ident}.json',
          dict(id=ident, text=text, source=source, at=time.time() if at is None else at, kind='owner' if owner else 'observation'))
    log('owner' if owner else 'input_observation', id=ident, text=text, source=source)
