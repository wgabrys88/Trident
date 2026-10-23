import threading, time

import pytest
from PIL import Image

import main

class Win:
    def __init__(self): self.done = []
    def _resolve_screen_pos(self, x, y, region): return x, y
    def _move_cursor(self, x, y): self.done.append(('move', x, y))
    def _do_click(self, pair, region): self.done.append(('click', pair))
    def _do_right_click(self, pair, region): self.done.append(('rclick', pair))
    def _do_double_click(self, pair, region): self.done.append(('dclick', pair))
    def _do_hotkey(self, combo): self.done.append(('key', combo))
    def _do_type_text(self, text): self.done.append(('type', text))
    def _do_stroke(self, pairs, region): self.done.append(('drag', pairs))
    def _do_scroll(self, pair, region, step, count): self.done.append(('scroll', pair))
    def _do_wheel(self, pair, region, amount, horizontal): self.done.append(('wheel', pair))
    def _do_cursor_pos(self, region): return '500,500'
    def _do_select_region(self): return '100,100,900,900', 0
    def _do_window_list(self): return [(7, 'Code', [0, 0, 1000, 960], 42, False, False, True)]

@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'log', lambda kind, data: None)
    monkeypatch.setattr(main, 'SCREENSHOT_DELAY', 0)
    monkeypatch.delenv('TRIDENT_END', raising=False)
    monkeypatch.delenv('TRIDENT_START', raising=False)
    hand = Win()
    monkeypatch.setattr(main.HAND, 'mod', hand, raising=False)
    e = main.Engine(tmp_path)
    monkeypatch.setattr(main.Engine, 'capture', lambda self, *_: dict(id='shot', at=time.time()))
    e.hand = hand
    return e

def watch(e, *alerts, at=None):
    main.write(e.work / 'scan.json', dict(kind='live', at=time.time() if at is None else at, alerts=list(alerts)))

def seen(e, at):
    main.write('screen.json', dict(id='old', at=at))

def alert(box, at=None): return dict(box=box, cells=50, at=time.time() if at is None else at)

def test_exec_programs_are_gated(engine):
    seen(engine, time.time() - 5)
    watch(engine, alert([700, 755, 1000, 911]))
    v = engine.hands("desktop.click(100, 100)\nprint('after')")
    assert v['interrupted']['box'] == [700, 755, 1000, 911]
    assert engine.hand.done == [] and v['stdout'] == ''

def test_gestures_stop_the_whole_batch(engine):
    seen(engine, time.time() - 5)
    watch(engine, alert([700, 755, 1000, 911]))
    v = engine.hands('key ctrl+s\nclick 10 10; click 20 20\ntype hi')
    assert v['interrupted'] and engine.hand.done == []

def test_alert_seen_in_the_last_capture_does_not_stop(engine):
    watch(engine, alert([700, 755, 1000, 911], time.time() - 5))
    seen(engine, time.time() - 1)
    v = engine.hands('click 10 10')
    assert 'interrupted' not in v and engine.hand.done == [('click', '10,10')]

def test_alert_that_contains_the_point_is_acked(engine):
    seen(engine, time.time() - 5)
    watch(engine, alert([700, 755, 1000, 911]))
    v = engine.hands('click 800 800; click 10 10')
    assert 'interrupted' not in v and len(engine.hand.done) == 2
    assert v['cleared'][0]['cleared'] == 'inside'

def test_expect_clears_the_change_the_mind_caused(engine):
    seen(engine, time.time() - 5)
    watch(engine)
    def palette(pair, region):
        engine.hand.done.append(('click', pair)); watch(engine, alert([300, 0, 700, 133]))
    engine.hand._do_click = palette
    v = engine.hands('expect 300 0 700 140\nclick 500 20\ntype settings\nkey enter')
    assert 'interrupted' not in v
    assert engine.hand.done == [('click', '500,20'), ('type', 'settings'), ('key', 'enter')]
    assert v['cleared'][0]['cleared'] == 'expected'

