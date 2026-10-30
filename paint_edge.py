"""Trace a light and a dark edge on each peak, and cover the brown sky dab."""
import organism
import paint_cat as pc
import paint_real as pr


def ridge(sil, want_lit):
    peaks = pr.peak_xs(sil)
    strokes, run = [], []
    x = int(sil[0][0])
    while x <= int(sil[-1][0]):
        if pr.lit(x, peaks) == want_lit:
            run.append((x, pr.y_at(sil, x)))
        elif len(run) >= 2:
            strokes.append(run)
            run = []
        else:
            run = []
        x += 7
    if len(run) >= 2:
        strokes.append(run)
    return pr.safe(strokes, pad=18)


def brown(app):
    pixels, width, _height = app.desk.grab()

    def rgb(x, y):
        i = (y * width + x) * 4
        return pixels[i + 2], pixels[i + 1], pixels[i]

    strokes = []
    for y in range(390, 520, 8):
        run = []
        for x in range(1000, 1400, 8):
            if pr.hits_cat(x, y, 8) or pr.sun_disk(x, y) or y > pr.y_at(pr.FAR, x) - 8:
                if len(run) >= 2:
                    strokes.append(run)
                run = []
                continue
            red, green, blue = rgb(x, y)
            dab = abs(red - 196) < 28 and abs(green - 106) < 28 and blue < 80 and red > green + 40
            if dab:
                run.append((x, y))
            else:
                if len(run) >= 2:
                    strokes.append(run)
                run = []
        if len(run) >= 2:
            strokes.append(run)
    return strokes


def main():
    pc.DEADLINE = pr.STOP
    organism.configure(str(pc.WORK), '20261029_231909', '40', '1793312349.6254058', '1793314749.6254058')
    app = organism.App(str(pc.WORK))
    organism.send(organism.LEFT_UP)
    app.desk.click_pixel(900, 14)
    try:
        pc.run_pass(app, '#E7C48A', 10, brown(app), 0.004)
        pc.run_pass(app, '#E4EEF6', 8, ridge(pr.FAR, True), 0.004)
        pc.run_pass(app, '#3E5164', 8, ridge(pr.FAR, False), 0.004)
        pc.run_pass(app, '#C6B56A', 8, ridge(pr.NEAR, True), 0.004)
        pc.run_pass(app, '#1C2E24', 8, ridge(pr.NEAR, False), 0.004)
        pc.save_view(app, 'canvas_real.png')
    finally:
        organism.send(organism.LEFT_UP)
        organism.send(organism.MOVE, 900, 14, absolute=True)


if __name__ == '__main__':
    main()
