import base64, hashlib, json, math, re, time, urllib.request
from pathlib import Path
from base import NORM, SET, Fail, event
from desk import VK, png, unpng
from state import locked

FACTOR, TARGET = 32, 1440
POINTER = ('left_click', 'right_click', 'double_click', 'triple_click', 'mouse_move', 'left_click_drag')
ACTIONS = POINTER + ('key', 'type', 'scroll', 'hscroll', 'wait', 'terminate')
ALIASES = {'arrowup': 'up', 'arrowdown': 'down', 'arrowleft': 'left', 'arrowright': 'right', 'super': 'win'}


def snap(number, how):
    return int(how(number / FACTOR) * FACTOR)


def fit_size(width, height):
    if min(width, height) <= 0 or max(width, height) / min(width, height) > 200:
        raise Fail('the capture has a bad shape')
    tall, wide = (height * TARGET / width, TARGET) if width > TARGET else (height, width)
    out_h, out_w = max(FACTOR, snap(tall, round)), max(FACTOR, snap(wide, round))
    limit, floor = 4096 * FACTOR ** 2, 256 * FACTOR ** 2
    if out_h * out_w > limit:
        beta = math.sqrt((tall * wide) / limit)
        out_h, out_w = max(FACTOR, snap(tall / beta, math.floor)), max(FACTOR, snap(wide / beta, math.floor))
    elif out_h * out_w < floor:
        beta = math.sqrt(floor / (tall * wide))
        out_h, out_w = snap(tall * beta, math.ceil), snap(wide * beta, math.ceil)
    return out_w, out_h


def system_text(width, height):
    normalized = SET.get('coordinate_mode', 'normalized') == 'normalized'
    units = '0-1000 model-display units on each axis, independently of bitmap dimensions' if normalized else 'pixels of the sent bitmap'
    schema = dict(name='computer_use', description=f'Use a mouse and keyboard to interact with a desktop GUI. Coordinates are {units}. Consult the screenshot and target the center. The bitmap is {width}x{height}.',
                  parameters=dict(type='object', required=['action'], properties={
                      'action': dict(type='string', enum=list(ACTIONS)),
                      'coordinate': dict(type='array', items=dict(type='number'), minItems=2, maxItems=2, description='Required for clicks, mouse_move and left_click_drag. Drag destination; start at current cursor.'),
                      'keys': dict(type='array', items=dict(type='string'), description='Required for key: presses in order, releases in reverse.'),
                      'text': dict(type='string', description='Required for type.'),
                      'pixels': dict(type='number', description=f'Required for scroll/hscroll. Signed distance in {units}, positive up/right, negative down/left.'),
                      'time': dict(type='number', description='Required for wait, seconds (0-60).'),
                      'answer': dict(type='string', description='Required for terminate: observation or limitation.'),
                  }))
    return ('You are Fara, developed by Microsoft AI Frontiers. Inspect one desktop screenshot and return one semantic action or observation. '
            'If the named target is absent or uncertain, terminate with that limitation; do not choose another target. '
            'For an observation request, terminate with your written observation. Do not fabricate missing user information. '
            'Use only the supported desktop subset below; browser/DOM actions are unavailable.\n'
            'You are provided with function signatures within <tools></tools> XML tags:\n<tools>\n' + json.dumps(schema) +
            '\n</tools>\nFor each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:\n'
            '<tool_call>\n{"name": "computer_use", "arguments": {"action": "terminate", "answer": "your observation"}}\n</tool_call>')


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise Fail('the eye returned a non-finite or nonnumeric argument')
    return value


