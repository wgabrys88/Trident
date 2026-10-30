"""Cover the leftover light ribbon beside the sun. Leave the cat and the sun disk alone."""
import organism
import paint_cat as pc
import paint_more as pm

PALETTE = (
    (0xE3, 0x9A, 0x58),
    (0xF6, 0xD7, 0xA0),
    (0xB7, 0xCB, 0xDC),
    (0x6E, 0x9C, 0xC8),
    (0x5A, 0x70, 0x80),
    (0x3E, 0x53, 0x44),
)


def main():
    organism.configure(str(pc.WORK), '20261029_225413', '40', '1793310853.3588097', '1793313253.3588097')
    app = organism.App(str(pc.WORK))
    organism.send(organism.LEFT_UP)
    app.desk.click_pixel(860, 18)
    pixels, width, _height = app.desk.grab()

    def rgb(x, y):
        i = (y * width + x) * 4
        return (pixels[i + 2], pixels[i + 1], pixels[i])

    groups = {color: [] for color in PALETTE}
    for x in range(740, 1120, 12):
        for y in range(470, 620, 10):
            if pm.in_cat(x, y) or pc.blocked(x, y):
                continue
            # Keep the sun.
            if 830 <= x <= 915 and 445 <= y <= 525:
                continue
            red, green, blue = rgb(x, y)
            if not (red > 210 and green > 210 and blue > 200 and abs(red - blue) < 40):
                continue
            source = None
            for sy in range(y - 30, 320, -10):
                color = rgb(x, sy)
                if color[2] + 25 < color[0] or color[0] < 190:
                    source = color
                    break
            if source is None:
                source = (0xE3, 0x9A, 0x58)
            key = min(PALETTE, key=lambda item: sum((a - b) * (a - b) for a, b in zip(item, source)))
            groups[key].append([(x - 6, y), (x + 8, y)])
    try:
        for color, strokes in groups.items():
            if len(strokes) < 2:
                continue
            pc.run_pass(app, '#%02x%02x%02x' % color, 18, strokes, 0.003)
        pc.save_view(app, 'canvas_final.png')
    finally:
        organism.send(organism.LEFT_UP)
        organism.send(organism.MOVE, 900, 18, absolute=True)


if __name__ == '__main__':
    main()
