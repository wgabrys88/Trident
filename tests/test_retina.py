import json

import pytest
from PIL import Image

import main


def test_point_is_clamped_to_norm():
    assert main.fara_point(dict(coordinate=[1001, 230])) == (1000, 230)
    assert main.fara_point(dict(coordinate=[-4, 12.6])) == (0, 13)
    assert main.fara_point(dict(coordinate='(262, 147)')) == (262, 147)
    assert main.fara_point(dict(action='terminate')) is None
    with pytest.raises(ValueError): main.fara_point(dict(coordinate=[1, 2, 3]))


def test_program_uses_clamped_point():
    assert main.fara_program(dict(action='left_click', coordinate=[1200, 5])) == 'click 1000 5'
    assert 'desktop.drag([(x, y), (1000, 0)])' in main.fara_program(dict(action='left_click_drag', coordinate=[1001, -1]))


def test_retina_sends_the_bare_goal(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'log', lambda kind, data: None)
    assert not hasattr(main, 'FARA_LOOK')
    shot = tmp_path / 'shot.png'; Image.new('RGB', (1920, 1080), 'white').save(shot)
    sent = {}
    def post(self, payload, shown):
        sent.update(payload)
        return dict(choices=[dict(finish_reason='stop', message=dict(content='I will click it.\n<tool_call>\n{"name": "computer_use", "arguments": {"action": "left_click", "coordinate": [1001, 40]}}\n</tool_call>'))])
    monkeypatch.setattr(main.Vision, 'post', post)
    e = main.Engine(tmp_path)
    main.write('screen.json', dict(id='s1', image=str(shot), size=[1920, 1080], region='NONE'))
    v = e.retina('In the Paint window, click the Brushes button.')
    user = sent['messages'][1]['content']
    assert user[1] == dict(type='text', text='In the Paint window, click the Brushes button.')
    assert sent['temperature'] == 0 and sent['chat_template_kwargs'] == dict(enable_thinking=False)
    assert sent['max_tokens'] == main.FARA_MAX_TOKENS
    assert v['coordinate'] == [1000, 40] and v['program'] == 'click 1000 40'


def test_mind_prompt_has_no_box_or_inventory_request(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'log', lambda kind, data: None)
    e = main.Engine(tmp_path)
    prompt = e._mind_prompt('paint', '', None)
    eyes = prompt.split('EYES.', 1)[1].split('\n', 1)[0]
    assert 'names what is on' not in eyes and 'target point' in eyes
    assert 'Name every window' not in prompt