def validate(arguments, width=1000, height=1000):
    action = arguments.get('action')
    if action not in ACTIONS:
        raise Fail(f'unsupported Fara action: {action!r}')
    required = ('coordinate',) if action in POINTER else {
        'key': ('keys',), 'type': ('text',), 'scroll': ('pixels',), 'hscroll': ('pixels',), 'wait': ('time',), 'terminate': ('answer',)
    }[action]
    if not all(key in arguments for key in required):
        raise Fail(f'{action} requires {required}')
    if set(arguments) - {'action', 'thoughts', *required}:
        raise Fail(f'unexpected arguments for {action}')
    if action in POINTER:
        xy = arguments['coordinate']
        if not isinstance(xy, list) or len(xy) != 2 or not all(0 <= number(v) <= bound for v, bound in zip(xy, (width, height))):
            raise Fail('the eye returned a point outside the configured coordinate space')
    elif action == 'key':
        keys = arguments['keys']
        if not isinstance(keys, list) or not keys or not all(isinstance(key, str) and ALIASES.get(key.lower(), key.lower()) in VK for key in keys):
            raise Fail('unsupported key combination')
    elif action in ('type', 'terminate'):
        if not isinstance(arguments[required[0]], str) or (action == 'terminate' and not arguments['answer'].strip()):
            raise Fail(f'{action} requires text')
    elif action in ('scroll', 'hscroll'):
        if abs(number(arguments['pixels'])) > 10000:
            raise Fail('scroll exceeds supported distance')
    elif not 0 <= number(arguments['time']) <= 60:
        raise Fail('wait must be 0-60 seconds')
    return arguments


def parse_call(raw, width=1000, height=1000):
    matches = list(re.finditer(r'<tool_call>(.*?)</tool_call>', raw, re.S))
    if len(matches) != 1 or raw.count('<tool_call>') != 1 or raw.count('</tool_call>') != 1:
        raise Fail('the eye must return exactly one computer_use call')
    try:
        def unique(pairs):
            out = {}
            for key, value in pairs:
                if key in out:
                    raise ValueError('duplicate JSON key')
                out[key] = value
            return out
        call = json.loads(matches[0][1], object_pairs_hook=unique)
    except ValueError as error:
        raise Fail('the eye returned invalid JSON') from error
    if not isinstance(call, dict) or call.get('name') != 'computer_use' or not isinstance(call.get('arguments'), dict):
        raise Fail('the eye must call computer_use')
    return raw[:matches[0].start()].strip(), validate(call['arguments'], width, height)


def to_norm(px, py, width, height):
    return [px * NORM / max(1, width - 1), py * NORM / max(1, height - 1)]


def load_capture(app):
    with locked(app.work / 'capture.lock'):
        path = app.work / 'screen.json'
        if not path.exists():
            raise Fail('there is no capture yet')
        screen = json.loads(path.read_text(encoding='utf-8-sig'))
        data = Path(screen['image']).read_bytes()
        image = unpng(data)
        if list(image[1:]) != screen.get('size'):
            raise Fail('capture metadata and bitmap dimensions disagree')
        digest = hashlib.sha256(data).hexdigest()
        if screen.get('sha256') and screen['sha256'] != digest:
            raise Fail('capture hash mismatch')
        return screen | dict(sha256=digest), image


