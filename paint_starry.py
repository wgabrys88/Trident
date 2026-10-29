"""Paint Van Gogh's The Starry Night into the open Windows 11 Paint window."""
import math
import random
import subprocess
import time
from pathlib import Path

import organism

WORK = Path(r'C:\Users\eb-wjt\Downloads\Jarvis\Trident')
SAFE = (612, 286, 1755, 955)  # inside the visible canvas, clear of the PowerShell window
DEADLINE = 1793310562.9878514 - 130
random.seed(1889)

VORTICES = (
    (0.56, 0.30, 0.24, 1.0, 2.4),
    (0.30, 0.18, 0.15, -1.0, 1.5),
    (0.78, 0.20, 0.13, 1.0, 1.35),
    (0.44, 0.50, 0.15, -1.0, 1.15),
    (0.66, 0.46, 0.12, 1.0, 1.1),
)


def log(msg):
    print(msg, flush=True)


def P(u, v):
    left, top, right, bottom = SAFE
    return (left + u * (right - left), top + v * (bottom - top))


def hex_rgb(value):
    value = value.lstrip('#')
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def hex_state():
    out = subprocess.run(
        ['powershell.exe', '-NoProfile', '-STA', '-File', str(WORK / '_focus.ps1')],
        capture_output=True, text=True,
    )
    return ((out.stdout or '') + '\n' + (out.stderr or '')).strip()


def well_colors(app):
    pixels, width, _height = app.desk.grab()
    found = []
    for x, y in ((1275, 116), (1275, 122), (1284, 121), (1266, 121)):
        i = (y * width + x) * 4
        found.append((pixels[i + 2], pixels[i + 1], pixels[i]))
    return found


def close_enough(color, target, tol=28):
    return all(abs(a - b) <= tol for a, b in zip(color, target))


def set_size(size):
    log(f'size {size}')
    out = subprocess.run(
        ['powershell.exe', '-NoProfile', '-STA', '-File', str(WORK / 'paint_set.ps1'), '-Size', str(size), '-Opacity', '100'],
        capture_output=True, text=True,
    )
    text = ((out.stdout or '') + (out.stderr or '')).strip()
    log(text)
    if out.returncode != 0 or 'now=' not in (out.stdout or ''):
        raise SystemExit('could not set brush size')


