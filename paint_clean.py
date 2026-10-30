"""Cover the herringbone ticks and repaint the tail as one solid shape."""
import organism
import paint_cat as pc
import paint_more as pm
import paint_real as pr


def tail_fill():
    hx, hy = pc.cat_anchors()
    x0, y0 = hx + 100, hy + 90
    xc, yc = hx + 200, hy + 28
    x1, y1 = hx + 155, hy + 145
    strokes = []
    for i in range(16):
        t = i / 15
        x = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * xc + t ** 2 * x1
        y = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * yc + t ** 2 * y1
        strokes.extend(pm.disk(x, y, 18, 14, step=4))
    return strokes


def main():
    pc.DEADLINE = pr.STOP
    organism.configure(str(pc.WORK), '20261029_231909', '40', '1793312349.6254058', '1793314749.6254058')
    app = organism.App(str(pc.WORK))
    organism.send(organism.LEFT_UP)
    app.desk.click_pixel(900, 14)
    try:
        pc.run_pass(app, '#16325F', 40, pr.sky(296, 378, 10, 0.3, 5), 0.004)
        pc.run_pass(app, '#3E74A8', 36, pr.sky(352, 448, 10, 0.9, 6), 0.004)
        pc.run_pass(app, '#E39A58', 34, pr.sky(424, 530, 10, 1.6, 5), 0.004)
        pc.run_pass(app, '#9BB4C8', 24, pr.fill_under(pr.FAR, 500, True, step=7, pad=14), 0.004)
        pc.run_pass(app, '#5C7388', 24, pr.fill_under(pr.FAR, 505, False, step=7, pad=14), 0.004)
        pc.run_pass(app, '#F7FBFF', 11, pr.snow(pr.FAR), 0.004)
        pc.run_pass(app, '#C47A3C', 14, tail_fill(), 0.004)
        pc.save_view(app, 'canvas_real.png')
    finally:
        organism.send(organism.LEFT_UP)
        organism.send(organism.MOVE, 900, 14, absolute=True)


if __name__ == '__main__':
    main()
