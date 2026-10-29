"""Break the flat cypress bar, seat the village, and turn the stars into oil orbs."""
import math
import random

import organism
import paint_starry as s

random.seed(1891)


def rows(x0, x1, y0, y1, step):
    strokes = []
    y = y0
    while y <= y1:
        pts = []
        x = x0
        while x <= x1:
            pts.append((x, y + math.sin(x * 0.04 + y) * 2))
            x += 10
        strokes.append(pts)
        y += step
    return strokes


def flame():
    strokes = []
    for _ in range(32):
        x0 = random.randint(690, 830)
        y0 = random.randint(430, 540)
        x1 = random.randint(740, 860)
        y1 = random.randint(340, 420)
        x2 = random.randint(760, 900)
        y2 = random.randint(278, 350)
        pts = []
        for step in range(8):
            t = step / 7
            bend = (1 - t) ** 2
            x = bend * x0 + 2 * (1 - t) * t * x1 + t * t * x2
            y = bend * y0 + 2 * (1 - t) * t * y1 + t * t * y2
            pts.append((x, y))
        strokes.append(pts)
    return strokes


def orbs():
    strokes = []
    for u, v, scale in s.STARS:
        if s.inside_cypress(u, v, 1.05):
            continue
        x, y = s.P(u, v)
        radius = 15 + 7 * scale
        for i in range(16):
            ang = (i / 16) * math.tau + random.uniform(-0.2, 0.2)
            rad = radius * random.uniform(0.15, 1.0)
            cx = x + math.cos(ang) * rad
            cy = y + math.sin(ang) * rad
            tang = ang + math.pi / 2
            span = 6 + 3 * scale
            strokes.append([
                (cx - math.cos(tang) * span, cy - math.sin(tang) * span),
                (cx, cy),
                (cx + math.cos(tang) * span, cy + math.sin(tang) * span),
            ])
    return strokes


def cores():
    strokes = []
    for u, v, scale in s.STARS:
        if s.inside_cypress(u, v, 1.05):
            continue
        x, y = s.P(u, v)
        for _ in range(6):
            ang = random.random() * math.tau
            span = 4 + 2 * scale
            strokes.append([(x, y), (x + math.cos(ang) * span, y + math.sin(ang) * span)])
    return strokes


def main():
    organism.configure(str(s.WORK), '20261029_220922', '40', '1793308162.9878514', '1793310562.9878514')
    app = organism.App(str(s.WORK))
    organism.send(organism.LEFT_UP)
    if time_left() < 160:
        s.log('too late')
        return
    s.log('erase bar')
    s.set_size(18)
    s.set_color(app, '#1b4a96')
    s.paint_many(app, rows(650, 845, 300, 372, 11), 0.003)
    s.set_size(11)
    s.set_color(app, '#4a86d0')
    s.paint_many(app, rows(680, 830, 312, 360, 16), 0.003)
    s.log('curl the cypress')
    s.set_size(13)
    s.set_color(app, '#07140e')
    from paint_refine import paint_raw
    paint_raw(app, flame(), 0.004)
    s.log('ground the village')
    s.set_size(16)
    s.set_color(app, '#0c1424')
    paint_raw(app, rows(640, 1765, 948, 978, 12), 0.003)
    s.run_pass(app, '#ffd24a', 16, orbs(), 0.004)
    s.run_pass(app, '#fff6d0', 8, cores(), 0.004)
    organism.send(organism.MOVE, 900, 18, absolute=True)
    image = app.desk.grab()
    (s.WORK / 'canvas_done.png').write_bytes(organism.png(app.desk.stretch(image, (584, 250, 1788, 990), 960, 592)))
    s.log('fixed')


def time_left():
    import time
    return s.DEADLINE - time.time()


if __name__ == '__main__':
    main()
