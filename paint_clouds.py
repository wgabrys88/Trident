"""Break the flat orange sky into clouds. Leave the cat and the peaks alone."""
import organism
import paint_cat as pc
import paint_more as pm
import paint_real as pr

LIGHT = ((820, 400), (980, 430), (1220, 415), (1560, 425), (1740, 405))
DEEP = ((900, 455), (1120, 445), (1320, 450), (1640, 455))


def puffs(spots, rx, ry):
    strokes = []
    for cx, cy in spots:
        strokes.extend(pm.disk(cx, cy, rx, ry, step=5))
    kept = []
    for pts in strokes:
        run = []
        for x, y in pts:
            if y < pr.y_at(pr.FAR, x) - 10:
                run.append((x, y))
            else:
                if len(run) >= 2:
                    kept.append(run)
                run = []
        if len(run) >= 2:
            kept.append(run)
    return pr.safe(kept, pad=12)


def main():
    pc.DEADLINE = pr.STOP
    organism.configure(str(pc.WORK), '20261029_231909', '40', '1793312349.6254058', '1793314749.6254058')
    app = organism.App(str(pc.WORK))
    organism.send(organism.LEFT_UP)
    app.desk.click_pixel(900, 14)
    try:
        pc.run_pass(app, '#F6D7A0', 18, puffs(LIGHT, 58, 20), 0.004)
        pc.run_pass(app, '#C46A30', 14, puffs(DEEP, 40, 16), 0.004)
        pc.save_view(app, 'canvas_real.png')
    finally:
        organism.send(organism.LEFT_UP)
        organism.send(organism.MOVE, 900, 14, absolute=True)


if __name__ == '__main__':
    main()
