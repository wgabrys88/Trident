"""Turn the flat sunset into an oil landscape: shaded peaks, a broken sky, fur on the cat."""
import math
import random
import time

import organism
import paint_cat as pc
import paint_more as pm

random.seed(29)
STOP = 1793314629.0  # 23:57:09, two minutes before the wake ends
FAR = (
    (670, 575), (730, 500), (770, 448), (830, 530), (900, 575),
    (980, 490), (1060, 418), (1140, 500), (1210, 545),
    (1290, 430), (1360, 388), (1440, 470), (1510, 555),
    (1590, 455), (1660, 418), (1740, 500), (1830, 560),
)
NEAR = (
    (670, 730), (750, 640), (830, 548), (930, 660), (1040, 730),
    (1180, 700), (1280, 545), (1380, 500), (1480, 640),
    (1580, 545), (1670, 515), (1760, 640), (1830, 710),
)


def log(msg):
    print(msg, flush=True)


def y_at(sil, x):
    if x <= sil[0][0]:
        return sil[0][1]
    if x >= sil[-1][0]:
        return sil[-1][1]
    for (x0, y0), (x1, y1) in zip(sil, sil[1:]):
        if x0 <= x <= x1:
            t = (x - x0) / max(1.0, x1 - x0)
            return y0 + t * (y1 - y0)
    return sil[-1][1]


