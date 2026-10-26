import ast, base64, json, math, re, urllib.request
from pathlib import Path

from base import NORM, SET, Fail
from desk import png, unpng

FACTOR, TARGET = 32, 1440
ACTIONS = ('left_click', 'right_click', 'double_click', 'mouse_move', 'left_click_drag', 'key', 'type', 'scroll', 'terminate')


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
    return f'''You see one desktop screenshot. Reply with exactly one action.
The image is {width} by {height} pixels. Coordinates are pixels of this image, measured from the left and from the top.
If the named control is not drawn, terminate and say it is not visible. Do not click a different control.
Use the computer_use function. Available actions: left_click, right_click, double_click, mouse_move, left_click_drag, key, type, scroll, terminate.
A click needs coordinate [x, y]. terminate needs answer, the one sentence you saw.
Return only this:
<tool_call>
{{"name": "computer_use", "arguments": {{"action": "left_click", "coordinate": [x, y]}}}}
</tool_call>'''


def parse_call(raw):
    parts = raw.split('<tool_call>', 1)
    if len(parts) != 2:
        raise Fail('the eye did not return one action')
    blob = parts[1].split('</tool_call>', 1)[0].strip()
    try:
        call = json.loads(blob)
    except json.JSONDecodeError:
        call = ast.literal_eval(blob)
    if not isinstance(call, dict) or not isinstance(call.get('arguments'), dict):
        raise Fail('the eye did not return one action')
    action = call['arguments'].get('action')
    if action not in ACTIONS:
        raise Fail(f'the eye returned an unknown action {action!r}')
    return parts[0].strip(), call['arguments']


def pixel(arguments):
    if 'coordinate' not in arguments:
        return None
    xy = arguments['coordinate']
    if isinstance(xy, str):
        xy = re.findall(r'-?\d+(?:\.\d+)?', xy)
    if not (isinstance(xy, (list, tuple)) and len(xy) == 2):
        raise Fail('the eye returned a bad point')
    return float(xy[0]), float(xy[1])


def to_norm(px, py, width, height):
    return max(0, min(NORM, round(px * NORM / width))), max(0, min(NORM, round(py * NORM / height)))


def program(action, point, arguments):
    if action == 'key':
        keys = arguments.get('keys') or []
        if isinstance(keys, str):
            keys = [keys]
        return 'key ' + '+'.join(str(key).lower() for key in keys)
    if action == 'type':
        return 'type ' + str(arguments.get('text') or '')
    if point is None:
        return ''
    x, y = point
    if action == 'left_click':
        return f'click {x} {y}'
    if action == 'right_click':
        return f'rclick {x} {y}'
    if action == 'double_click':
        return f'dclick {x} {y}'
    if action == 'mouse_move':
        return f'move {x} {y}'
    return ''


class Eye:
    name, flag = 'eye', 'eye_file'
    doc = '''$CALL --eye-file PATH
Reads the whole goal from the file and looks at the latest capture. It returns one point, on a 0-1000 scale of the capture, or one answer.
A first line that is exactly zoom asks again on a crop around the first point.
Ask whether a control is visible before you ask for a click. A click request for something that is not drawn can hit a nearby pixel.
A fact comes back as action terminate and an answer. The point is yours to run, change, or ignore.'''

    def run(self, app, path, dry=False):
        text = read_goal(path)
        zoom = False
        if text.splitlines()[:1] == ['zoom']:
            zoom, text = True, '\n'.join(text.splitlines()[1:]).strip()
        if not text:
            raise Fail('the eye file is empty')
        if dry:
            width, height = fit_size(1920, 1080)
            return dict(dry=True, system=system_text(width, height), sent_size=[width, height], goal=text, zoom=zoom)
        screen, image = load_capture(app)
        if screen.get('blank'):
            return dict(blank=True, capture=screen.get('id'), goal=text)
        seen = ask(app, image, text)
        if not zoom or not seen.get('coordinate'):
            return seen
        box = zoom_box(seen['coordinate'])
        crop = crop_image(app, image, box)
        fine = ask(app, crop, text, screen=screen)
        if not fine.get('coordinate'):
            return seen
        mapped = map_back(box, fine['coordinate'], fine['sent_size'])
        arguments = dict(seen['arguments'])
        arguments['coordinate'] = list(mapped)
        return seen | dict(action=fine['action'], arguments=arguments, coordinate=list(mapped), program=program(fine['action'], mapped, arguments), zoom=box)


def read_goal(path):
    from base import read_file
    return read_file(path).strip()


def load_capture(app):
    path = app.work / 'screen.json'
    if not path.exists():
        raise Fail('there is no capture yet')
    screen = json.loads(path.read_text(encoding='utf-8-sig'))
    return screen, unpng(Path(screen['image']).read_bytes())


def ask(app, image, goal, screen=None):
    screen = screen or json.loads((app.work / 'screen.json').read_text(encoding='utf-8-sig'))
    width, height = fit_size(*image[1:])
    fitted = image if (image[1], image[2]) == (width, height) else app.desk.stretch(image, (0, 0, image[1], image[2]), width, height)
    payload = dict(model=SET['vision_model'], temperature=0, max_tokens=2048, chat_template_kwargs=dict(enable_thinking=False), messages=[
        dict(role='system', content=system_text(width, height)),
        dict(role='user', content=[dict(type='image_url', image_url=dict(url='data:image/png;base64,' + base64.b64encode(png(fitted)).decode())), dict(type='text', text=goal)]),
    ])
    request = urllib.request.Request(SET['vision_url'] + '/v1/chat/completions', json.dumps(payload).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=180) as response:
        body = json.load(response)
    message = body['choices'][0]['message']
    content = (message.get('content') or '').strip()
    if not content:
        raise Fail('the eye returned nothing')
    thoughts, arguments = parse_call(content)
    raw = pixel(arguments)
    point = to_norm(*raw, width, height) if raw else None
    return dict(goal=goal, capture=screen.get('id'), region=screen.get('region'), thoughts=thoughts, action=arguments.get('action'), arguments=arguments, coordinate=list(point) if point else None, answer=arguments.get('answer') or '', program=program(arguments.get('action'), point, arguments), sent_size=[width, height])


def zoom_box(point, span=250):
    left = min(max(point[0] - span // 2, 0), NORM - span)
    top = min(max(point[1] - span // 2, 0), NORM - span)
    return [left, top, left + span, top + span]


def crop_image(app, image, box):
    width, height = image[1:]
    pixels = [(v * (height if i % 2 else width) + NORM // 2) // NORM for i, v in enumerate(box)]
    return app.desk.stretch(image, pixels, TARGET, max(1, round(TARGET * (pixels[3] - pixels[1]) / max(1, pixels[2] - pixels[0]))))


def map_back(box, point, sent_size):
    qx, qy = point
    return [max(0, min(NORM, box[0] + (qx * (box[2] - box[0]) + NORM // 2) // NORM)), max(0, min(NORM, box[1] + (qy * (box[3] - box[1]) + NORM // 2) // NORM))]


eye = Eye()