def test_without_expect_the_own_palette_stops(engine):
    seen(engine, time.time() - 5)
    watch(engine)
    def palette(pair, region):
        engine.hand.done.append(('click', pair)); watch(engine, alert([300, 0, 700, 133]))
    engine.hand._do_click = palette
    v = engine.hands('click 500 20\ntype settings')
    assert v['interrupted']['box'] == [300, 0, 700, 133] and engine.hand.done == [('click', '500,20')]

def test_expect_window_expires(engine, monkeypatch):
    seen(engine, time.time() - 5)
    watch(engine)
    engine.desktop.arm(time.time() - 5)
    engine.desktop.expect(300, 0, 700, 140, 0.01)
    time.sleep(0.05)
    watch(engine, alert([300, 0, 700, 133]))
    with pytest.raises(main.Interrupted): engine.desktop.key('enter')

def test_python_can_expect(engine):
    seen(engine, time.time() - 5)
    watch(engine)
    def palette(combo):
        engine.hand.done.append(('key', combo)); watch(engine, alert([300, 0, 700, 133]))
    engine.hand._do_hotkey = palette
    v = engine.hands("desktop.expect(300, 0, 700, 140)\ndesktop.key('ctrl+shift+p')\ndesktop.type_text('x')")
    assert 'interrupted' not in v and engine.hand.done == [('key', 'ctrl+shift+p'), ('type', 'x')]

def test_stale_watch_does_not_gate(engine):
    seen(engine, time.time() - 50)
    watch(engine, alert([700, 755, 1000, 911], time.time() - 20), at=time.time() - 10)
    v = engine.hands('click 10 10')
    assert v['watch'] == 'stale' and 'interrupted' not in v

def test_gate_converts_region_points_to_screen(engine):
    main.text('region.txt', '500,0,1000,500')
    seen(engine, time.time() - 5)
    watch(engine, alert([500, 0, 600, 100]))
    v = engine.hands('click 100 100')
    assert 'interrupted' not in v and v['cleared'][0]['cleared'] == 'inside'
    watch(engine, alert([0, 0, 100, 100]))
    v = engine.hands('click 100 100')
    assert v['interrupted']['box'] == [0, 0, 100, 100]

def test_region_accepts_none_and_boxes_and_rejects_the_rest(engine):
    assert (engine.work / 'region.txt').read_text() == 'NONE'
    engine.region('0,0,500,500'); assert (engine.work / 'region.txt').read_text() == '0,0,500,500'
    engine.region('NONE'); assert (engine.work / 'region.txt').read_text() == 'NONE'
    engine.region(); assert (engine.work / 'region.txt').read_text() == '100,100,900,900'
    for bad in ('500,0,100,500', '0,0,1001,500', '0,0,500', '-1,0,5,5', 'none', '0, 0, 5, 5'):
        with pytest.raises(SystemExit): engine.region(bad)
    assert (engine.work / 'region.txt').read_text() == '100,100,900,900'

def test_every_result_carries_the_clock(engine, monkeypatch, capsys):
    monkeypatch.delenv('TRIDENT_START', raising=False)
    engine._emit('story', dict(ok=True))
    assert 'clock' not in main.read(engine.result)
    monkeypatch.setenv('TRIDENT_START', str(time.time() - 600))
    monkeypatch.setenv('TRIDENT_END', '')
    engine._emit('story', dict(ok=True))
    clock = main.read(engine.result)['clock']
    assert clock['used'] == '10.0 min' and 'left' not in clock
    monkeypatch.setenv('TRIDENT_END', str(time.time() + 1800))
    engine._emit('story', dict(ok=True))
    clock = main.read(engine.result)['clock']
    assert clock['used'] == '10.0 min' and clock['left'] in ('30.0 min', '29.9 min')
    assert '"clock"' in capsys.readouterr().out

