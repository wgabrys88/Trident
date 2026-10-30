"""Lay the cat's shadow on the path, to the right of the sun."""
import organism
import paint_cat as pc
import paint_real as pr


def shadow():
    strokes = []
    y = 936
    while y <= 962:
        wide = 90 + (y - 936) * 4
        strokes.append([(1160 - wide // 3, y), (1160 + wide, y)])
        y += 6
    return pr.safe(strokes, pad=4)


def main():
    pc.DEADLINE = pr.STOP
    organism.configure(str(pc.WORK), '20261029_231909', '40', '1793312349.6254058', '1793314749.6254058')
    app = organism.App(str(pc.WORK))
    organism.send(organism.LEFT_UP)
    app.desk.click_pixel(900, 14)
    try:
        pc.run_pass(app, '#8A6A3E', 14, shadow(), 0.004)
        pc.save_view(app, 'canvas_real.png')
    finally:
        organism.send(organism.LEFT_UP)
        organism.send(organism.MOVE, 900, 14, absolute=True)


if __name__ == '__main__':
    main()
