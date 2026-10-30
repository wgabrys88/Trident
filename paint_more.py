"""Sharpen the mountains, open the cat's eyes, and close the bare left canvas."""
import math

import organism
import paint_cat as pc

WORK = pc.WORK


def log(msg):
    print(msg, flush=True)


def ellipse(x, y, cx, cy, rx, ry):
    return ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1


def in_cat(x, y):
    if ellipse(x, y, 1130, 660, 120, 125):
        return True
    if ellipse(x, y, 1150, 825, 160, 120):
        return True
    if ellipse(x, y, 1300, 805, 95, 80):
        return True
    return False


def keep(strokes):
    out = []
    for pts in strokes:
        run = []
        for x, y in pts:
            x, y = int(round(x)), int(round(y))
            if in_cat(x, y) or pc.blocked(x, y) or not (pc.L <= x <= pc.R and pc.T <= y <= pc.B):
                if len(run) >= 2:
                    out.append(run)
                run = []
            elif not run or (x, y) != run[-1]:
                run.append((x, y))
        if len(run) >= 2:
            out.append(run)
    return out


def peak(summit, left, right, step=12):
    return keep(pc.triangle_strokes(summit, left, right, step=step))


def cap(summit, left, right):
    sx, sy = summit
    def mix(p):
        return (sx + (p[0] - sx) * 0.32, sy + (p[1] - sy) * 0.32)
    return peak(summit, mix(left), mix(right), step=5)


def edge(a, b, step=10):
    pts = []
    dist = math.hypot(b[0] - a[0], b[1] - a[1])
    n = max(2, int(dist / step))
    for i in range(n + 1):
        t = i / n
        pts.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return keep([pts])


def disk(cx, cy, rx, ry, step=3):
    strokes = []
    y = cy - ry
    while y <= cy + ry:
        span = 1 - ((y - cy) / ry) ** 2
        if span > 0:
            hw = rx * math.sqrt(span)
            strokes.append([(cx - hw, y), (cx, y), (cx + hw, y)])
        y += step
    return strokes


def pupil(cx, cy):
    return [
        [(cx, cy - 8), (cx, cy), (cx, cy + 8)],
        [(cx + 3, cy - 7), (cx + 3, cy + 7)],
        [(cx - 3, cy - 6), (cx - 3, cy + 6)],
    ]


def gutter(y0, y1):
    strokes = []
    y = y0
    while y <= y1:
        strokes.append([(662, y), (700, y), (748, y), (790, y)])
        y += 14
    return strokes


def main():
    organism.configure(str(WORK), '20261029_225413', '40', '1793310853.3588097', '1793313253.3588097')
    app = organism.App(str(WORK))
    organism.send(organism.LEFT_UP)
    app.desk.click_pixel(860, 18)
    far = (
        ((730, 468), (652, 575), (830, 560)),
        ((1040, 430), (900, 575), (1200, 555)),
        ((1320, 400), (1160, 570), (1520, 545)),
        ((1640, 445), (1480, 575), (1830, 550)),
    )
    near = (
        ((820, 545), (652, 730), (1040, 700)),
        ((1280, 520), (1080, 735), (1500, 690)),
        ((1620, 530), (1460, 740), (1830, 680)),
    )
    try:
        pc.run_pass(app, '#24548C', 40, gutter(286, 360), 0.003)
        pc.run_pass(app, '#6E9CC8', 36, gutter(348, 430), 0.003)
        pc.run_pass(app, '#B7CBDC', 34, [s for p in far for s in peak(*p)], 0.003)
        pc.run_pass(app, '#7E97AE', 16, [s for summit, _left, right in far for s in edge(summit, right)], 0.004)
        pc.run_pass(app, '#F7FBFF', 12, [s for p in far for s in cap(*p)], 0.004)
        pc.run_pass(app, '#3E5344', 40, [s for p in near for s in peak(*p)], 0.003)
        pc.run_pass(app, '#C4A36A', 18, [s for summit, left, _right in near for s in edge(left, summit)], 0.004)
        pc.run_pass(app, '#2A3A40', 16, [s for summit, _left, right in near for s in edge(summit, right)], 0.004)
        pc.run_pass(app, '#F4F8FC', 10, [s for p in near for s in cap(*p)], 0.004)
        # Swallow the repeated tree ticks, then set a few trees at different depths.
        pc.run_pass(app, '#4A624C', 26, keep(pc.band(748, 808, 18, 4, 0.4)), 0.003)
        trees = []
        for x, height, base in ((790, 34, 790), (900, 48, 800), (1688, 28, 775), (1760, 42, 805)):
            for i, half in enumerate(range(height // 2, 3, -5)):
                yy = base - i * 5
                trees.append([(x - half, yy), (x, yy - 4), (x + half * 0.7, yy)])
            trees.append([(x, base), (x + 1, base + 14)])
        pc.run_pass(app, '#1A2E22', 10, keep(trees), 0.004)
        eyes = ((1106, 694), (1162, 696))
        pc.run_pass(app, '#2A1A10', 5, [s for cx, cy in eyes for s in disk(cx, cy, 20, 14, 3)], 0.005)
        pc.run_pass(app, '#F7F3EA', 5, [s for cx, cy in eyes for s in disk(cx, cy, 16, 11, 3)], 0.005)
        pc.run_pass(app, '#E2A020', 4, [s for cx, cy in eyes for s in disk(cx, cy + 1, 9, 8, 2)], 0.005)
        pc.run_pass(app, '#140E0A', 3, [s for cx, cy in eyes for s in pupil(cx + 1, cy + 1)], 0.006)
        pc.run_pass(app, '#F7F3EA', 3, [[(cx - 5, cy - 4), (cx - 2, cy - 2)] for cx, cy in eyes], 0.006)
        pc.save_view(app, 'canvas_more.png')
    finally:
        organism.send(organism.LEFT_UP)
        organism.send(organism.MOVE, 900, 18, absolute=True)
        log('refine done')


if __name__ == '__main__':
    main()
