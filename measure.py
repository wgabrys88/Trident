"""Explicit numerical diagnostics, never a semantic canvas detector."""
import json
from pathlib import Path
from base import Fail, read_file
from desk import unpng
from grounding import record_path, point_pixel


def bounds(value, width, height):
    if not isinstance(value, list) or len(value) != 4 or not all(type(v) is int for v in value):
        raise Fail('bounds must be four integer bitmap pixels')
    x1, y1, x2, y2 = value
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise Fail('bounds outside pinned bitmap')
    return value


class Measure:
    name, flag = 'measure', 'measure_file'
    doc = '''$CALL --measure-file PATH
Numerical body diagnostics, not semantic perception. JSON requires capture (a pinned metadata file), bounds [left,top,right,bottom) in bitmap pixels OR two action_refs grounded by Fara, exclusions (possibly []), and classes [{name,rgb:[r,g,b],tolerance:0}]. Counts are exclusive in class order plus other. Scope is visible_region, never whole canvas. The mind must obtain semantic bounds through Fara; no pixel searches. Invisible content and overlays remain unmeasured.'''

    def run(self, app, path, dry=False):
        spec = json.loads(read_file(path))
        if not all(key in spec for key in ('capture', 'exclusions', 'classes')):
            raise Fail('measure requires pinned capture, exclusions and classes')
        meta_path = Path(spec['capture'])
        if meta_path.name == 'screen.json':
            raise Fail('pin the capture metadata; screen.json is mutable')
        screen = json.loads(read_file(meta_path))
        from hashlib import sha256
        data = Path(screen['image']).read_bytes()
        if not screen.get('sha256') or sha256(data).hexdigest() != screen['sha256']:
            raise Fail('measurement requires matching pinned capture hash')
        pixels, width, height = unpng(data)
        if screen.get('size') != [width, height]:
            raise Fail('measurement capture dimensions disagree')
        if ('bounds' in spec) == ('action_refs' in spec):
            raise Fail('choose bounds or two Fara action_refs')
        if 'action_refs' in spec:
            refs = spec['action_refs']
            if not isinstance(refs, list) or len(refs) != 2:
                raise Fail('two action references required')
            points = []
            for ref in refs:
                record = json.loads(read_file(record_path(app.work, ref)))
                if record['screen']['sha256'] != screen['sha256'] or record['result'].get('coordinate') is None:
                    raise Fail('action reference is not a point in this capture')
                points.extend(point_pixel(record['result']['coordinate'], [width, height]))
            rect = bounds(points, width, height)
        else:
            rect = bounds(spec['bounds'], width, height)
        if not isinstance(spec['exclusions'], list) or not isinstance(spec['classes'], list):
            raise Fail('exclusions and classes must be arrays')
        exclusions = [bounds(box, width, height) for box in spec['exclusions']]
        classes, names = [], {'other'}
        for item in spec['classes']:
            rgb, tolerance, name = item.get('rgb'), item.get('tolerance', 0), item.get('name')
            if not isinstance(name, str) or not name or name in names or not isinstance(rgb, list) or len(rgb) != 3 or not all(type(v) is int and 0 <= v <= 255 for v in rgb) or type(tolerance) is not int or not 0 <= tolerance <= 255:
                raise Fail('invalid or duplicate color class')
            names.add(name)
            classes.append((name, rgb, tolerance))
        if dry:
            return dict(dry=True, bounds=rect, scope='visible_region')
        counts = {name: 0 for name, _, _ in classes} | {'other': 0}
        denominator = excluded = 0
        for y in range(rect[1], rect[3]):
            for x in range(rect[0], rect[2]):
                if any(a <= x < c and b <= y < d for a, b, c, d in exclusions):
                    excluded += 1
                    continue
                denominator += 1
                i = (y * width + x) * 4
                rgb = pixels[i + 2], pixels[i + 1], pixels[i]
                name = next((name for name, color, tol in classes if all(abs(a - b) <= tol for a, b in zip(rgb, color))), 'other')
                counts[name] += 1
        return dict(capture=screen['id'], capture_hash=screen['sha256'], bounds=rect, exclusions=exclusions, excluded=excluded,
                    denominator=denominator, counts=counts, scope='visible_region', limitations='Only explicit visible pixels; overlays included unless excluded; hidden content unknown.')


measure = Measure()
