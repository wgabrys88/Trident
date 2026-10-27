"""Deterministic Fara receipts and fresh-image guards. No semantic pixel search."""
import json, re, time, uuid
from pathlib import Path
from base import NORM, SET, Fail, event
from state import json_file, locked


def record_path(work, action_id):
    if not re.fullmatch(r'[0-9a-f]{32}', action_id):
        raise Fail('invalid Fara action ID')
    return Path(work) / 'actions' / (action_id + '.json')


def point_pixel(point, size):
    return [round(value * (length - 1) / NORM) for value, length in zip(point, size)]


def patch(image, point):
    pixels, width, height = image
    x, y = point_pixel(point, (width, height))
    box = [max(0, x - 2), max(0, y - 2), min(width, x + 3), min(height, y + 3)]
    sample = b''.join(pixels[(row * width + box[0]) * 4:(row * width + box[2]) * 4] for row in range(box[1], box[3]))
    return dict(box=box, bgra=sample.hex())


def target_window(screen, point):
    x, y = point_pixel(point, screen['size'])
    left, top, _, _ = screen['box']
    for window in screen['windows']:
        box = window.get('pixel_box')
        if box and box[0] <= x + left < box[2] and box[1] <= y + top < box[3]:
            return {key: window[key] for key in ('hwnd', 'pid', 'pixel_box')}
    return None


def foreground(screen):
    return next(({key: window[key] for key in ('hwnd', 'pid', 'pixel_box')} for window in screen['windows'] if window.get('foreground')), None)


def store_action(app, screen, image, seen):
    required = ('id', 'sha256', 'size', 'box', 'screen_size', 'region', 'windows', 'cursor', 'cursor_pixel')
    if any(key not in screen for key in required):
        raise Fail('capture lacks action provenance; take a new capture before grounding')
    point = seen.get('coordinate')
    point = point if point is not None else screen['cursor']
    if not all(0 <= value <= NORM for value in point) or (seen['action'] == 'left_click_drag' and not all(0 <= value <= NORM for value in screen['cursor'])):
        raise Fail('cursor lies outside capture; request a Fara mouse_move first')
    action_id = uuid.uuid4().hex
    record = dict(id=action_id, run=SET['run'], created=time.time(), state='ready', screen=screen, result=seen,
                  point=point, patch=patch(image, point), target_window=target_window(screen, point), foreground=foreground(screen))
    if seen['action'] == 'left_click_drag':
        record['start_patch'] = patch(image, screen['cursor'])
    json_file(record_path(app.work, action_id), record)
    event('action_stored', record)
    return dict(action_id=action_id, program='fara ' + action_id)


def execute(desk, action_id):
    from eye import ALIASES, load_capture
    from types import SimpleNamespace
    with locked(desk.work / 'hand.lock'):
        path = record_path(desk.work, action_id)
        if not path.exists():
            raise Fail('unknown Fara action')
        record = json.loads(path.read_text(encoding='utf-8'))
        if record['state'] != 'ready' or record['run'] != SET['run']:
            raise Fail('stale or consumed Fara action')
        if (desk.work / 'stop').exists() or (SET.get('wake_end') and time.time() >= float(SET['wake_end'])):
            raise Fail('wake stopped or deadline reached')
        desk.capture()
        fresh, image = load_capture(SimpleNamespace(work=desk.work))
        original = record['screen']
        for key in ('region', 'box', 'size', 'screen_size'):
            if fresh.get(key) != original[key]:
                raise Fail('Fara geometry mismatch: ' + key)
        if target_window(fresh, record['point']) != record['target_window']:
            raise Fail('Fara target window geometry changed')
        if patch(image, record['point']) != record['patch']:
            raise Fail('Fara patch mismatch: fresh pixels changed')
        result, args = record['result'], record['result']['arguments']
        action = result['action']
        if action in ('key', 'type', 'scroll', 'hscroll') and (record['foreground'] is None or foreground(fresh) != record['foreground']):
            raise Fail('Fara foreground window changed or unknown')
        if action in ('left_click_drag', 'scroll', 'hscroll'):
            if fresh['cursor_pixel'] != original['cursor_pixel']:
                raise Fail('Fara drag cursor provenance changed')
        if action == 'left_click_drag' and patch(image, original['cursor']) != record['start_patch']:
            raise Fail('Fara drag start pixels changed')
        if (desk.work / 'stop').exists() or (SET.get('wake_end') and time.time() >= float(SET['wake_end'])):
            raise Fail('wake stopped or deadline reached during guard')
        record.update(state='consumed', consumed_at=time.time(), fresh_capture=fresh['id'])
        json_file(path, record)  # Consume before the first possible input event.
        event('action_begin', dict(id=action_id, fresh_capture=fresh['id']))
        desk.action_box = original['box']  # No reread of mutable region during input.
        try:
            point = result.get('coordinate')
            if action in ('left_click', 'right_click', 'double_click', 'triple_click'):
                desk.click(*point, right=action == 'right_click', times={'double_click': 2, 'triple_click': 3}.get(action, 1))
            elif action == 'mouse_move':
                desk.move(*point)
            elif action == 'left_click_drag':
                desk.drag([original['cursor'], point])
            elif action == 'key':
                desk.key('+'.join(ALIASES.get(key.lower(), key.lower()) for key in args['keys']))
            elif action == 'type':
                desk.type_text(args['text'])
            elif action in ('scroll', 'hscroll'):
                # Win32 wheel units, not pixel displacement: one detent is 120.
                # Scale Fara's signed model-display distance to region pixels,
                # then 40 pixels/detent (explicit approximation, app-dependent).
                axis = 0 if action == 'hscroll' else 1
                distance = result['scroll_distance_pixels']
                desk.wheel(round(distance * 120 / 40), horizontal=axis == 0)
            elif action == 'wait':
                time.sleep(args['time'])
            else:
                raise Fail('unsupported executable Fara action')
        except BaseException as error:
            event('action_failed', dict(id=action_id, error=str(error)))
            raise
        finally:
            del desk.action_box
        event('action_complete', dict(id=action_id))