def test_mind_prompt_states_the_budget(engine):
    prompt = engine._mind_prompt('tidy the body', 25, time.mktime((2026, 10, 23, 11, 10, 0, 0, 0, -1)))
    assert 'The wake has 25 minutes and ends at 11:10.' in prompt
    assert 'No budget was given here; if the sentence names one, keep it.' in engine._mind_prompt('tidy the body', '', None)
    assert main.MINUTES == '' or main.E('TRIDENT_MINUTES')
    assert '--windows' in prompt and '--retina' in prompt and '--look' not in prompt and 'rewrite' not in prompt

def test_jev_fit_pads_extreme_strips_only():
    wide, sx, sy = main.jev_fit(Image.new('RGB', (2000, 100)))
    assert wide.width / wide.height < main.JEV_RATIO * 1.05 and (sx, sy) == (1, 10)
    tall, sx, sy = main.jev_fit(Image.new('RGB', (100, 2000)))
    assert tall.height / tall.width < main.JEV_RATIO * 1.05 and (sx, sy) == (10, 1)
    screen, sx, sy = main.jev_fit(Image.new('RGB', (1920, 1080)))
    assert (sx, sy) == (1, 1) and screen.size == main.fara_fit(Image.new('RGB', (1920, 1080))).size

def test_jev_boxes_map_back_through_the_pad(engine, monkeypatch, tmp_path):
    Image.new('RGB', (2000, 100)).save(tmp_path / 'strip.png')
    main.write('screen.json', dict(id='s', image=str(tmp_path / 'strip.png'), size=[2000, 100], at=time.time()))
    answer = dict(type='box', box=dict(left=0.5, top=0.05, right=0.6, bottom=0.08), confidence=0.9)
    monkeypatch.setattr(type(engine.decision), 'ask', lambda self, state, q, shown: dict(answers=dict(save=dict(answer))))
    v = engine.jev('find save', 'save@the save button')
    assert v['answers']['save']['box'] == dict(left=0.5, top=0.5, right=0.6, bottom=0.8)

REPLY = 'I see Code.\n<tool_call>{"name": "computer_use", "arguments": {"action": "left_click", "coordinate": [500, 20]}}</tool_call>'

def test_retina_asks_fresh_every_time(engine, monkeypatch, tmp_path):
    Image.new('RGB', (1920, 1080)).save(tmp_path / 'full.png')
    main.write('screen.json', dict(id='s', image=str(tmp_path / 'full.png'), size=[1920, 1080], region='NONE', at=time.time()))
    calls = []
    monkeypatch.setattr(type(engine.vision), 'ask', lambda self, text, image, origin: calls.append(text) or REPLY)
    engine.retina('where is the menu?'); v = engine.retina('and the title?')
    assert calls == [main.FARA_LOOK + 'where is the menu?', main.FARA_LOOK + 'and the title?']
    assert v['coordinate'] == [500, 20] and v['program'] == 'click 500 20' and main.read('look.json')['task'] == 'and the title?'
    with pytest.raises(ValueError): engine.retina('  ')

def test_windows_lists_the_hand(engine):
    assert engine.windows()['windows'] == [dict(hwnd=7, title='Code', box=[0, 0, 1000, 960], pid=42, minimized=False, topmost=False, foreground=True)]

def test_unscan_stops_the_watch_before_the_job_closes(engine):
    pid, stop = engine.work / 'scan.pid', engine.work / 'scan.stop'
    pid.write_text('1')
    def watcher():
        while not stop.exists(): time.sleep(0.01)
        pid.unlink(); stop.unlink()
    threading.Thread(target=watcher).start()
    engine._unscan()
    assert not pid.exists() and not stop.exists()

def test_cli_config_blocks_no_legitimate_work_and_lets_the_mind_read_its_captures():
    assert main.CLI_CONFIG['permissions'] == dict(allow=['Read(**)', 'Write(**)', 'Shell(*)', 'WebFetch(*)', 'Mcp(*:*)'], deny=[])
    assert main.CLI_CONFIG['approvalMode'] == 'unrestricted' and 'captures in shots/ are images you may read' in main.MIND

