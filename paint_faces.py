"""Paint back over the diagonal edge lines so the peaks are faces again."""
import organism
import paint_cat as pc
import paint_real as pr


def main():
    pc.DEADLINE = pr.STOP
    organism.configure(str(pc.WORK), '20261029_231909', '40', '1793312349.6254058', '1793314749.6254058')
    app = organism.App(str(pc.WORK))
    organism.send(organism.LEFT_UP)
    app.desk.click_pixel(900, 14)
    try:
        pc.run_pass(app, '#9BB4C8', 22, pr.fill_under(pr.FAR, 520, True, step=6, pad=16), 0.004)
        pc.run_pass(app, '#5C7388', 22, pr.fill_under(pr.FAR, 530, False, step=6, pad=16), 0.004)
        pc.run_pass(app, '#F7FBFF', 10, pr.snow(pr.FAR, pad=16), 0.004)
        pc.run_pass(app, '#7A9358', 22, pr.fill_under(pr.NEAR, 740, True, step=6, pad=18), 0.004)
        pc.run_pass(app, '#24362C', 22, pr.fill_under(pr.NEAR, 750, False, step=6, pad=18), 0.004)
        pc.run_pass(app, '#F4F8FC', 9, pr.snow(pr.NEAR, pad=16), 0.004)
        pc.save_view(app, 'canvas_real.png')
    finally:
        organism.send(organism.LEFT_UP)
        organism.send(organism.MOVE, 900, 14, absolute=True)


if __name__ == '__main__':
    main()
