import json

import pytest
from PIL import Image

import main

pilot = main.module('trident_pilot', main.ROOT / 'pilot.py')


def reply(thought, **args):
    return f'{thought}\n<tool_call>\n' + json.dumps(dict(name='computer_use', arguments=args)) + '\n</tool_call>'


class Win:
    """Records what the hand would do; 0-1000 maps to a 1920x1080 screen."""
    def __init__(self): self.done, self.cursor = [], '500,500'
    def _resolve_screen_pos(self, x, y, region): return round(x * 1919 / 1000), round(y * 1079 / 1000)
    def _move_cursor(self, x, y): self.done.append(('move', x, y)); self.cursor = f'{round(x * 1000 / 1919)},{round(y * 1000 / 1079)}'
    def _do_click(self, pair, region): self.done.append(('click', pair)); self.cursor = pair
    def _do_right_click(self, pair, region): self.done.append(('rclick', pair))
    def _do_double_click(self, pair, region): self.done.append(('dclick', pair))
    def _do_hotkey(self, combo): self.done.append(('key', combo))
    def _do_type_text(self, text): self.done.append(('type', text))
    def _do_stroke(self, pairs, region): self.done.append(('drag', pairs))
    def _do_wheel(self, pair, region, amount, horizontal): self.done.append(('wheel', pair, amount, horizontal))
    def _do_cursor_pos(self, region): return self.cursor


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'log', lambda kind, data: None)
    monkeypatch.setattr(main, 'SCREENSHOT_DELAY', 0)
    hand = Win(); monkeypatch.setattr(main.HAND, 'mod', hand, raising=False)
    e = main.Engine(tmp_path); e.hand = hand
    shots = tmp_path / 'shots'; shots.mkdir()
    count = iter(range(1000))
    def capture(self, *_):
        path = shots / f'{next(count):03d}.png'; Image.new('RGB', (1920, 1080), 'white').save(path)
        return dict(id=path.stem, image=str(path), size=[1920, 1080], region='NONE')
    monkeypatch.setattr(main.Engine, 'capture', capture)
    return e


def script(monkeypatch, replies):
    calls = []
    def chat(self, messages, shown):
        calls.append(messages); return replies[len(calls) - 1], dict(timings=dict(prompt_n=1, predicted_n=1))
    monkeypatch.setattr(main.Vision, 'chat', chat)
    return calls


def images(msgs): return sum(pilot.has_image(m) for m in msgs)


def test_prune_keeps_three_newest_screenshots_and_the_task_text():
    h = [pilot.user_turn('img0', 'TASK', original=True)]
    for i in range(1, 6):
        h += [dict(role='assistant', content=f'reply {i}'), pilot.user_turn(f'img{i}', pilot.NEXT)]
    out = pilot.messages('SYS', h)
    assert out[0] == dict(role='system', content='SYS')
    assert out[1] == dict(role='user', content=[dict(type='text', text='TASK')])
    assert images(out) == 3 and [m['content'] for m in out if m['role'] == 'assistant'] == [f'reply {i}' for i in range(1, 6)]
    assert [m['content'][0]['image_url']['url'] for m in out if pilot.has_image(m)] == ['img3', 'img4', 'img5']
    assert not any(k.startswith('_') for m in out for k in m)


def test_keys_and_scroll_mapping():
    assert pilot.key_combo(main, ['Control', 'c']) == 'ctrl+c'
    assert pilot.key_combo(main, ['ctrl+shift+s']) == 'ctrl+shift+s'
    assert pilot.key_combo(main, 'Meta') == 'win'
    assert pilot.key_combo(main, ['Enter']) == 'enter'
    assert pilot.key_combo(main, ['F5']) == 'f5'
    assert pilot.scroll_amount(-500, 1080) == -480
    assert pilot.scroll_amount(20, 1080) == 120
    assert pilot.scroll_amount(0, 1080) == 0


def test_loop_follows_the_harness(engine, monkeypatch):
    replies = [reply('I will open Start.', action='left_click', coordinate=[12, 985]),
               reply('I will move to the start.', action='mouse_move', coordinate=[300, 400]),
               reply('I will drag.', action='left_click_drag', coordinate=[1003, 420]),
               reply('I will type.', action='type', text='paint\nok'),
               reply('I will press keys.', action='key', keys=['Control', 's']),
               reply('Done.', action='terminate', answer='painted')]
    calls = script(monkeypatch, replies)
    r = engine.pilot('Open Paint and draw.', '10')
    assert r['status'] == 'terminate' and r['answer'] == 'painted' and r['actions'] == 6
    first = calls[0]
    assert first[0]['content'] == main.FARA_SYSTEM and first[1]['content'][1] == dict(type='text', text='Open Paint and draw.')
    assert len(first) == 2
    second = calls[1]
    assert second[2] == dict(role='assistant', content=replies[0])
    assert second[3]['content'][1]['text'] == pilot.NEXT
    assert all(images(c) <= 3 for c in calls) and images(calls[5]) == 3
    assert calls[5][1]['content'] == [dict(type='text', text='Open Paint and draw.')]
    done = engine.hand.done
    assert ('click', '12,985') in done
    assert ('drag', ['300,400', '1000,420']) in done
    assert ('type', 'paint') in done and ('key', 'enter') in done and ('type', 'ok') in done
    assert ('key', 'ctrl+s') in done
    trace = json.loads((engine.work / 'shots' / f'pilot_{main.WAKE_ID}.json').read_text('utf-8'))
    assert trace['records'][0]['screen_px'] == [23, 1063] and trace['records'][2]['coordinate'] == [1000, 420]


def test_page_reading_is_answered_with_an_observation(engine, monkeypatch):
    calls = script(monkeypatch, [reply('Read.', action='read_page_answer_question', question='what?'), reply('Done.', action='terminate', answer='x')])
    engine.pilot('task', '5')
    assert calls[1][-1]['content'][1]['text'] == pilot.NO_PAGE.format(action='read_page_answer_question') + '\n' + pilot.NEXT


def test_ask_user_stops_unless_autoreply(engine, monkeypatch):
    ask = reply('Ask.', action='ask_user_question', question='Which color?')
    script(monkeypatch, [ask])
    assert engine.pilot('task', '5')['status'] == 'ask_user_question'
    monkeypatch.setenv('TRIDENT_PILOT_AUTOREPLY', '1')
    calls = script(monkeypatch, [ask, reply('Done.', action='terminate', answer='ok')])
    assert engine.pilot('task', '5')['status'] == 'terminate'
    assert calls[1][-1]['content'][1]['text'] == pilot.AUTO_REPLY


def test_limits_and_bad_replies_stop(engine, monkeypatch):
    script(monkeypatch, [reply('Wait.', action='wait', time=0)] * 3)
    r = engine.pilot('task', '3')
    assert (r['status'], r['actions']) == ('max steps', 3)
    script(monkeypatch, ['no tool call here'])
    assert engine.pilot('task', '3')['status'].startswith('invalid action')
    script(monkeypatch, [reply('Fly.', action='fly')])
    assert engine.pilot('task', '3')['status'].startswith('invalid action')
    engine.sentinel.touch()
    assert engine.pilot('task', '3')['status'] == 'stop file'


def test_pilot_is_not_offered_to_the_mind(engine):
    assert '--pilot' not in engine._mind_prompt('x', '', None)