def test_expect_covers_an_alert_in_the_same_millisecond(engine):
    engine.desktop.arm(time.time() - 5)
    for _ in range(200):
        engine.desktop.expect(300, 0, 700, 140)
        watch(engine, alert([300, 0, 700, 133], round(time.time(), 3)))
        engine.desktop.key('enter')
        engine.desktop.arm(time.time())

def test_scan_reports_the_last_crash_and_starts_windowless(engine, monkeypatch):
    py = engine.work / '.venv' / 'Scripts' / 'pythonw.exe'
    py.parent.mkdir(parents=True); py.touch()
    (engine.work / 'scan.err').write_text('Traceback\nPermissionError: [WinError 5]')
    (engine.work / 'scan.stop').touch()
    started = []
    def popen(argv, **kwargs):
        started.append(argv); (engine.work / 'scan.err').unlink()
        watch(engine)
    monkeypatch.setattr(main.subprocess, 'Popen', popen)
    v = engine.scan()
    assert started[0][0] == str(py) and not (engine.work / 'scan.stop').exists()
    assert v['err'].endswith('PermissionError: [WinError 5]') and v['kind'] == 'live'

def wake(monkeypatch, minutes_used=10, budget=40):
    monkeypatch.setenv('TRIDENT_START', str(time.time() - minutes_used * 60))
    monkeypatch.setenv('TRIDENT_END', str(time.time() + (budget - minutes_used) * 60))

def test_ledger_keeps_one_lesson_per_wake_and_the_seal_closes_it(engine, monkeypatch):
    wake(monkeypatch)
    engine.ledger('first try'); engine.ledger('## wake fake\nlook first, then act')
    wakes = engine._wakes()
    assert len(wakes) == 1 and wakes[0].startswith(main.WAKE_ID + ' ') and 'used 10 of 40 min, open\nfake\nlook first, then act' in wakes[0]
    engine._seal(time.time() - 1500, '40')
    assert len(engine._wakes()) == 1 and 'used 25 of 40 min, ended\nfake\nlook first, then act' in engine._wakes()[0]

def test_seal_without_a_lesson_still_records_the_wake(engine):
    (engine.work / 'stop').touch()
    engine._seal(time.time() - 120, 40)
    assert engine._wakes()[0].endswith('used 2 of 40 min, stopped\nNo lesson was written.\n')

def test_ledger_dilutes_the_oldest_wakes(engine, monkeypatch):
    wake(monkeypatch)
    for n in range(main.LEDGER_WAKES + 3):
        monkeypatch.setattr(main, 'WAKE_ID', f'run{n:02d}'); engine.ledger(f'lesson {n}')
    wakes = engine._wakes()
    assert len(wakes) == main.LEDGER_WAKES and wakes[0].startswith('run03 ') and wakes[-1].startswith(f'run{main.LEDGER_WAKES + 2:02d} ')
    monkeypatch.setattr(main, 'LEDGER_CHARS', 300)
    engine.ledger('x' * 200)
    assert sum(map(len, engine._wakes())) <= 300 and engine._wakes()[-1].endswith('x' * 200 + '\n')

def test_ledger_asks_for_a_shorter_lesson(engine, monkeypatch):
    wake(monkeypatch)
    with pytest.raises(ValueError): engine.ledger('x' * (main.LEDGER_LESSON + 1))
    with pytest.raises(ValueError): engine.ledger('  ')
    monkeypatch.delenv('TRIDENT_START')
    with pytest.raises(SystemExit): engine.ledger('outside a wake')

def test_the_ledger_reaches_the_next_wake_and_jev(engine, monkeypatch):
    assert 'LEDGER\n\nThis is the first wake.' in engine._mind_prompt('s', 40, time.time())
    wake(monkeypatch); engine.ledger('the palette needs expect')
    assert 'the palette needs expect' in engine._mind_prompt('s', 40, time.time()).split('TOOLS')[0]
    states = []
    monkeypatch.setattr(type(engine.decision), 'ask', lambda self, state, q, shown: states.append(state) or dict(answers={}))
    engine.jev('why', 'ok?Is it done')
    assert any(part.startswith('LEDGER: ## wake ') and 'the palette needs expect' in part for part in states[0])

