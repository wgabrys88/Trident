"""Zoom: coarse Fara point on the capture, fine point on a crop of the same capture, mapped back."""
import json

from PIL import Image

import main


def reply(action='left_click', point=(500, 500)):
    args = dict(action=action) | (dict(coordinate=list(point)) if point else dict(answer='done'))
    return 'I will click it.\n<tool_call>\n' + json.dumps(dict(name='computer_use', arguments=args)) + '\n</tool_call>'


def fake(monkeypatch, tmp_path, replies, size=(1920, 1080)):
    monkeypatch.setattr(main, 'log', lambda kind, data: None)
    shot = tmp_path / 'shot.png'; Image.new('RGB', size, 'white').save(shot)
    sent = []
    def post(self, payload, shown):
        sent.append(shown); r = replies.pop(0)
        if isinstance(r, Exception): raise r
        return dict(choices=[dict(finish_reason='stop', message=dict(content=r))])
    monkeypatch.setattr(main.Vision, 'post', post)
    e = main.Engine(tmp_path)
    main.write('screen.json', dict(id='s1', image=str(shot), size=list(size), region='NONE'))
    return e, sent


def test_box_is_clamped_at_the_edges():
    assert main.zoom_box((500, 500)) == [375, 375, 625, 625]
    assert main.zoom_box((10, 990)) == [0, 750, 250, 1000]
    assert main.zoom_box((1000, 0)) == [750, 0, 1000, 250]
    assert main.zoom_box((500, 500), 2000) == [0, 0, 1000, 1000]


def test_fine_point_maps_back():
    box = [375, 375, 625, 625]
    assert main.zoom_back(box, (500, 500)) == [500, 500]
    assert main.zoom_back(box, (0, 0)) == [375, 375]
    assert main.zoom_back(box, (1000, 1000)) == [625, 625]
    assert main.zoom_back([0, 750, 250, 1000], (200, 600)) == [50, 900]


def test_crop_keeps_aspect_and_is_upscaled():
    im = Image.new('RGB', (1920, 1080))
    crop = main.zoom_crop(im, [375, 375, 625, 625])
    assert crop.width == main.FARA_TARGET_WIDTH and abs(crop.width / crop.height - 1920 / 1080) < 0.01
    assert main.fara_fit(crop).size == (1440, 800)


def test_zoom_asks_twice_and_returns_the_fine_point(tmp_path, monkeypatch):
    e, sent = fake(monkeypatch, tmp_path, [reply(point=(100, 100)), reply('double_click', (200, 600))])
    v = e.retina('zoom In the Paint window, click the red swatch.')
    assert v['task'] == 'In the Paint window, click the red swatch.' and len(sent) == 2
    assert sent[0]['question'] == sent[1]['question'] == v['task']
    assert v['zoom']['box'] == [0, 0, 250, 250] and v['zoom']['coarse'] == [100, 100] and v['zoom']['fine'] == [200, 600]
    assert v['coordinate'] == [50, 150] and v['action'] == 'double_click' and v['program'] == 'dclick 50 150'


def test_zoom_falls_back_to_the_coarse_point(tmp_path, monkeypatch):
    for second in (reply('terminate', None), 'no tool call here', OSError('server down')):
        e, sent = fake(monkeypatch, tmp_path, [reply(point=(300, 400)), second])
        v = e.retina('zoom In the editor, click the Trust link.')
        assert len(sent) == 2 and v['coordinate'] == [300, 400] and v['program'] == 'click 300 400'
        assert v['zoom']['box'] == [175, 275, 425, 525] and v['zoom']['fine'] is None


def test_no_zoom_without_a_pointer_action(tmp_path, monkeypatch):
    e, sent = fake(monkeypatch, tmp_path, [reply('terminate', None)])
    v = e.retina('zoom What does the title say?')
    assert len(sent) == 1 and v['zoom'] is None and v['action'] == 'terminate'
    e, sent = fake(monkeypatch, tmp_path, [reply()])
    assert 'zoom' not in e.retina('In the Paint window, click the Brushes button.') and len(sent) == 1