def ask(app, image, goal, screen=None):
    if screen is None:
        raise Fail('eye requests require bound capture metadata')
    width, height = fit_size(*image[1:])
    fitted = image if image[1:] == (width, height) else app.desk.stretch(image, (0, 0, image[1], image[2]), width, height)
    payload = dict(model=SET['vision_model'], temperature=0, max_tokens=2048, chat_template_kwargs=dict(enable_thinking=False), messages=[
        dict(role='system', content=system_text(width, height)),
        dict(role='user', content=[dict(type='image_url', image_url=dict(url='data:image/png;base64,' + base64.b64encode(png(fitted)).decode())), dict(type='text', text=goal)])])
    started = time.time()
    event('eye_request', dict(goal=goal, capture=screen, sent_size=[width, height], system=payload['messages'][0]['content'], requested_model=SET['vision_model']))
    request = urllib.request.Request(SET['vision_url'] + '/v1/chat/completions', json.dumps(payload).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=180) as response:
        body = json.load(response)
    event('eye_response', body, seconds=time.time() - started)
    content = (body['choices'][0]['message'].get('content') or '').strip()
    if not content:
        raise Fail('the eye returned no content; no action was created')
    mode = SET.get('coordinate_mode', 'normalized')
    thoughts, arguments = parse_call(content, *((width - 1, height - 1) if mode == 'pixels' else (NORM, NORM)))
    point = arguments.get('coordinate')
    point = to_norm(*point, width, height) if point is not None and mode == 'pixels' else point
    from evidence import cost
    scroll_distance = None
    if arguments['action'] in ('scroll', 'hscroll'):
        axis = 0 if arguments['action'] == 'hscroll' else 1
        scroll_distance = arguments['pixels'] * image[axis + 1] / (NORM if mode == 'normalized' else (width, height)[axis])
    return dict(goal=goal, capture=screen['id'], region=screen.get('region'), capture_hash=screen['sha256'], original_size=screen['size'],
                thoughts=thoughts, action=arguments['action'], arguments=arguments, coordinate=point, answer=arguments.get('answer', ''),
                sent_size=[width, height], coordinate_mode=mode, scroll_distance_pixels=scroll_distance, requested_model=SET['vision_model'], reported_model=body.get('model'), **cost(body.get('usage')), program=None)


def zoom_box(point, span=250):
    left, top = [min(max(v - span / 2, 0), NORM - span) for v in point]
    return [left, top, left + span, top + span]


def crop_image(app, image, box):
    width, height = image[1:]
    pixels = [round(v * (height if i % 2 else width) / NORM) for i, v in enumerate(box)]
    return app.desk.stretch(image, pixels, pixels[2] - pixels[0], pixels[3] - pixels[1]), pixels


def map_back(box, point, sent_size=None):
    return [box[i] + point[i] * (box[i + 2] - box[i]) / NORM for i in range(2)]


class Eye:
    name, flag = 'eye', 'eye_file'
    doc = '''$CALL --eye-file PATH
Fara is the only desktop-perception tool: read, view, inspect and analyze screenshots through it. Give one semantic observation or action, naming the window and target. A first line zoom refines a grounded point. Ask if a control is visible before clicking.
Observations and absent/uncertain targets return terminate and written evidence. Executable results return a one-use program: fara ACTION_ID. Copy it unchanged into a hand file. The body alone converts Fara's 0-1000 coordinates, checks a fresh 5x5 patch and geometry, and executes. Do not calculate or revise coordinates. If refused, ask Fara again. No native vision, screenshot reads or mind-written pixel scans. This is a prompt contract, not a security boundary.'''

    def run(self, app, path, dry=False):
        from base import read_file
        text = read_file(path).strip()
        zoom = text.splitlines()[:1] == ['zoom']
        if zoom:
            text = '\n'.join(text.splitlines()[1:]).strip()
        if not text:
            raise Fail('the eye file is empty')
        if dry:
            size = fit_size(1920, 1080)
            return dict(dry=True, system=system_text(*size), sent_size=size, goal=text, zoom=zoom)
        screen, image = load_capture(app)
        if screen.get('blank'):
            return dict(blank=True, capture=screen['id'], program=None)
        seen = ask(app, image, text, screen)
        if zoom and seen.get('coordinate') is not None:
            crop, pixels = crop_image(app, image, zoom_box(seen['coordinate']))
            fine = ask(app, crop, text, screen)
            fine['coarse'] = seen
            fine['zoom_pixels'] = pixels
            if fine.get('coordinate') is not None:
                # 0 and 1000 name the first and last included pixels, matching
                # the hand's endpoint mapping rather than the crop's open end.
                box = [(v - (1 if i >= 2 else 0)) * NORM / max(1, (image[2] if i % 2 else image[1]) - 1) for i, v in enumerate(pixels)]
                fine['coordinate'] = map_back(box, fine['coordinate'])
            seen = fine
        if seen['action'] != 'terminate':
            from grounding import store_action
            seen.update(store_action(app, screen, image, seen))
        return seen


eye = Eye()