def test_recall_digests_earlier_journals_but_not_this_one(engine, monkeypatch, tmp_path):
    root = tmp_path / 'root'; root.mkdir()
    (root / 'run_20261022_090000.md').write_text('## 09:00:01 user\n\nYou are Trident.\n\nSENTENCE\n\nsort the files\n\n## 09:00:05 request shell\n\nls\n\n## 09:00:06 response shell\n\nok\n\n## 09:00:07 err\n\nboom\n\n## 09:30:00 say\n\nDone: 2 met, 1 not met.\n', encoding='utf-8')
    (root / 'run_20261023_100000.md').write_text('## 10:00:00 say\n\nthis wake\n', encoding='utf-8')
    monkeypatch.setattr(main, 'ROOT', root); monkeypatch.setattr(main, 'RUN', root / 'run_20261023_100000.md')
    assert engine.recall() == dict(wakes=[dict(run='run_20261022_090000', span=['09:00:01', '09:30:00'], sentence='sort the files', calls=dict(shell=1), errors=1, final='Done: 2 met, 1 not met.')])

SKILL = '''def greet(engine, name='world'):
    """$CALL --greet [NAME] : say hello."""
    return dict(hello=name, story=engine.story_path.name)
'''

def test_a_grown_skill_becomes_a_tool(engine, tmp_path):
    skills = tmp_path / 'skills'; skills.mkdir()
    (skills / 'greet.py').write_text(SKILL, encoding='utf-8')
    try:
        main.grow(skills)
        assert engine.greet('body') == dict(hello='body', story='story.txt')
        assert 'greet' in main.tools() and '--greet [NAME] : say hello.' in engine._mind_prompt('s', 40, time.time())
    finally:
        del main.Engine.greet

def test_a_skill_cannot_shadow_a_tool(tmp_path):
    skills = tmp_path / 'skills'; skills.mkdir()
    (skills / 'hands.py').write_text('def hands(engine, src): return {}\n', encoding='utf-8')
    with pytest.raises(SystemExit): main.grow(skills)

def test_the_prompts_are_task_generic(engine):
    told = '\n'.join((engine._mind_prompt('s', '', None), main.FARA_SYSTEM, main.FARA_LOOK, main.JEV_USAGE, main.CHANGE_APPROACH)).lower()
    for word in ('paint', 'jpg', 'public note', 'notepad', 'calculator', 'vs code', 'vscode', 'editor', 'extension', 'interpreter', 'pylance', 'status bar', '40 minutes', '1-2 file', 'only place', 'no comments', 'no fallbacks', 'px-wjt', 'eb-wjt', 'oyu ', 'the was that'):
        assert word not in told, word
    for rule in ('LOOK FIRST', 'CHECKLIST', 'TIME', 'MEMORY', 'EYES', 'JEV', 'EVOLVE', 'HONESTY', 'FINISH'):
        assert rule + '.' in main.MIND
    assert 'act wherever the sentence requires' in main.MIND

def test_every_flag_explains_itself():
    for name, f in main.tools().items():
        doc = f.__doc__ or ''
        assert doc.startswith('$CALL --' + name), name
        assert len(doc) > 80 and (name in ('stop', 'mind') or 'Returns' in doc), name

def test_ledger_without_a_budget(engine, monkeypatch):
    monkeypatch.setenv('TRIDENT_START', str(time.time() - 300)); monkeypatch.setenv('TRIDENT_END', '')
    engine.ledger('measure before acting')
    assert 'used 5 min, open\nmeasure before acting' in engine._wakes()[0]
    engine._seal(time.time() - 420, '')
    assert 'used 7 min, ended\nmeasure before acting' in engine._wakes()[0]
