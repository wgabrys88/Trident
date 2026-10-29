"""Cover the raw canvas still showing at the edges and under the village."""
import organism
import paint_starry as s
from paint_refine import paint_raw


def band(x0, x1, y0, y1, step, dx=12):
    strokes = []
    y = y0
    while y <= y1:
        pts = []
        x = x0
        while x <= x1:
            pts.append((x, y))
            x += dx
        strokes.append(pts)
        y += step
    return strokes


def main():
    organism.configure(str(s.WORK), '20261029_220922', '40', '1793308162.9878514', '1793310562.9878514')
    app = organism.App(str(s.WORK))
    organism.send(organism.LEFT_UP)
    s.set_size(14)
    s.set_color(app, '#1b4a96')
    sky = band(588, 650, 268, 700, 12) + band(1690, 1772, 268, 700, 12)
    paint_raw(app, sky, 0.003)
    s.set_color(app, '#0c1424')
    dark = band(588, 680, 690, 978, 12) + band(1660, 1772, 690, 978, 12)
    dark += band(640, 1768, 926, 956, 10)
    paint_raw(app, dark, 0.003)
    organism.send(organism.MOVE, 900, 18, absolute=True)
    image = app.desk.grab()
    (s.WORK / 'canvas_done.png').write_bytes(organism.png(app.desk.stretch(image, (560, 248, 1790, 1000), 980, 600)))
    s.log('edges covered')


if __name__ == '__main__':
    main()
