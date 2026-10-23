import numpy as np

from scan import WINDOW, Eye

def run(paint, frames, eye=None, frame=None):
    eye = eye or Eye()
    frame = np.full((24, 32), 40, np.uint8) if frame is None else frame
    if eye.prev is None: eye.step(frame)
    report = None
    for i in range(frames):
        frame = frame.copy()
        paint(frame, i)
        report = eye.step(frame)
    return eye, frame, report

def test_static_screen_has_no_alert():
    _, _, report = run(lambda frame, i: None, WINDOW)
    assert report['kind'] == 'live'
    assert report['alert'] is None
    assert report['rhythmic'] == []
    assert report['rate'][-1] == 0

def test_clock_is_rhythmic_and_a_quiet_block_alerts():
    def clock(frame, i):
        frame[0:2, 0:2] = 200 if (i // 4) % 2 == 0 else 40
    eye, frame, report = run(clock, WINDOW)
    assert report['alert'] is None
    assert report['rhythmic'] == [[0, 0, 62, 83]]
    assert report['rate'][0] >= 200 and report['rate'][-1] == 0
    frame = frame.copy()
    frame[0:2, 0:2] = 200 if (WINDOW // 4) % 2 == 0 else 40
    frame[8:16, 10:20] = 220
    report = eye.step(frame)
    assert report['alert']['cells'] == 80
    assert report['alert']['box'] == [312, 333, 625, 666]
    held = eye.step(frame.copy())
    assert held['alert']['at'] == report['alert']['at']

def test_unit_clock_is_rhythmic_and_a_faint_wash_is_not_a_popup():
    def tick(frame, i):
        frame[22, 30] = 41 if (i // 4) % 2 == 0 else 40
    _, _, report = run(tick, WINDOW)
    assert report['alert'] is None
    assert report['rhythmic'] == [[937, 916, 968, 958]]
    def wash(frame, i):
        if i == WINDOW - 1: frame[8:16, 10:20] = 41
    _, _, faint = run(wash, WINDOW)
    assert faint['alert'] is None

def test_a_speck_does_not_alert_and_six_cells_do():
    def speck(frame, i):
        if i == WINDOW - 1: frame[20, 30] = 255
    _, _, report = run(speck, WINDOW)
    assert report['alert'] is None
    def block(frame, i, width):
        if i == WINDOW - 1: frame[10, 0:width] = 255
    _, _, small = run(lambda frame, i: block(frame, i, 5), WINDOW)
    _, _, large = run(lambda frame, i: block(frame, i, 6), WINDOW)
    assert small['alert'] is None
    assert large['alert']['cells'] == 6
