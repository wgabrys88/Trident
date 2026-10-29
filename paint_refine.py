"""A second sitting: impasto on the vortex, a real steeple, and stars as painted orbs."""
import math
import random
import time

import organism
import paint_starry as s

random.seed(1890)


def disk(u, v, radius, count, span):
    x, y = s.P(u, v)
    strokes = []
    for _ in range(count):
        ang = random.random() * math.tau
        rad = radius * math.sqrt(random.random())
        cx = x + math.cos(ang) * rad
        cy = y + math.sin(ang) * rad
        tang = ang + math.pi / 2 + random.uniform(-0.4, 0.4)
        strokes.append([
            (cx - math.cos(tang) * span, cy - math.sin(tang) * span),
            (cx, cy),
            (cx + math.cos(tang) * span, cy + math.sin(tang) * span),
        ])
    return strokes


def halos():
    strokes = []
    for u, v, scale in s.STARS:
        if s.inside_cypress(u, v, 1.15):
            continue
        x, y = s.P(u, v)
        ring = 18 + 16 * scale
        for i in range(9):
            ang = (i / 9) * math.tau + random.uniform(-0.2, 0.2)
            pts = []
            for k in range(4):
                a = ang + (k / 3 - 0.5) * 0.7
                rad = ring * random.uniform(0.88, 1.08)
                pts.append((x + math.cos(a) * rad, y + math.sin(a) * rad))
            strokes.append(pts)
    return strokes


def star_bodies():
    strokes = []
    for u, v, scale in s.STARS:
        if s.inside_cypress(u, v, 1.15):
            continue
        strokes.extend(disk(u, v, 11 + 8 * scale, 16, 7 + 4 * scale))
    return strokes


def star_cores():
    strokes = []
    for u, v, scale in s.STARS:
        if s.inside_cypress(u, v, 1.15):
            continue
        strokes.extend(disk(u, v, 5 + 3 * scale, 8, 4))
    return strokes


def steeple():
    strokes = []
    v = 0.48
    while v <= 0.83:
        t = (v - 0.48) / 0.35
        half = 0.006 + 0.046 * t
        x0, y = s.P(0.50 - half, v)
        x1, _y = s.P(0.50 + half, v)
        pts = []
        x = x0
        while x <= x1:
            pts.append((x, y + random.randint(-1, 1)))
            x += 7
        if len(pts) >= 2:
            strokes.append(pts)
        v += 0.010
    edge_l, edge_r = [], []
    v = 0.48
    while v <= 0.83:
        t = (v - 0.48) / 0.35
        half = 0.006 + 0.046 * t
        edge_l.append(s.P(0.50 - half, v))
        edge_r.append(s.P(0.50 + half, v))
        v += 0.02
    strokes.extend((edge_l, edge_r, [s.P(0.50, 0.42), s.P(0.50, 0.50)]))
    return strokes


def hook():
    strokes = []
    for _ in range(18):
        jitter = random.uniform(-0.015, 0.015)
        pts = []
        for step in range(8):
            t = step / 7
            u = 0.10 + 0.11 * math.sin(t * math.pi * 0.85) + jitter
            v = 0.035 + 0.07 * t
            pts.append(s.P(u, v))
        strokes.append(pts)
    return strokes


def main_vortex(u, v):
    return math.hypot(u - 0.56, (v - 0.30) * 1.15) < 0.22 and s.in_sky(u, v)


def edges(y_values):
    strokes = []
    for y in y_values:
        strokes.append([(x, y) for x in range(598, 1772, 12)])
    return strokes


def paint_raw(app, strokes, dwell=0.003):
    left, top, right, bottom = 590, 262, 1772, 976
    done = 0
    for pts in strokes:
        clean = []
        for x, y in pts:
            x = int(max(left, min(right, round(x))))
            y = int(max(top, min(bottom, round(y))))
            if not clean or (x, y) != clean[-1]:
                clean.append((x, y))
        if len(clean) >= 2:
            app.desk.brush_pixels(clean, dwell=dwell)
            done += 1
    s.log(f'  painted {done}')
    return done


def main():
    organism.configure(str(s.WORK), '20261029_220922', '40', '1793308162.9878514', '1793310562.9878514')
    app = organism.App(str(s.WORK))
    organism.send(organism.LEFT_UP)
    if time.time() > s.DEADLINE:
        s.log('too late to refine')
        return
    s.run_pass(app, '#0a2048', 16, s.flow_strokes(70, 0.05, main_vortex, 1.0), 0.004)
    s.run_pass(app, '#9ec4e6', 13, s.flow_strokes(60, 0.04, main_vortex, 1.0), 0.004)
    s.run_pass(app, '#080c14', 16, steeple(), 0.004)
    s.run_pass(app, '#050706', 14, hook(), 0.004)
    s.run_pass(app, '#eef6fb', 8, halos(), 0.004)
    s.run_pass(app, '#ffe38a', 11, star_bodies(), 0.004)
    s.run_pass(app, '#fff6d8', 7, star_cores(), 0.004)
    s.log('edges')
    s.set_size(26)
    s.set_color(app, '#1b4a96')
    paint_raw(app, edges((268, 276)))
    s.set_color(app, '#101c30')
    paint_raw(app, edges((966, 976)))
    organism.send(organism.MOVE, 900, 18, absolute=True)
    image = app.desk.grab()
    crop = app.desk.stretch(image, (584, 250, 1788, 990), 960, 592)
    (s.WORK / 'canvas_done.png').write_bytes(organism.png(crop))
    s.log('refined')


if __name__ == '__main__':
    main()
