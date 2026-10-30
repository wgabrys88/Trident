"""Cover the grass on the cat, put the pupils and the tail back, and break the sky bands."""
import math

import organism
import paint_cat as pc
import paint_more as pm
import paint_real as pr

STOP = pr.STOP


def sky_streaks(y, color_box):
    strokes = []
    x = 700
    while x <= 1800:
        yy = y + int(8 * math.sin(x * 0.02))
        if not pr.sun_disk(x, yy) and yy < pr.y_at(pr.FAR, x) - 4:
            strokes.append([(x, yy - 20), (x + 5, yy), (x - 3, yy + 22)])
        x += 20
    return pr.safe(strokes, pad=6)


def spurs():
    shapes = (
        ((1185, 455), (1075, 575), (1295, 560)),
        ((1490, 470), (1375, 590), (1605, 575)),
        ((990, 455), (900, 560), (1090, 545)),
    )
    strokes = []
    for shape in shapes:
        strokes.extend(pm.peak(*shape, step=6))
    return strokes


def orange_left(app):
    pixels, width, _height = app.desk.grab()

    def rgb(x, y):
        i = (y * width + x) * 4
        return pixels[i + 2], pixels[i + 1], pixels[i]

    strokes = []
    for y in range(470, 590, 8):
        run = []
        for x in range(1040, 1700, 9):
            if pr.hits_cat(x, y, 10) or pc.blocked(x, y) or pr.sun_disk(x, y):
                if len(run) >= 2:
                    strokes.append(run)
                run = []
                continue
            top = pr.y_at(pr.FAR, x)
            red, green, blue = rgb(x, y)
            hole = red > 190 and 110 < green < 190 and blue < 140 and top - 8 <= y <= top + 36
            if hole:
                run.append((x, y))
            else:
                if len(run) >= 2:
                    strokes.append(run)
                run = []
        if len(run) >= 2:
            strokes.append(run)
    return strokes


def main():
    pc.DEADLINE = STOP
    organism.configure(str(pc.WORK), '20261029_231909', '40', '1793312349.6254058', '1793314749.6254058')
    app = organism.App(str(pc.WORK))
    organism.send(organism.LEFT_UP)
    app.desk.click_pixel(900, 14)
    hx, hy = pc.cat_anchors()
    eyes = ((1106, 694), (1162, 696))
    try:
        pc.run_pass(app, '#2A5080', 12, sky_streaks(385, None), 0.004)
        pc.run_pass(app, '#C48448', 12, sky_streaks(455, None), 0.004)
        pc.run_pass(app, '#4E6478', 20, spurs(), 0.004)
        pc.run_pass(app, '#6A8498', 16, orange_left(app), 0.004)
        pc.run_pass(app, '#C47A3C', 16, pc.ellipse_strokes(1150, 825, 148, 108, step=6), 0.004)
        pc.run_pass(app, '#E7C7A4', 12, pc.ellipse_strokes(1142, 800, 46, 34, step=5), 0.004)
        pc.run_pass(app, '#4A2A18', 5, pc.stripe_strokes(hx, hy), 0.004)
        pc.run_pass(app, '#C47A3C', 11, pc.tail_strokes(hx, hy), 0.004)
        pc.run_pass(app, '#8A4E28', 6, pc.tail_strokes(hx, hy)[::2], 0.004)
        pc.run_pass(app, '#3A2418', 11, pc.ear_strokes(hx, hy), 0.004)
        pc.run_pass(app, '#E7B0A8', 6, pc.ear_strokes(hx, hy, inner=True), 0.004)
        pc.run_pass(app, '#F2D2BC', 8, pc.ellipse_strokes(hx + 4, hy + 70, 36, 26, step=4), 0.004)
        pc.run_pass(app, '#C46A74', 5, pc.nose_strokes(hx, hy), 0.004)
        pc.run_pass(app, '#2A1A10', 5, [s for cx, cy in eyes for s in pm.disk(cx, cy, 18, 13, 3)], 0.005)
        pc.run_pass(app, '#F7F3EA', 5, [s for cx, cy in eyes for s in pm.disk(cx, cy, 14, 10, 3)], 0.005)
        pc.run_pass(app, '#E2A020', 4, [s for cx, cy in eyes for s in pm.disk(cx + 1, cy + 1, 8, 7, 2)], 0.005)
        pc.run_pass(app, '#140E0A', 3, [s for cx, cy in eyes for s in pm.pupil(cx + 1, cy + 1)], 0.006)
        pc.run_pass(app, '#F7F3EA', 3, [[(cx - 5, cy - 4), (cx - 2, cy - 2)] for cx, cy in eyes], 0.006)
        pc.run_pass(app, '#F4F1EA', 2, pc.whisker_strokes(hx, hy), 0.004)
        pc.save_view(app, 'canvas_real.png')
    finally:
        organism.send(organism.LEFT_UP)
        organism.send(organism.MOVE, 900, 14, absolute=True)


if __name__ == '__main__':
    main()
