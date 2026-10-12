import asyncio, ctypes, json, math, os, subprocess, time, tomllib, uuid
from ctypes import wintypes as W
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

async def request(device, op, args, seconds):
    ident, folder = token(), RUN / device
    path, reply = folder / f'{ident}.request', folder / f'{ident}.reply'
    write(path, dict(id=ident, op=op, args=args, expires=time.time() + seconds))
    log('request', id=ident, device=device, op=op, args=args)
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
    log('receipt', id=ident, device=device, result=value)
    return value

async def serve(handler, role):
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
                log('attempt', id=job['id'], device=role, op=job['op'])
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
            if result.get('outcome') == 'unknown':
                os._exit(1)
        await asyncio.sleep(.05)

def object_in(text):
    """Never recover a nested object from a malformed outer candidate."""
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate JSON field')
            value[key] = item
        return value
    values, start, depth, quoted, escaped = [], None, 0, False, False
    for index, char in enumerate(text):
        if start is None:
            if char == '{':
                if text[:index].rstrip().endswith('['):
                    raise ValueError('An action cannot be wrapped in an array')
                start, depth = index, 1
            continue
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                values.append(json.loads(text[start:index+1], object_pairs_hook=unique))
                start = None
    if start is not None or len(values) != 1 or not isinstance(values[0], dict):
        raise ValueError('Exactly one complete JSON object is required')
    return values[0]

def number(value):
    return type(value) in (int, float) and math.isfinite(value)

def box_valid(box):
    return (isinstance(box, list) and len(box) == 4 and all(number(v) for v in box)
            and 0 <= box[0] < box[2] <= 1000 and 0 <= box[1] < box[3] <= 1000)

def keys_in(value):
    keys = value.lower().split('+') if isinstance(value, str) else value
    modifiers = {'ctrl', 'alt', 'shift', 'win'}
    named = {'enter', 'esc', 'escape', 'tab', 'space', 'backspace', 'delete', 'insert',
             'home', 'end', 'pageup', 'pagedown', 'up', 'down', 'left', 'right', 'capslock', 'printscreen'}
    if (not isinstance(keys, list) or not keys or any(not isinstance(k, str) for k in keys)
            or len(set(keys)) != len(keys) or any(k not in modifiers for k in keys[:-1])
            or keys[-1] in modifiers or not (keys[-1] in named or
                 keys[-1] in {f'f{i}' for i in range(1, 25)} or
                 len(keys[-1]) == 1 and keys[-1] in 'abcdefghijklmnopqrstuvwxyz0123456789,./;\'[]\\-=')):
        raise ValueError('Use one real key or modifiers followed by one real key')
    return keys

