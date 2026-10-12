import asyncio, json, os, subprocess, sys, time, tomllib, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFG = tomllib.loads((ROOT / 'organism.toml').read_text('utf-8'))
RUN = Path(os.environ.get('TRIDENT_RUN', ROOT / 'private'))

def token():
    return uuid.uuid4().hex

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(token() + '.pending')
    pending.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    pending.replace(path)

def log(event, **data):
    RUN.mkdir(parents=True, exist_ok=True)
    with (RUN / f'{os.getpid()}.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(dict(at=time.time(), event=event) | data, ensure_ascii=False) + '\n')

async def request(device, op, args, seconds):
    ident = token()
    folder = RUN / device
    reply = folder / f'{ident}.reply'
    write(folder / f'{ident}.request', dict(op=op, args=args, expires=time.time() + seconds))
    log('request', id=ident, device=device, op=op, args=args)
    try:
        async with asyncio.timeout(seconds):
            while not reply.exists():
                await asyncio.sleep(.05)
    except TimeoutError:
        (folder / f'{ident}.request').unlink(missing_ok=True)
        log('deadline', id=ident, device=device, op=op, outcome='unknown')
        raise
    value = json.loads(reply.read_text('utf-8'))
    reply.unlink()
    log('receipt', id=ident, device=device, result=value)
    return value

async def serve(handler, role):
    log('ready', device=role)
    while True:
        for path in sorted((RUN / role).glob('*.request'), key=lambda p: p.stat().st_mtime_ns):
            job = json.loads(path.read_text('utf-8'))
            path.unlink()
            try:
                if time.time() >= job['expires']:
                    raise TimeoutError('Expired before execution; not attempted')
                async with asyncio.timeout(job['expires'] - time.time()):
                    result = await handler(job['op'], job['args'])
            except Exception as error:
                result = dict(error=f'{type(error).__name__}: {error}', verified=False,
                              outcome='unknown' if isinstance(error, TimeoutError) else 'failed')
            write(path.with_suffix('.reply'), result)
            if result.get('outcome') == 'unknown':
                os._exit(1)  # A timed-out thread must never perform later side effects.
        await asyncio.sleep(.05)

def execute(argv, seconds, **options):
    with subprocess.Popen(argv, creationflags=subprocess.CREATE_NO_WINDOW,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options) as process:
        try:
            output, errors = process.communicate(timeout=seconds)
        except subprocess.TimeoutExpired:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=5)
            raise TimeoutError('Process timed out; side effects unknown') from None
        if process.returncode:
            raise RuntimeError(errors.decode(errors='replace')[-1500:])
        return output

def object_in(text):
    decoder, values, index = json.JSONDecoder(), [], 0
    while index < len(text):
        start = text.find('{', index)
        if start < 0:
            break
        try:
            value, index = decoder.raw_decode(text, start)
            if isinstance(value, dict):
                values.append(value)
        except ValueError:
            index = start + 1
    if len(values) != 1:
        raise ValueError('Exactly one JSON object is required')
    return values[0]

def publish_owner(text, source):
    ident = token()
    write(ROOT / 'private' / 'inbox' / f'{time.time_ns()}-{ident}.json',
          dict(id=ident, text=text[:4000], source=source, at=time.time()))
    log('owner', id=ident, text=text, source=source)
