"""Paint the stray dash back into the path."""
import organism
import paint_cat as pc
import paint_real as pr


def path():
    strokes = []
    y = 940
    while y <= 964:
        strokes.append([(1060, y), (1380, y)])
        y += 6
    return strokes


def main():
    pc.DEADLINE = pr.STOP
    organism.configure(str(pc.WORK), '20261029_231909', '40', '1793312349.6254058', '1793314749.6254058')
    app = organism.App(str(pc.WORK))
    organism.send(organism.LEFT_UP)
    app.desk.click_pixel(900, 14)
    try:
        pc.run_pass(app, '#C3A269', 16, path(), 0.004)
        pc.save_view(app, 'canvas_real.png')
    finally:
        organism.send(organism.LEFT_UP)
        organism.send(organism.MOVE, 900, 14, absolute=True)


if __name__ == '__main__':
    main()
