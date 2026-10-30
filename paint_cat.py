"""Oil-paint a cat in front of mountains on the open Windows 11 Paint canvas."""
import math
import random
import subprocess
import sys
import time
from pathlib import Path

import organism

WORK = Path(r'C:\Users\eb-wjt\Downloads\Jarvis\Trident')
# Inner white canvas. Cursor must stay out of the opacity slider.
L, T, R, B = 658, 276, 1836, 964
# Opacity slider hit box, padded by a few pixels.
SLIDER = (669, 432, 718, 834)
DEADLINE = 1793313080.0
random.seed(29)
PHASE = 0.0


def log(msg):
    print(msg, flush=True)


def P(u, v):
    return (L + u * (R - L), T + v * (B - T))


def blocked(x, y):
    x0, y0, x1, y1 = SLIDER
    return x0 <= x <= x1 and y0 <= y <= y1


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
    for x, y in ((1251, 121), (1251, 114), (1244, 121), (1258, 121), (1251, 128)):
        i = (y * width + x) * 4
        found.append((pixels[i + 2], pixels[i + 1], pixels[i]))
    return found


def close_enough(color, target, tol=12):
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
    colors = well_colors(app)
    if any(close_enough(color, target, 12) for color in colors):
        log(f'color kept {hx} {colors[0]}')
        return
    for attempt in range(3):
        state = hex_state()
        if 'RECT' not in state:
            app.desk.click_pixel(1617, 146)
            time.sleep(0.35)
            state = hex_state()
        if 'RECT' not in state:
            log(f'picker closed {hx} try {attempt}')
            continue
        nums = state.split('RECT', 1)[1].split('FOCUS')[0].strip().split(',')
        x, y, w, h = [int(float(v)) for v in nums]
        app.desk.click_pixel(x + w // 2, y + h // 2)
        time.sleep(0.1)
        state = hex_state()
        if 'FOCUS True' not in state:
            log('hex field missed focus')
            app.desk.key('escape')
            time.sleep(0.15)
            continue
        app.desk.key('ctrl+a')
        time.sleep(0.03)
        app.desk.type_text(hx)
        time.sleep(0.12)
        app.desk.key('enter')
        time.sleep(0.45)
        colors = well_colors(app)
        if any(close_enough(color, target, 12) for color in colors):
            log(f'color {hx} {colors[0]}')
            return
        log(f'mismatch {hx} {colors}')
    raise SystemExit(f'could not set {hx}')


def split_run(points):
    strokes, run = [], []
    for x, y in points:
        x, y = int(round(x)), int(round(y))
        if x < L or x > R or y < T or y > B or blocked(x, y):
            if len(run) >= 2:
                strokes.append(run)
            run = []
            continue
        if not run or (x, y) != run[-1]:
            run.append((x, y))
    if len(run) >= 2:
        strokes.append(run)
    return strokes


def paint_many(app, strokes, dwell):
    done = 0
    for pts in strokes:
        if time.time() > DEADLINE:
            log(f'deadline at {done}')
            return done
        parts = split_run(pts)
        for clean in parts:
            app.desk.brush_pixels(clean, dwell=dwell)
            done += 1
            if done % 40 == 0:
                log(f'  strokes {done}')
    log(f'  painted {done}')
    return done


def run_pass(app, hx, size, strokes, dwell=0.003):
    if time.time() > DEADLINE:
        log('stop before ' + hx)
        return 0
    if not strokes:
        log('empty ' + hx)
        return 0
    log(f'pass {hx} n={len(strokes)}')
    set_size(size)
    set_color(app, hx)
    return paint_many(app, strokes, dwell)


def band(y0, y1, step, amp, phase):
    strokes = []
    y = y0
    while y <= y1:
        pts = []
        x = L
        while x <= R:
            yy = y + math.sin(x * 0.017 + phase) * amp + math.sin(x * 0.005 + phase) * amp * 0.35
            pts.append((x, yy))
            x += 11
        strokes.append(pts)
        y += step
    return strokes


def ridge(x, base_v, peaks):
    u = (x - L) / (R - L)
    y = T + base_v * (B - T)
    for cu, hv, wv in peaks:
        d = (u - cu) / wv
        y -= hv * (B - T) * math.exp(-(d * d))
    return y


FAR = ((0.06, 0.11, 0.065), (0.20, 0.17, 0.075), (0.37, 0.06, 0.055), (0.56, 0.19, 0.09), (0.75, 0.14, 0.08), (0.92, 0.10, 0.06))
MID = ((0.0, 0.08, 0.12), (0.24, 0.13, 0.13), (0.50, 0.11, 0.15), (0.76, 0.15, 0.13), (0.98, 0.07, 0.1))


def far_y(x):
    return ridge(x, 0.50, FAR)


def mid_y(x):
    return ridge(x, 0.66, MID)


def meadow_y(x):
    u = (x - L) / (R - L)
    return T + (B - T) * (0.74 + 0.012 * math.sin(u * 9.0) + 0.008 * math.sin(u * 17.0))


def fill_where(pred, y0, y1, step, phase, amp=5):
    strokes = []
    y = y0
    while y <= y1:
        pts = []
        x = L
        while x <= R:
            yy = y + math.sin(x * 0.02 + phase) * amp
            pts.append((x, yy if pred(x, yy) else -9999))
            x += 11
        # break the polyline where the predicate failed
        run = []
        for px, py in pts:
            if py < T:
                if len(run) >= 2:
                    strokes.append(run)
                run = []
            else:
                run.append((px, py))
        if len(run) >= 2:
            strokes.append(run)
        y += step
    return strokes


def along(fn, lift=0, step=12):
    pts = []
    x = L
    while x <= R:
        pts.append((x, fn(x) + lift))
        x += step
    return [pts]


def sun_strokes():
    cx, cy = P(0.18, 0.30)
    strokes = []
    rad = 34
    y = cy - rad
    while y <= cy + rad:
        t = 1 - ((y - cy) / rad) ** 2
        if t > 0:
            hw = rad * math.sqrt(t)
            strokes.append([(cx - hw, y), (cx, y), (cx + hw, y)])
        y += 8
    return strokes


def cloud_strokes():
    puffs = ((0.32, 0.16, 70, 22), (0.48, 0.12, 90, 24), (0.70, 0.18, 80, 20), (0.86, 0.11, 60, 18), (0.14, 0.14, 50, 16))
    strokes = []
    for u, v, rx, ry in puffs:
        cx, cy = P(u, v)
        y = cy - ry
        while y <= cy + ry:
            t = 1 - ((y - cy) / ry) ** 2
            if t > 0:
                hw = rx * math.sqrt(max(0, t))
                strokes.append([(cx - hw, y), (cx, y + random.uniform(-2, 2)), (cx + hw, y)])
            y += 8
    return strokes


def ellipse_strokes(cx, cy, rx, ry, step=7):
    strokes = []
    y = cy - ry
    while y <= cy + ry:
        span = 1 - ((y - cy) / ry) ** 2
        if span > 0:
            hw = rx * math.sqrt(span)
            pts = []
            x = cx - hw
            while x <= cx + hw:
                pts.append((x, y + math.sin(x * 0.05) * 1.5))
                x += 4
            if len(pts) >= 2:
                strokes.append(pts)
        y += step
    return strokes


def side_ellipse(cx, cy, rx, ry, side, step=7):
    strokes = []
    y = cy - ry
    while y <= cy + ry:
        span = 1 - ((y - cy) / ry) ** 2
        if span > 0:
            hw = rx * math.sqrt(span)
            if side < 0:
                x0, x1 = cx - hw, cx - rx * 0.05
            else:
                x0, x1 = cx + rx * 0.05, cx + hw
            if x1 - x0 > 6:
                strokes.append([(x0, y), ((x0 + x1) / 2, y), (x1, y)])
        y += step
    return strokes


def triangle_strokes(a, b, c, step=6):
    ys = [a[1], b[1], c[1]]
    y0, y1 = min(ys), max(ys)
    strokes = []
    y = y0
    while y <= y1:
        xs = []
        for p, q in ((a, b), (b, c), (c, a)):
            y_a, y_b = p[1], q[1]
            if (y_a <= y <= y_b) or (y_b <= y <= y_a):
                if abs(y_b - y_a) < 1e-6:
                    xs.extend((p[0], q[0]))
                else:
                    t = (y - y_a) / (y_b - y_a)
                    xs.append(p[0] + t * (q[0] - p[0]))
        if len(xs) >= 2:
            strokes.append([(min(xs), y), (max(xs), y)])
        y += step
    return strokes


def cat_anchors():
    hx, hy = P(0.40, 0.615)
    return hx, hy


def ear_strokes(hx, hy, inner=False):
    if inner:
        left = ((hx - 34, hy - 78), (hx - 48, hy - 24), (hx - 12, hy - 30))
        right = ((hx + 32, hy - 74), (hx + 10, hy - 28), (hx + 46, hy - 18))
    else:
        left = ((hx - 46, hy - 108), (hx - 70, hy - 18), (hx - 8, hy - 36))
        right = ((hx + 42, hy - 104), (hx + 6, hy - 32), (hx + 68, hy - 12))
    return triangle_strokes(*left, step=5) + triangle_strokes(*right, step=5)


def tail_strokes(hx, hy):
    x0, y0 = hx + 108, hy + 96
    xc, yc = hx + 210, hy + 20
    x1, y1 = hx + 168, hy + 150
    strokes = []
    for offset in range(-18, 19, 6):
        pts = []
        px = py = None
        for i in range(26):
            t = i / 25
            x = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * xc + t ** 2 * x1
            y = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * yc + t ** 2 * y1
            if px is not None:
                dx, dy = x - px, y - py
                norm = math.hypot(dx, dy) or 1
                x += -dy / norm * offset
                y += dx / norm * offset
            pts.append((x, y))
            px, py = x, y
        strokes.append(pts)
    return strokes


def stripe_strokes(hx, hy):
    strokes = []
    # forehead marks
    for ox, tilt in ((-22, -0.4), (0, 0.0), (20, 0.35)):
        pts = []
        for i in range(8):
            t = i / 7
            pts.append((hx + ox + tilt * 18 * t, hy - 36 + t * 28))
        strokes.append(pts)
    # cheek and body bands
    for ox in (-78, -40, -8, 28, 64, 96):
        pts = []
        for i in range(14):
            t = i / 13
            pts.append((hx + ox + math.sin(t * math.pi) * 10, hy + 48 + t * 92))
        strokes.append(pts)
    return strokes


def eye_strokes(hx, hy):
    strokes = []
    for ex, ey, sclera, iris, pupil in (
        (hx - 24, hy - 4, 16, 9, 4),
        (hx + 26, hy - 2, 15, 8, 4),
    ):
        strokes.append(('white', ellipse_strokes(ex, ey, sclera, sclera * 0.72, step=4)))
        strokes.append(('iris', ellipse_strokes(ex + 1, ey + 1, iris, iris * 0.85, step=3)))
        strokes.append(('pupil', ellipse_strokes(ex + 1, ey + 1, pupil, pupil * 1.3, step=3)))
        strokes.append(('white', [[(ex - 5, ey - 5), (ex - 2, ey - 3)]]))
    return strokes


def only(tagged, kind):
    out = []
    for key, part in tagged:
        if key == kind:
            out.extend(part)
    return out


def nose_strokes(hx, hy):
    a = (hx - 8, hy + 16)
    b = (hx + 10, hy + 16)
    c = (hx + 1, hy + 28)
    strokes = triangle_strokes(a, b, c, step=3)
    strokes.append([(hx + 1, hy + 28), (hx - 6, hy + 38), (hx - 14, hy + 42)])
    strokes.append([(hx + 1, hy + 28), (hx + 8, hy + 38), (hx + 16, hy + 42)])
    return strokes


def whisker_strokes(hx, hy):
    origin_l = (hx - 12, hy + 20)
    origin_r = (hx + 14, hy + 20)
    strokes = []
    for ox, oy in (origin_l, origin_r):
        direction = -1 if ox < hx else 1
        for dy, bend in ((-10, -6), (0, 0), (10, 5)):
            strokes.append([
                (ox, oy),
                (ox + direction * 36, oy + dy + bend),
                (ox + direction * 78, oy + dy * 1.4 + bend),
            ])
    return strokes


def tree_strokes():
    spots = (0.08, 0.16, 0.70, 0.84, 0.93)
    strokes = []
    for u in spots:
        x, y = P(u, 0.73)
        height = 26 + int(u * 20)
        for i, half in enumerate(range(height // 2, 2, -4)):
            yy = y - i * 4
            strokes.append([(x - half, yy), (x, yy - 3), (x + half, yy)])
        strokes.append([(x - 2, y), (x, y + 16), (x + 2, y)])
    return strokes


def grass_strokes(front=False):
    hx, hy = cat_anchors()
    strokes = []
    y0 = hy + (150 if front else 70)
    for x in range(L + 8, R - 8, 12 if front else 16):
        if abs(x - hx) < (70 if front else 130) and not front:
            continue
        if front and abs(x - hx) > 90:
            continue
        lean = random.randint(-8, 8)
        tall = random.randint(16, 42 if front else 28)
        strokes.append([(x, min(B - 2, y0 + 8)), (x + lean, y0 - tall)])
    return strokes


def path_strokes():
    return fill_where(
        lambda x, y: meadow_y(x) <= y <= B and in_path(x, y),
        int(meadow_y(L)), B, 16, 1.7, amp=3,
    )


def in_path(x, y):
    v = (y - T) / (B - T)
    t = max(0.0, min(1.0, (v - 0.72) / 0.28))
    cx = L + (R - L) * (0.42 + 0.08 * t)
    half = (R - L) * (0.012 + 0.15 * t)
    return abs(x - cx) <= half


def save_view(app, name):
    image = app.desk.grab()
    crop = app.desk.stretch(image, (633, 259, 1868, 982), 1000, 586)
    path = WORK / name
    path.write_bytes(organism.png(crop))
    log(f'saved {name}')


def main():
    organism.configure(str(WORK), '20261029_225413', '40', '1793310853.3588097', '1793313253.3588097')
    app = organism.App(str(WORK))
    organism.send(organism.LEFT_UP)
    app.desk.click_pixel(860, 18)
    time.sleep(0.1)
    log(f'start {time.strftime("%H:%M:%S")} deadline {time.strftime("%H:%M:%S", time.localtime(DEADLINE))}')
    hx, hy = cat_anchors()
    log(f'cat anchor {hx:.0f},{hy:.0f}')
    try:
        run_pass(app, '#1A3E78', 78, band(T, int(P(0, 0.22)[1]), 34, 7, 0.4))
        run_pass(app, '#4E86B8', 70, band(int(P(0, 0.16)[1]), int(P(0, 0.36)[1]), 30, 8, 1.2))
        run_pass(app, '#E39A58', 64, band(int(P(0, 0.30)[1]), int(P(0, 0.46)[1]), 28, 7, 2.1))
        run_pass(app, '#F6D7A0', 56, band(int(P(0, 0.40)[1]), int(P(0, 0.54)[1]), 24, 6, 0.7))
        run_pass(app, '#FFE6A8', 28, sun_strokes(), 0.004)
        run_pass(app, '#F0C48A', 26, cloud_strokes(), 0.004)
        save_view(app, 'canvas_sky.png')
        run_pass(app, '#8EABC4', 58, fill_where(lambda x, y: far_y(x) <= y <= mid_y(x) + 36, int(min(far_y(x) for x in range(L, R, 40))), int(P(0, 0.62)[1]), 24, 0.3))
        run_pass(app, '#617890', 30, along(far_y, lift=18), 0.004)
        run_pass(app, '#F4F7FB', 16, along(far_y, lift=2), 0.004)
        run_pass(app, '#4A624C', 52, fill_where(lambda x, y: mid_y(x) <= y <= meadow_y(x) + 28, int(P(0, 0.50)[1]), int(P(0, 0.80)[1]), 22, 1.1))
        run_pass(app, '#C4A06A', 24, fill_where(lambda x, y: mid_y(x) <= y <= mid_y(x) + 36 and ((x - L) / (R - L)) < 0.7, int(P(0, 0.48)[1]), int(P(0, 0.74)[1]), 16, 0.6), 0.004)
        run_pass(app, '#6E8A3A', 52, fill_where(lambda x, y: y >= meadow_y(x), int(P(0, 0.70)[1]), B, 22, 0.2))
        run_pass(app, '#D4B45A', 30, fill_where(lambda x, y: meadow_y(x) + 10 <= y <= meadow_y(x) + 70, int(P(0, 0.74)[1]), int(P(0, 0.86)[1]), 16, 1.4), 0.004)
        run_pass(app, '#C4A06A', 36, path_strokes(), 0.003)
        run_pass(app, '#1E3324', 12, tree_strokes(), 0.004)
        run_pass(app, '#2A2418', 20, grass_strokes(False), 0.004)
        save_view(app, 'canvas_land.png')
        # Cat, back to front. Light comes from the left.
        run_pass(app, '#2A2418', 22, ellipse_strokes(hx + 16, hy + 168, 100, 22, step=6), 0.004)
        run_pass(app, '#3A2416', 20, ellipse_strokes(hx + 12, hy + 108, 132, 98, step=8) + ear_strokes(hx, hy) + tail_strokes(hx, hy), 0.004)
        run_pass(app, '#C4843C', 18, ellipse_strokes(hx + 8, hy + 112, 116, 82, step=7) + ellipse_strokes(hx, hy, 74, 62, step=6) + tail_strokes(hx, hy), 0.004)
        run_pass(app, '#8A4E28', 14, side_ellipse(hx + 8, hy + 112, 116, 82, 1, step=7) + side_ellipse(hx, hy, 74, 62, 1, step=6), 0.004)
        run_pass(app, '#E8B878', 12, side_ellipse(hx + 4, hy + 108, 100, 70, -1, step=7) + side_ellipse(hx - 6, hy, 64, 52, -1, step=6), 0.004)
        run_pass(app, '#3A2416', 8, stripe_strokes(hx, hy), 0.004)
        run_pass(app, '#F2D2B4', 12, ellipse_strokes(hx + 2, hy + 124, 48, 46, step=5) + ellipse_strokes(hx + 2, hy + 16, 30, 22, step=4), 0.004)
        run_pass(app, '#E7A0A0', 8, ear_strokes(hx, hy, inner=True), 0.004)
        eyes = eye_strokes(hx, hy)
        run_pass(app, '#F4F0E8', 6, only(eyes, 'white'), 0.005)
        run_pass(app, '#E2A020', 5, only(eyes, 'iris'), 0.005)
        run_pass(app, '#1A120C', 4, only(eyes, 'pupil'), 0.005)
        run_pass(app, '#C45A58', 5, nose_strokes(hx, hy), 0.005)
        run_pass(app, '#F4F0E8', 3, whisker_strokes(hx, hy), 0.005)
        run_pass(app, '#3F4E2C', 8, grass_strokes(True), 0.004)
        save_view(app, 'canvas_cat.png')
    finally:
        organism.send(organism.LEFT_UP)
        organism.send(organism.MOVE, 900, 18, absolute=True)
        log(f'end {time.strftime("%H:%M:%S")}')


if __name__ == '__main__':
    main()