def validate(decision):
    fields = {'do', 'args', 'expect', 'check', 'compare', 'story', 'questions'}
    if (not isinstance(decision, dict) or not fields <= decision.keys() or decision.keys() - fields - {'corrects'} or
            any(not isinstance(decision[k], str) for k in {'do', 'expect', 'check', 'story'}) or
            decision['do'] not in CFG['capabilities'] or not isinstance(decision['args'], dict) or
            not decision['expect'].strip() or
            len(decision['story']) > CFG['story_chars']):
        raise ValueError('Invalid action fields, prediction, comparison or story')
    comparison = decision['compare']
    if (not isinstance(comparison, dict) or set(comparison) != {'observed', 'contradiction', 'verdict', 'box'} or
            any(not isinstance(comparison[k], str) for k in ('observed', 'contradiction', 'verdict')) or
            comparison['verdict'] not in ('supported', 'refuted', 'uncertain') or
            comparison['box'] is not None and not box_valid(comparison['box'])):
        raise ValueError('Comparison requires observed pixels, verdict, contradiction and a located box or null')
    questions = decision['questions']
    if (not isinstance(questions, dict) or not 1 <= len(questions) <= 20 or
            any(not isinstance(q, dict) or q.get('type') not in ('choice', 'noul', 'score') or
                not isinstance(q.get('instructions'), str) or not q['instructions'].strip() for q in questions.values())):
        raise ValueError('Each of 1..20 Jev questions needs type and instructions (plural), not instruction or options; choices use criteria')
    for q in questions.values():
        criteria = q.get('criteria')
        if (q['type']=='choice' and (not isinstance(criteria,dict) or not 1 <= len(criteria) <= 255) or
                q['type']=='score' and (not isinstance(criteria,list) or not 2 <= len(criteria) <= 10) or
                q['type']=='noul' and criteria is not None and not isinstance(criteria,dict)):
            raise ValueError('Jev choice requires options; score requires ordered levels; noul criteria are optional')
    if any(len(decision[k]) > 1000 for k in ('expect', 'check')):
        raise ValueError('Prediction and visual question must each fit 1000 characters')
    if 'corrects' in decision and (not isinstance(decision['corrects'], str) or
                                  not decision['corrects'].isalnum() or len(decision['corrects'])!=32 or
                                  decision['do'] not in {'launch', 'browse', 'focus', 'key', 'type', 'click', 'draw'}):
        raise ValueError('Only a corrective desktop action can resolve a prior failure')
    schemas = {'look': ({'question'}, {'mode', 'region'}), 'launch': ({'app'}, set()),
               'browse': ({'url'}, set()), 'focus': ({'title'}, set()), 'key': ({'keys', 'window'}, set()),
               'type': ({'text', 'window'}, set()), 'click': ({'target'}, {'points'}),
               'draw': ({'target'}, {'points', 'strokes'}), 'authorize': ({'quote'}, set()),
               'dial': (set(), set()), 'say': ({'text', 'outcome'}, {'evidence'}),
               'done': ({'text', 'outcome'}, {'evidence'}), 'recall': ({'query'}, set()), 'rest': (set(), set())}
    op, args = decision['do'], decision['args']
    required, optional = schemas[op]
    if not required <= args.keys() or args.keys() - required - optional:
        raise ValueError('Wrong arguments for '+op)
    for key, value in args.items():
        if key not in {'keys', 'points', 'strokes', 'region'} and (not isinstance(value, str) or not value.strip()):
            raise ValueError('Argument '+key+' must be nonempty text')
        if isinstance(value, str) and len(value) > 4000:
            raise ValueError('Argument '+key+' exceeds the bounded input size')
    if op in ('say', 'done') and any(not args[k].isalnum() or len(args[k])!=32 for k in ('outcome', 'evidence') if k in args):
        raise ValueError('Narration references must be recorded UUIDs')
    if op == 'key':
        keys_in(args['keys'])
    if op == 'look' and (args.get('mode', 'inspect') not in ('locate', 'inspect') or
                         'region' in args and not box_valid(args['region'])):
        raise ValueError('Invalid visual mode or crop')
    if op in {'launch', 'browse', 'focus', 'key', 'type', 'click', 'draw'} and not decision['check'].strip():
        raise ValueError('A desktop action needs one pointed visual check')
    if op in {'click', 'draw'}:
        if 'points' in args and 'strokes' in args or op == 'draw' and not {'points', 'strokes'} & args.keys():
            raise ValueError('Specify exactly one set of drawing strokes')
        strokes = args.get('strokes', [args.get('points', [[.5, .5]])])
        if (not isinstance(strokes, list) or not strokes or len(strokes) > 100 or
                any(not isinstance(s, list) or not s or len(s) > 200 or
                    any(not isinstance(p, list) or len(p) != 2 or
                        any(not number(v) or not 0 <= v <= 1 for v in p) for p in s) for s in strokes)):
            raise ValueError('Invalid relative X,Y points')
    return decision

def publish_owner(text, source, at=None, ident=None):
    ident = ident or token()
    owner = source in ('local-owner', 'telegram-owner')
    write(ROOT / 'private' / 'inbox' / f'{time.time_ns()}-{ident}.json',
          dict(id=ident, text=text, source=source, at=time.time() if at is None else at, kind='owner' if owner else 'observation'))
    log('owner' if owner else 'input_observation', id=ident, text=text, source=source)
