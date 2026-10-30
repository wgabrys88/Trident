"""Bury the leftover sine-wave ribbon and restore snow only on the summits."""
import organism
import paint_cat as pc
import paint_more as pm

FAR = (
    ((730, 468), (652, 575), (830, 560)),
    ((1040, 430), (900, 575), (1200, 555)),
    ((1320, 400), (1160, 570), (1520, 545)),
    ((1640, 445), (1480, 575), (1830, 550)),
)
NEAR = (
    ((820, 545), (652, 730), (1040, 700)),
    ((1280, 520), (1080, 735), (1500, 690)),
    ((1620, 530), (1460, 740), (1830, 680)),
)
PALETTE = (
    (0x24, 0x54, 0x8C),
    (0x6E, 0x9C, 0xC8),
    (0xE3, 0x9A, 0x58),
    (0xF6, 0xD7, 0xA0),
    (0xB7, 0xCB, 0xDC),
    (0x5A, 0x70, 0x80),
    (0x3E, 0x53, 0x44),
    (0x2C, 0x40, 0x38),
    (0x4A, 0x62, 0x4C),
)


def snap(color):
    return min(PALETTE, key=lambda item: sum((a - b) * (a - b) for a, b in zip(item, color)))


def right_face(summit, right):
    foot = (summit[0], right[1])
    return pm.peak(summit, foot, right, step=6)


def ribbon(app):
    pixels, width, _height = app.desk.grab()

    def rgb(x, y):
        i = (y * width + x) * 4
        return (pixels[i + 2], pixels[i + 1], pixels[i])

    groups = {color: [] for color in PALETTE}
    for x in range(690, 1820, 16):
        target = None
        for y in range(455, 635, 3):
            if pm.in_cat(x, y) or pc.blocked(x, y):
                continue
            red, green, blue = rgb(x, y)
            if red > 218 and green > 218 and blue > 205 and abs(red - blue) < 36:
                if target is None or red + green + blue > target[0]:
                    target = (red + green + blue, y)
        if target is None:
            continue
        y = target[1]
        source = None
        for sy in range(y - 24, 300, -8):
            if pm.in_cat(x, sy):
                continue
            color = rgb(x, sy)
            if not (color[0] > 218 and color[1] > 218 and color[2] > 205):
                source = color
                break
        if source is None:
            continue
        groups[snap(source)].append([(x, y - 10), (x, y), (x, y + 12)])
    return {color: strokes for color, strokes in groups.items() if len(strokes) >= 2}


def main():
    organism.configure(str(pc.WORK), '20261029_225413', '40', '1793310853.3588097', '1793313253.3588097')
    app = organism.App(str(pc.WORK))
    organism.send(organism.LEFT_UP)
    app.desk.click_pixel(860, 18)
    try:
        pc.run_pass(app, '#B7CBDC', 46, [s for peak in FAR for s in pm.peak(*peak, step=6)], 0.0025)
        pc.run_pass(app, '#5A7080', 28, [s for summit, _left, right in FAR for s in right_face(summit, right)], 0.003)
        pc.run_pass(app, '#3E5344', 50, [s for peak in NEAR for s in pm.peak(*peak, step=6)], 0.0025)
        pc.run_pass(app, '#2C4038', 30, [s for summit, _left, right in NEAR for s in right_face(summit, right)], 0.003)
        found = ribbon(app)
        print('ribbon colors', {('#%02x%02x%02x' % color): len(strokes) for color, strokes in found.items()}, flush=True)
        for color, strokes in found.items():
            pc.run_pass(app, '#%02x%02x%02x' % color, 22, strokes, 0.003)
        pc.run_pass(app, '#F7FBFF', 14, [s for peak in FAR for s in pm.cap(*peak)], 0.004)
        pc.run_pass(app, '#F4F8FC', 12, [s for peak in NEAR for s in pm.cap(*peak)], 0.004)
        pc.save_view(app, 'canvas_final.png')
    finally:
        organism.send(organism.LEFT_UP)
        organism.send(organism.MOVE, 900, 18, absolute=True)
        print('cover done', flush=True)


if __name__ == '__main__':
    main()
