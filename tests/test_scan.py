import os, time

import numpy as np

import scan
from scan import HISTORY, WINDOW, Eye

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
    assert report['alerts'] == []
    assert report['rhythmic'] == []
    assert report['rate'][-1] == 0

def test_clock_is_rhythmic_and_a_quiet_block_alerts_once():
    def clock(frame, i):
        frame[0:2, 0:2] = 200 if (i // 4) % 2 == 0 else 40
    eye, frame, report = run(clock, WINDOW)
    assert report['alerts'] == []
    assert report['rhythmic'] == [[0, 0, 62, 83]]
    assert report['rate'][0] >= 200 and report['rate'][-1] == 0
    frame = frame.copy()
    frame[0:2, 0:2] = 200 if (WINDOW // 4) % 2 == 0 else 40
    frame[8:16, 10:20] = 220
    report = eye.step(frame)
    assert [(a['box'], a['cells']) for a in report['alerts']] == [([312, 333, 625, 666], 80)]
    held = eye.step(frame.copy())
    assert held['alerts'] == report['alerts']

def test_unit_clock_is_rhythmic_and_a_faint_wash_is_not_a_popup():
    def tick(frame, i):
        frame[22, 30] = 41 if (i // 4) % 2 == 0 else 40
    _, _, report = run(tick, WINDOW)
    assert report['alerts'] == []
    assert report['rhythmic'] == [[937, 916, 968, 958]]
    def wash(frame, i):
        if i == WINDOW - 1: frame[8:16, 10:20] = 41
    _, _, faint = run(wash, WINDOW)
    assert faint['alerts'] == []

def test_a_speck_does_not_alert_and_six_cells_do():
    def speck(frame, i):
        if i == WINDOW - 1: frame[20, 30] = 255
    _, _, report = run(speck, WINDOW)
    assert report['alerts'] == []
    def block(frame, i, width):
        if i == WINDOW - 1: frame[10, 0:width] = 255
    _, _, small = run(lambda frame, i: block(frame, i, 5), WINDOW)
    _, _, large = run(lambda frame, i: block(frame, i, 6), WINDOW)
    assert small['alerts'] == []
    assert large['alerts'][0]['cells'] == 6

def test_alerts_stay_in_history_after_the_screen_settles():
    eye, frame, _ = run(lambda frame, i: None, WINDOW)
    for row in range(HISTORY + 2):
        for _ in range(WINDOW): report = eye.step(frame.copy())
        frame = frame.copy(); frame[row % 20, 0:8] = 250 - row
        report = eye.step(frame)
    assert len(report['alerts']) == HISTORY
    assert [a['box'][1] for a in report['alerts']][-1] == (HISTORY + 1) % 20 * 1000 // 24
    for _ in range(WINDOW * 2): report = eye.step(frame.copy())
    assert len(report['alerts']) == HISTORY

def test_publish_skips_a_frame_when_the_reader_holds_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(scan, 'OUT', tmp_path / 'scan.json')
    def held(self, target): raise PermissionError(5, 'Access is denied')
    monkeypatch.setattr(type(tmp_path), 'replace', held)
    assert scan.publish(dict(at=1)) is False
    monkeypatch.undo()
    monkeypatch.setattr(scan, 'OUT', tmp_path / 'scan.json')
    assert scan.publish(dict(at=2)) is True
    assert (tmp_path / 'scan.json').read_text() == '{"at":2}'

def test_claim_takes_over_a_live_pid_without_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(scan, 'PID', tmp_path / 'scan.pid')
    monkeypatch.setattr(scan, 'OUT', tmp_path / 'scan.json')
    monkeypatch.setattr(scan, 'alive', lambda pid: True)
    (tmp_path / 'scan.pid').write_text('4242')
    (tmp_path / 'scan.json').write_text('{}')
    assert scan.claim() is False
    old = time.time() - 10
    os.utime(tmp_path / 'scan.json', (old, old))
    assert scan.claim() is True
    assert (tmp_path / 'scan.pid').read_text() == str(os.getpid())

def test_watch_writes_its_crash_to_scan_err_and_cleans_up(tmp_path, monkeypatch):
    for name in ('PID', 'OUT', 'STOP', 'ERR'): monkeypatch.setattr(scan, name, tmp_path / getattr(scan, name).name)
    monkeypatch.setattr(scan.win32, '_setup_bindings', lambda: None)
    def broken(): raise RuntimeError('capture failed')
    monkeypatch.setattr(scan, 'grab', broken)
    (tmp_path / 'scan.err').write_text('old')
    try: scan.watch()
    except RuntimeError: pass
    assert 'RuntimeError: capture failed' in (tmp_path / 'scan.err').read_text()
    assert not (tmp_path / 'scan.pid').exists()

def test_watch_stops_on_scan_stop(tmp_path, monkeypatch):
    for name in ('PID', 'OUT', 'STOP', 'ERR'): monkeypatch.setattr(scan, name, tmp_path / getattr(scan, name).name)
    monkeypatch.setattr(scan.win32, '_setup_bindings', lambda: None)
    frames = iter(range(3))
    def grab():
        if next(frames) == 2: (tmp_path / 'scan.stop').touch()
        return np.full((24, 32), 40, np.uint8)
    monkeypatch.setattr(scan, 'grab', grab)
    scan.watch()
    assert not (tmp_path / 'scan.pid').exists() and not (tmp_path / 'scan.stop').exists() and not (tmp_path / 'scan.err').exists()
    assert '"pid":%d' % os.getpid() in (tmp_path / 'scan.json').read_text()
