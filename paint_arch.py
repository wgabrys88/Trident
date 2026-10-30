"""Paint out the white arch left of the central peaks and put the sun back."""
import organism
import paint_cat as pc
import paint_more as pm


def band(x0, y0, x1, y1, step=8):
    strokes = []
    y = y0
    while y <= y1:
        t = (y - y0) / max(1, y1 - y0)
        x = x0 + (x1 - x0) * t
        strokes.append([(x, y), (x + 64, y)])
        y += step
    return pm.keep(strokes)


def sun():
    return pm.keep(pc.sun_strokes())


def main():
    organism.configure(str(pc.WORK), '20261029_225413', '40', '1793310853.3588097', '1793313253.3588097')
    app = organism.App(str(pc.WORK))
    organism.send(organism.LEFT_UP)
    app.desk.click_pixel(860, 18)
    try:
        pc.run_pass(app, '#E39A58', 22, band(830, 494, 900, 536), 0.004)
        pc.run_pass(app, '#3E5344', 26, band(850, 536, 980, 620), 0.003)
        pc.run_pass(app, '#FFE6A8', 20, sun(), 0.004)
        pc.run_pass(app, '#F7FBFF', 12, pm.cap((820, 545), (652, 730), (1040, 700)), 0.004)
        pc.save_view(app, 'canvas_final.png')
    finally:
        organism.send(organism.LEFT_UP)
        organism.send(organism.MOVE, 900, 18, absolute=True)


if __name__ == '__main__':
    main()