def peak_xs(sil):
    found = []
    for i, (x, y) in enumerate(sil):
        left = min(p[1] for p in sil[max(0, i - 1):i]) if i else 10_000
        right = min(p[1] for p in sil[i + 1:i + 2]) if i + 1 < len(sil) else 10_000
        if y <= left and y <= right:
            found.append(x)
    return found or [sil[len(sil) // 2][0]]


def lit(x, peaks):
    nearest = min(peaks, key=lambda q: abs(q - x))
    return x <= nearest


def sun_disk(x, y):
    return (x - 870) ** 2 / 52 ** 2 + (y - 482) ** 2 / 46 ** 2 <= 1


def hits_cat(x, y, pad):
    if pm.in_cat(x, y):
        return True
    if pad <= 0:
        return False
    return pm.in_cat(x - pad, y) or pm.in_cat(x + pad, y) or pm.in_cat(x, y - pad) or pm.in_cat(x, y + pad)


def safe(strokes, pad=0, keep_sun=True):
    out = []
    for pts in strokes:
        run = []
        for x, y in pts:
            x, y = int(round(x)), int(round(y))
            blocked = (
                hits_cat(x, y, pad)
                or pc.blocked(x, y)
                or not (pc.L <= x <= pc.R and pc.T <= y <= pc.B)
                or (keep_sun and sun_disk(x, y))
            )
            if blocked:
                if len(run) >= 2:
                    out.append(run)
                run = []
            elif not run or (x, y) != run[-1]:
                run.append((x, y))
        if len(run) >= 2:
            out.append(run)
    return out


def fill_under(sil, y_bottom, want_lit, step=8, x_step=9, pad=12):
    peaks = peak_xs(sil)
    y = int(min(p[1] for p in sil))
    x0, x1 = int(sil[0][0]), int(sil[-1][0])
    strokes = []
    while y <= y_bottom:
        run = []
        x = x0
        while x <= x1:
            top = y_at(sil, x) + 4 * math.sin(x * 0.031)
            if y >= top and lit(x, peaks) == want_lit:
                run.append((x, y))
            else:
                if len(run) >= 2:
                    strokes.append(run)
                run = []
            x += x_step
        if len(run) >= 2:
            strokes.append(run)
        y += step
    return safe(strokes, pad=pad)


def snow(sil, pad=8):
    peaks = peak_xs(sil)
    strokes = []
    for px in peaks:
        for dx in range(-34, 35, 8):
            x = px + dx
            top = y_at(sil, x)
            reach = 22 * (1 - abs(dx) / 40)
            if reach < 6:
                continue
            strokes.append([(x, top + 2), (x, top + reach)])
    return safe(strokes, pad=pad)


def sky(y0, y1, step, phase, amp):
    strokes = []
    y = y0
    while y <= y1:
        run = []
        x = pc.L + 6
        while x <= pc.R - 6:
            yy = y + math.sin(x * 0.012 + phase) * amp + math.sin(x * 0.005) * amp * 0.4
            if yy < y_at(FAR, x) - 8:
                run.append((x, yy))
            else:
                if len(run) >= 2:
                    strokes.append(run)
                run = []
            x += 11
        if len(run) >= 2:
            strokes.append(run)
        y += step
    return safe(strokes, pad=10)


def meadow(y0, y1, step, phase):
    strokes = []
    y = y0
    while y <= y1:
        run = []
        x = pc.L + 4
        while x <= pc.R - 4:
            yy = y + math.sin(x * 0.02 + phase) * 5
            if yy > y_at(NEAR, x) + 8 and not pc.in_path(x, yy):
                run.append((x, yy))
            else:
                if len(run) >= 2:
                    strokes.append(run)
                run = []
            x += 10
        if len(run) >= 2:
            strokes.append(run)
        y += step
    return safe(strokes, pad=14)


def grass():
    strokes = []
    for x in range(pc.L + 16, pc.R - 10, 14):
        for k in range(3):
            base = 790 + k * 52 + (x * 3) % 11
            lean = ((x // 14) % 7) - 3
            tall = 16 + (x + k * 5) % 18
            strokes.append([(x, base), (x + lean, base - tall)])
    return safe(strokes, pad=16)


def gutter():
    strokes = []
    y = 286
    while y <= 450:
        strokes.append([(661, y), (666, y)])
        y += 8
    return strokes


def blue_gap(app):
    pixels, width, _height = app.desk.grab()

    def rgb(x, y):
        i = (y * width + x) * 4
        return pixels[i + 2], pixels[i + 1], pixels[i]

    strokes = []
    for y in range(455, 575, 9):
        run = []
        for x in range(900, 1160, 9):
            if hits_cat(x, y, 8) or pc.blocked(x, y) or sun_disk(x, y):
                if len(run) >= 2:
                    strokes.append(run)
                run = []
                continue
            if y > y_at(FAR, x) + 18:
                if len(run) >= 2:
                    strokes.append(run)
                run = []
                continue
            red, green, blue = rgb(x, y)
            pale = blue > red + 10 and 150 < blue < 235 and red > 130 and green > 150
            if pale:
                run.append((x, y))
            else:
                if len(run) >= 2:
                    strokes.append(run)
                run = []
        if len(run) >= 2:
            strokes.append(run)
    return strokes


def fur():
    strokes = {'#C47A3C': [], '#8A4A28': [], '#E7C7A4': [], '#4A2A18': []}
    body = ((1130, 655, 95, 108, 70), (1150, 820, 145, 105, 90))
    for cx, cy, rx, ry, count in body:
        for i in range(count):
            ang = (i * 2.399) % math.tau
            span = 0.25 + (i % 5) * 0.1
            x = cx + math.cos(ang) * rx * span
            y = cy + math.sin(ang) * ry * span
            if (x - 1105) ** 2 / 22 ** 2 + (y - 695) ** 2 / 16 ** 2 <= 1:
                continue
            if (x - 1155) ** 2 / 22 ** 2 + (y - 697) ** 2 / 16 ** 2 <= 1:
                continue
            dx = math.cos(ang) * 14
            dy = abs(math.sin(ang)) * 12 + 6
            key = '#E7C7A4' if 1088 < x < 1205 and 745 < y < 860 else ('#8A4A28' if i % 4 == 0 else '#C47A3C')
            strokes[key].append([(x, y), (x + dx * 0.4, y + dy * 0.5), (x + dx, y + dy)])
    for ox in (-70, -32, 8, 46, 82):
        pts = []
        for i in range(12):
            t = i / 11
            pts.append((1150 + ox + math.sin(t * math.pi) * 12, 760 + t * 110))
        strokes['#4A2A18'].append(pts)
    return strokes


def trees():
    strokes = []
    for x, height, base in ((800, 36, 785), (1688, 46, 800), (1768, 64, 845)):
        for i, half in enumerate(range(height // 2, 3, -4)):
            yy = base - i * 5
            strokes.append([(x - half, yy), (x, yy - 5), (x + int(half * 0.65), yy)])
        strokes.append([(x, base), (x + 1, base + 16)])
    return safe(strokes, pad=8)


def main():
    pc.DEADLINE = STOP
    organism.configure(str(pc.WORK), '20261029_231909', '40', '1793312349.6254058', '1793314749.6254058')
    app = organism.App(str(pc.WORK))
    organism.send(organism.LEFT_UP)
    app.desk.click_pixel(900, 14)
    time.sleep(0.15)
    log(f'start {time.strftime("%H:%M:%S")}')
    hx, hy = pc.cat_anchors()
    try:
        pc.run_pass(app, '#16325F', 34, sky(292, 390, 16, 0.4, 12), 0.002)
        pc.run_pass(app, '#3E74A8', 30, sky(360, 470, 16, 1.3, 14), 0.002)
        pc.run_pass(app, '#E39A58', 28, sky(430, 545, 14, 2.1, 10), 0.002)
        pc.run_pass(app, '#9BB4C8', 26, fill_under(FAR, 590, True), 0.002)
        pc.run_pass(app, '#5C7388', 26, fill_under(FAR, 600, False), 0.002)
        pc.run_pass(app, '#F7FBFF', 12, snow(FAR), 0.002)
        pc.run_pass(app, '#E8B56A', 18, blue_gap(app), 0.002)
        pc.run_pass(app, '#7A9358', 26, fill_under(NEAR, 760, True, step=8, pad=16), 0.002)
        pc.run_pass(app, '#24362C', 26, fill_under(NEAR, 770, False, step=8, pad=16), 0.002)
        pc.run_pass(app, '#F4F8FC', 11, snow(NEAR), 0.002)
        pc.run_pass(app, '#8EAA62', 24, meadow(735, 860, 14, 0.6), 0.002)
        pc.run_pass(app, '#3E6A32', 22, meadow(840, 958, 14, 1.4), 0.002)
        pc.run_pass(app, '#C4A36A', 20, safe(pc.path_strokes(), pad=12), 0.002)
        pc.run_pass(app, '#1E3324', 10, trees(), 0.002)
        pc.run_pass(app, '#6E8F45', 8, grass(), 0.002)
        pc.run_pass(app, '#24548C', 16, gutter(), 0.003)
        coats = fur()
        pc.run_pass(app, '#C47A3C', 8, coats['#C47A3C'], 0.002)
        pc.run_pass(app, '#8A4A28', 7, coats['#8A4A28'], 0.002)
        pc.run_pass(app, '#E7C7A4', 8, coats['#E7C7A4'], 0.002)
        pc.run_pass(app, '#4A2A18', 5, coats['#4A2A18'], 0.002)
        pc.run_pass(app, '#3A2418', 12, pc.ear_strokes(hx, hy), 0.003)
        pc.run_pass(app, '#E7B0A8', 6, pc.ear_strokes(hx, hy, inner=True), 0.003)
        pc.run_pass(app, '#F2D2BC', 10, pc.ellipse_strokes(hx + 4, hy + 78, 40, 30, step=5), 0.003)
        pc.run_pass(app, '#C46A74', 5, pc.nose_strokes(hx, hy), 0.003)
        eyes = pc.eye_strokes(hx, hy)
        pc.run_pass(app, '#F7F3EA', 5, pc.only(eyes, 'white'), 0.004)
        pc.run_pass(app, '#E2A020', 4, pc.only(eyes, 'iris'), 0.004)
        pc.run_pass(app, '#140E0A', 3, pc.only(eyes, 'pupil'), 0.005)
        pc.run_pass(app, '#F4F1EA', 2, pc.whisker_strokes(hx, hy), 0.004)
        pc.run_pass(app, '#2A1C14', 8, pc.tail_strokes(hx, hy)[:4], 0.002)
        pc.save_view(app, 'canvas_real.png')
    finally:
        organism.send(organism.LEFT_UP)
        organism.send(organism.MOVE, 900, 14, absolute=True)
        log(f'stop {time.strftime("%H:%M:%S")}')


if __name__ == '__main__':
    main()