def set_color(app, hx):
    target = hex_rgb(hx)
    for attempt in range(3):
        state = hex_state()
        if 'RECT' not in state:
            app.desk.click_pixel(1641, 146)
            time.sleep(0.4)
            state = hex_state()
        if 'RECT' not in state:
            log(f'picker closed {hx} try {attempt}')
            continue
        nums = state.split('RECT', 1)[1].split('FOCUS')[0].strip().split(',')
        x, y, w, h = [int(float(v)) for v in nums]
        app.desk.click_pixel(x + w // 2, y + h // 2)
        time.sleep(0.12)
        state = hex_state()
        if 'FOCUS True' not in state:
            log('hex field missed focus')
            app.desk.key('escape')
            time.sleep(0.2)
            continue
        app.desk.key('ctrl+a')
        time.sleep(0.04)
        app.desk.type_text(hx)
        time.sleep(0.16)
        app.desk.key('enter')
        time.sleep(0.28)
        colors = well_colors(app)
        if any(close_enough(color, target, 12) for color in colors):
            log(f'color {hx} {colors[1]}')
            return
        log(f'mismatch {hx} {colors}')
    raise SystemExit(f'could not set {hx}')


def sanitize(pts):
    left, top, right, bottom = SAFE
    out = []
    for x, y in pts:
        x = int(max(left, min(right, round(x))))
        y = int(max(top, min(bottom, round(y))))
        if not out or (x, y) != out[-1]:
            out.append((x, y))
    return out if len(out) >= 2 else None


def paint_many(app, strokes, dwell):
    done = 0
    for pts in strokes:
        if time.time() > DEADLINE:
            log(f'deadline at {done}')
            return done
        clean = sanitize(pts)
        if not clean:
            continue
        app.desk.brush_pixels(clean, dwell=dwell)
        done += 1
        if done % 25 == 0:
            log(f'  strokes {done}')
    log(f'  painted {done}')
    return done


def run_pass(app, hx, size, strokes, dwell=0.004):
    if time.time() > DEADLINE:
        log('stop before pass ' + hx)
        return 0
    log(f'pass {hx} n={len(strokes)}')
    set_size(size)
    set_color(app, hx)
    return paint_many(app, strokes, dwell)


def horizon(u):
    return 0.69 + 0.028 * math.sin(u * 5.2) + 0.016 * math.sin(u * 11.0 + 1.4)


def ground_strokes(v0, v1, step):
    left, top, right, bottom = SAFE
    y = int(top + v0 * (bottom - top))
    y_end = int(top + v1 * (bottom - top))
    strokes = []
    while y < y_end:
        phase = random.random() * math.tau
        amp = random.randint(6, 15)
        pts = []
        x = left
        while x <= right:
            yy = y + math.sin(x * 0.014 + phase) * amp + math.sin(x * 0.0047 + y * 0.01) * 7
            pts.append((x, yy))
            x += random.randint(10, 14)
        strokes.append(pts)
        y += step + random.randint(-1, 2)
    return strokes


def flow(u, v):
    vx, vy = 0.85, 0.16 * math.sin(u * 7.5 + v * 2.4)
    for cx, cy, rad, direc, strength in VORTICES:
        dx, dy = u - cx, (v - cy) * 1.2
        radius = math.hypot(dx, dy) + 0.035
        fall = math.exp(-((radius / rad) ** 2))
        vx += (-dy * direc) / radius * fall * strength
        vy += (dx * direc) / radius * fall * strength
    return vx, vy


def in_sky(u, v):
    return 0.0 <= u <= 1.0 and 0.0 <= v <= horizon(u) + 0.03


def near_vortex(u, v):
    for cx, cy, rad, _direc, _strength in VORTICES:
        if math.hypot(u - cx, (v - cy) * 1.15) < rad * 0.72:
            return True
    return False


def sky_point(bias):
    for _ in range(8):
        if random.random() < bias:
            cx, cy, rad, _d, _s = random.choice(VORTICES)
            ang = random.random() * math.tau
            radius = rad * (random.random() ** 0.6)
            u = cx + math.cos(ang) * radius
            v = cy + math.sin(ang) * radius * 0.72
        else:
            u, v = random.random(), random.uniform(0.02, 0.66)
        if in_sky(u, v):
            return u, v
    return 0.5, 0.3


def flow_strokes(count, length, pred, bias):
    strokes = []
    tries = 0
    while len(strokes) < count and tries < count * 12:
        tries += 1
        u, v = sky_point(bias)
        if not pred(u, v):
            continue
        cu, cv = u, v
        pts = [P(cu, cv)]
        steps = random.randint(7, 13)
        for _step in range(steps):
            vx, vy = flow(cu, cv)
            norm = math.hypot(vx, vy) + 1e-6
            cu += vx / norm * (length / steps) + random.uniform(-0.004, 0.004)
            cv += vy / norm * (length / steps) + random.uniform(-0.003, 0.003)
            if not in_sky(cu, cv):
                break
            pts.append(P(cu, cv))
        if len(pts) >= 4:
            strokes.append(pts)
    return strokes


def band_strokes(v0, v1, step, follow=False):
    strokes = []
    v = v0
    while v < v1:
        pts = []
        u = 0.0
        while u <= 1.0:
            vv = (horizon(u) + (v - 0.70)) if follow else v
            vv += 0.008 * math.sin(u * 18 + v * 20)
            pts.append(P(u, min(0.995, max(0.0, vv))))
            u += 0.011
        strokes.append(pts)
        v += step
    return strokes


def wall_strokes(u, w, top, bottom, gap=8):
    x0, y0 = P(u - w / 2, top)
    x1, y1 = P(u + w / 2, bottom)
    strokes = []
    x = x0
    while x <= x1:
        j = random.randint(-3, 3)
        strokes.append([(x + j, y0), (x, (y0 + y1) / 2 + random.randint(-5, 5)), (x - j, y1)])
        x += gap
    return strokes


def roof_strokes(u, w, eave, roof_h):
    strokes = []
    apex = eave - roof_h
    rows = 7
    for i in range(rows):
        t = i / (rows - 1)
        v = apex + roof_h * t
        half = (w / 2) * t
        if half < 0.004:
            continue
        x0, y = P(u - half, v)
        x1, _y = P(u + half, v)
        strokes.append([(x0, y), ((x0 + x1) / 2, y + random.randint(-1, 1)), (x1, y)])
    return strokes


HOUSES = (
    (0.32, 0.055, 0.11, 0.035),
    (0.39, 0.07, 0.145, 0.04),
    (0.47, 0.05, 0.10, 0.03),
    (0.62, 0.06, 0.13, 0.04),
    (0.70, 0.085, 0.12, 0.045),
    (0.79, 0.055, 0.11, 0.035),
    (0.87, 0.07, 0.15, 0.04),
    (0.94, 0.05, 0.09, 0.03),
)


def house_walls():
    strokes = []
    for u, w, h, _roof in HOUSES:
        strokes.extend(wall_strokes(u, w, 0.98 - h, 0.985))
    strokes.extend(wall_strokes(0.505, 0.09, 0.78, 0.985, gap=7))
    return strokes


def house_roofs():
    strokes = []
    for u, w, h, roof in HOUSES:
        strokes.extend(roof_strokes(u, w * 1.15, 0.98 - h, roof))
    return strokes


def steeple_strokes():
    strokes = []
    v = 0.50
    while v < 0.80:
        t = (v - 0.50) / 0.30
        half = 0.005 + 0.028 * t
        x0, y = P(0.505 - half, v)
        x1, _y = P(0.505 + half, v)
        pts = []
        x = x0
        while x <= x1:
            pts.append((x, y + random.randint(-1, 1)))
            x += 7
        if len(pts) >= 2:
            strokes.append(pts)
        v += 0.011
    tip = P(0.505, 0.455)
    base = P(0.505, 0.51)
    strokes.append([tip, base])
    return strokes


def inside_cypress(u, v, shrink=1.0):
    if v < 0.01 or v > 1.02:
        return False
    t = min(1.0, max(0.0, (v - 0.02) / 0.98))
    center = 0.055 + (1.0 - t) * 0.08
    if t < 0.20:
        center += (0.20 - t) * 0.55
    width = (0.02 + 0.16 * (t ** 0.62)) * shrink
    if t < 0.10:
        width *= t / 0.10
    return abs(u - center) <= width * 0.5


def cypress_strokes(count, shrink, long=False):
    strokes = []
    tries = 0
    while len(strokes) < count and tries < count * 14:
        tries += 1
        u = random.uniform(0.0, 0.30)
        v = random.uniform(0.04, 0.99)
        if not inside_cypress(u, v, shrink):
            continue
        length = random.uniform(0.10, 0.24) if long and random.random() < 0.45 else random.uniform(0.05, 0.13)
        cu, cv = u, v
        pts = []
        for step in range(9):
            cv -= length / 9
            cu += 0.006 + (0.012 if cv < 0.22 else 0.0)
            cu += random.uniform(-0.004, 0.004)
            if step > 2 and not inside_cypress(cu, cv, min(1.0, shrink + 0.25)):
                break
            pts.append(P(cu, cv))
        if len(pts) >= 3:
            strokes.append(pts)
    return strokes


def moon_strokes():
    mx, my = P(0.845, 0.15)
    radius = 54
    strokes = []
    for i in range(-radius, radius + 1, 7):
        for j in range(-radius, radius + 1, 7):
            if i * i + j * j > radius * radius:
                continue
            if (i + 22) ** 2 + j * j < 47 * 47:
                continue
            ang = math.atan2(j, i)
            tx, ty = -math.sin(ang), math.cos(ang)
            span = random.randint(10, 18)
            strokes.append([
                (mx + i - tx * span, my + j - ty * span),
                (mx + i, my + j),
                (mx + i + tx * span, my + j + ty * span),
            ])
    return strokes


def moon_glints():
    mx, my = P(0.845, 0.15)
    strokes = []
    for _ in range(16):
        ang = random.uniform(-1.05, 1.05)
        rad = random.uniform(34, 52)
        x = mx + math.cos(ang) * rad
        y = my + math.sin(ang) * rad
        strokes.append([(x - 7, y), (x + 2, y - 2), (x + 8, y + 2)])
    return strokes


STARS = (
    (0.24, 0.11, 0.85),
    (0.37, 0.16, 0.7),
    (0.51, 0.08, 1.05),
    (0.65, 0.17, 0.72),
    (0.33, 0.31, 0.9),
    (0.46, 0.40, 0.5),
    (0.58, 0.27, 1.2),
    (0.70, 0.38, 0.66),
    (0.42, 0.21, 0.48),
    (0.76, 0.10, 0.58),
    (0.62, 0.08, 0.4),
)


def star_strokes():
    strokes = []
    for u, v, scale in STARS:
        if inside_cypress(u, v, 1.15):
            continue
        x, y = P(u, v)
        rays = 12 if scale >= 0.8 else 8
        for i in range(rays):
            ang = (i / rays) * math.tau + random.uniform(-0.28, 0.28)
            length = (16 + 26 * scale) * random.uniform(0.65, 1.1)
            bend = random.uniform(-8, 8)
            pts = []
            for s in range(6):
                t = s / 5
                rad = 3 + length * t
                pts.append((x + math.cos(ang) * rad, y + math.sin(ang) * rad + bend * t * t))
            strokes.append(pts)
    return strokes


def window_strokes():
    strokes = []
    for u, w, h, _roof in HOUSES:
        top, bottom = 0.98 - h, 0.975
        cols = 2 if w < 0.065 else 3
        for row in range(2):
            for col in range(cols):
                wu = u - w / 2 + w * (col + 1) / (cols + 1)
                wv = top + (bottom - top) * (row + 1) / 3
                x, y = P(wu, wv)
                strokes.append([(x - 5, y), (x + 1, y + 1), (x + 6, y)])
    for v in (0.60, 0.68, 0.75):
        x, y = P(0.505, v)
        strokes.append([(x - 3, y), (x + 4, y + 1)])
    return strokes


def clear_stray(app):
    app.desk.click_pixel(1000, 16)
    time.sleep(0.08)
    pixels, width, _height = app.desk.grab()

    def lum(x, y):
        i = (y * width + x) * 4
        return pixels[i + 2] + pixels[i + 1] + pixels[i]

    if lum(1080, 704) < 600:
        log('undo stray stroke')
        app.desk.key('ctrl+z')
        time.sleep(0.2)


def white_ratio(app):
    pixels, width, _height = app.desk.grab()
    left, top, right, bottom = SAFE
    white = total = 0
    for y in range(top, bottom, 3):
        row = y * width
        for x in range(left, right, 3):
            i = (row + x) * 4
            total += 1
            if pixels[i] > 232 and pixels[i + 1] > 232 and pixels[i + 2] > 232:
                white += 1
    return white / max(1, total)


def main():
    organism.configure(str(WORK), '20261029_220922', '40', '1793308162.9878514', '1793310562.9878514')
    app = organism.App(str(WORK))
    organism.send(organism.LEFT_UP)
    clear_stray(app)
    passes = (
        ('#1b4a96', 32, lambda: ground_strokes(0.0, 0.80, 13), 0.003),
        ('#0c2860', 17, lambda: flow_strokes(110, 0.13, in_sky, 0.75), 0.004),
        ('#4a86d0', 14, lambda: flow_strokes(150, 0.10, in_sky, 0.8), 0.004),
        ('#d7eef8', 8, lambda: flow_strokes(70, 0.055, near_vortex, 0.95), 0.004),
        ('#101c30', 20, lambda: band_strokes(0.66, 1.0, 0.018, follow=True), 0.003),
        ('#1d4460', 12, lambda: band_strokes(0.67, 0.78, 0.02, follow=True), 0.004),
        ('#0c1424', 13, house_walls, 0.004),
        ('#16325c', 11, lambda: house_roofs() + steeple_strokes(), 0.004),
        ('#050706', 20, lambda: cypress_strokes(160, 1.0, long=True), 0.004),
        ('#143222', 11, lambda: cypress_strokes(55, 0.55), 0.004),
        ('#e6b03a', 15, moon_strokes, 0.004),
        ('#ffe38a', 6, lambda: star_strokes() + window_strokes() + moon_glints(), 0.005),
    )
    for hx, size, builder, dwell in passes:
        if time.time() > DEADLINE:
            break
        run_pass(app, hx, size, builder(), dwell)
    organism.send(organism.MOVE, 900, 18, absolute=True)
    ratio = white_ratio(app)
    log(f'white_ratio {ratio:.3f}')
    image = app.desk.grab()
    crop = app.desk.stretch(image, (590, 259, 1782, 982), 900, 546)
    (WORK / 'canvas_done.png').write_bytes(organism.png(crop))
    log('saved canvas_done.png')


if __name__ == '__main__':
    main()
