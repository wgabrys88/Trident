import concurrent.futures, hashlib, io, json, os, subprocess, sys, tempfile, threading, time, unittest, uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import base, decide, desk, evidence, eye, grounding, main, memory, mind
from measure import measure
from state import atomic, json_file

ROOT = Path(__file__).resolve().parents[1]


def call(action='left_click', **kwargs):
    return '<tool_call>\n' + json.dumps(dict(name='computer_use', arguments=dict(action=action, **kwargs))) + '\n</tool_call>'


class FakeDesk:
    def __init__(self, work, size=(21, 11)):
        self.work, self.size = Path(work), size
        self.pixels = bytes([20, 40, 60, 255]) * (size[0] * size[1])
        self.events, self.counter = [], 0
        self.region = 'NONE'
        self.box = [0, 0, *size]
        self.cursor = [500, 500]
        self.windows = [dict(hwnd=4, pid=5, pixel_box=[0, 0, *size], foreground=True)]

    def capture(self):
        self.counter += 1
        sid = str(self.counter)
        image = self.work / (sid + '.png')
        data = desk.png((self.pixels, *self.size))
        atomic(image, data)
        screen = dict(id=sid, image=str(image), size=list(self.size), region=self.region, box=self.box[:], screen_size=list(self.size),
                      windows=json.loads(json.dumps(self.windows)), cursor=self.cursor[:], cursor_pixel=grounding.point_pixel(self.cursor, self.size), sha256=hashlib.sha256(data).hexdigest())
        json_file(self.work / 'screen.json', screen)
        json_file(self.work / (sid + '.json'), screen)
        return screen

    def stretch(self, image, box, width, height):
        # Isolated nearest-neighbour fixture, never a live desktop operation.
        pixels, w, h = image
        out = b''.join(pixels[(min(h - 1, box[1] + y * (box[3] - box[1]) // height) * w + min(w - 1, box[0] + x * (box[2] - box[0]) // width)) * 4:][:4]
                       for y in range(height) for x in range(width))
        return out, width, height

    def click(self, *args, **kwargs): self.events.append(('click', args, kwargs))
    def move(self, *args): self.events.append(('move', args))
    def drag(self, *args): self.events.append(('drag', args))
    def wheel(self, *args, **kwargs): self.events.append(('wheel', args, kwargs))
    def key(self, *args): self.events.append(('key', args))
    def type_text(self, *args): self.events.append(('type', args))


class BodyCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        base.configure(self.work, 'test')
        self.env = patch.dict(os.environ, TRIDENT_JOURNAL='1', TRIDENT_CHAT_ID='test:1')
        self.env.start()
        self.addCleanup(self.env.stop)
        self.desk = FakeDesk(self.work)
        self.app = SimpleNamespace(work=self.work, desk=self.desk)

    def receipt(self, action='left_click', point=None, **kwargs):
        screen = self.desk.capture()
        image = desk.unpng(Path(screen['image']).read_bytes())
        if action in eye.POINTER:
            kwargs['coordinate'] = point if point is not None else [500, 500]
        result = dict(action=action, coordinate=kwargs.get('coordinate'), arguments=dict(action=action, **kwargs), coordinate_mode='normalized')
        if action in ('scroll', 'hscroll'):
            result['scroll_distance_pixels'] = kwargs['pixels'] * self.desk.size[0 if action == 'hscroll' else 1] / 1000
        eye.validate(result['arguments'])
        return grounding.store_action(self.app, screen, image, result)['action_id']


class BodyTests(BodyCase):
    def test_parser_all_advertised_actions(self):
        for action in eye.ACTIONS:
            args = dict(coordinate=[0, 1000]) if action in eye.POINTER else {
                'key': dict(keys=['Control', 'ArrowUp']), 'type': dict(text='a\nb'), 'scroll': dict(pixels=-500),
                'hscroll': dict(pixels=200), 'wait': dict(time=0), 'terminate': dict(answer='Not visible')
            }[action]
            with self.subTest(action=action):
                self.assertEqual(eye.parse_call(call(action, **args))[1]['action'], action)

    def test_parser_rejects_malformed(self):
        valid = call(coordinate=[500, 500])
        cases = [valid + valid, valid.replace('computer_use', 'other'), call('scroll'), call(coordinate=[1001, 0]),
                 call(coordinate=[float('nan'), 4]), call(coordinate=['4', 5]), call(coordinate=[True, 5]),
                 call('type', text=4), call('key', keys=['unknown']), call('terminate', answer=''), call('visit_url', url='x'),
                 valid.replace('"action": "left_click"', '"action":"left_click","action":"left_click"'), call('wait', time=61),
                 '<tool_call>{bad}</tool_call>']
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(base.Fail):
                eye.parse_call(raw)

    def test_normalized_and_explicit_pixels(self):
        self.assertEqual(eye.to_norm(1439, 799, 1440, 800), [1000, 1000])
        self.assertEqual(grounding.point_pixel([0, 1000], (1350, 1020)), [0, 1019])
        self.assertEqual(grounding.point_pixel([1000, 0], (1350, 1020)), [1349, 0])
        with patch.object(desk.Desk, '_box', return_value=[15, 20, 1365, 1040]):
            self.assertEqual(desk.Desk(self.work).point(683, 112), (15 + round(683 * 1349 / 1000), 20 + round(112 * 1019 / 1000)))
        self.assertIn('0-1000 model-display', eye.system_text(1344, 1024))
        base.SET['coordinate_mode'] = 'pixels'
        self.assertIn('pixels of the sent bitmap', eye.system_text(1344, 1024))

    def test_capture_bound_once_and_metadata(self):
        screen = self.desk.capture()
        _, image = eye.load_capture(self.app)
        response = dict(model='reported-fara', usage=dict(prompt_tokens=7), choices=[dict(message=dict(content=call(coordinate=[683, 112])))])
        class Reply(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *_): self.close()
        with patch.object(self.desk, 'stretch', return_value=(b'\0' * 4 * 32 * 32, 32, 32)), patch('eye.urllib.request.urlopen', return_value=Reply(json.dumps(response).encode())):
            result = eye.ask(self.app, image, 'Click cyan in Paint', screen)
        self.assertEqual(result['coordinate'], [683, 112])
        self.assertEqual(result['reported_model'], 'reported-fara')
        self.assertEqual(result['original_size'], [21, 11])
        self.assertEqual(result['capture_hash'], screen['sha256'])
        screen['size'] = [1, 1]
        json_file(self.work / 'screen.json', screen)
        with self.assertRaisesRegex(base.Fail, 'dimensions'):
            eye.load_capture(self.app)

    def test_explicit_pixel_backend_and_scroll_conversion(self):
        screen = self.desk.capture()
        image = (self.desk.pixels, *self.desk.size)
        base.SET['coordinate_mode'] = 'pixels'
        width, height = eye.fit_size(*image[1:])
        response = dict(choices=[dict(message=dict(content=call(coordinate=[width - 1, height - 1])))])
        with patch.object(self.desk, 'stretch', return_value=(bytes(width * height * 4), width, height)), patch('eye.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(response).encode())):
            result = eye.ask(self.app, image, 'click lower right', screen)
        self.assertEqual(result['coordinate'], [1000, 1000])
        self.assertIsNone(result['usage'])
        self.assertIsNone(result['estimated_usd'])
        self.assertEqual(result['money'], 'not metered')
        response['choices'][0]['message']['content'] = call('scroll', pixels=height)
        with patch.object(self.desk, 'stretch', return_value=(bytes(width * height * 4), width, height)), patch('eye.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(response).encode())):
            result = eye.ask(self.app, image, 'scroll one viewport', screen)
        self.assertEqual(result['scroll_distance_pixels'], 11)

    def test_unavailable_eye_has_no_executable_record(self):
        self.desk.capture()
        goal = self.work / 'q.txt'
        goal.write_text('Click Paint cyan')
        with patch('eye.urllib.request.urlopen', side_effect=OSError('unavailable')), patch.object(self.desk, 'stretch', return_value=(bytes(32 * 32 * 4), 32, 32)):
            with self.assertRaisesRegex(OSError, 'unavailable'): eye.eye.run(self.app, goal)
        self.assertFalse((self.work / 'actions').exists())
        self.assertEqual(self.desk.events, [])

    def test_zoom_mapping_and_absence(self):
        self.desk.capture()
        goal = self.work / 'q.txt'
        goal.write_text('zoom\nClick Paint cyan', encoding='utf-8')
        coarse = dict(action='left_click', coordinate=[500, 500], arguments=dict(action='left_click', coordinate=[500, 500]), coordinate_mode='normalized')
        absent = dict(action='terminate', coordinate=None, answer='Target absent', program=None)
        with patch('eye.ask', side_effect=[coarse.copy(), absent.copy()]):
            result = eye.eye.run(self.app, goal)
        self.assertEqual(result['action'], 'terminate')
        self.assertIsNone(result['program'])
        fine = coarse | dict(coordinate=[1000, 0])
        with patch('eye.ask', side_effect=[coarse.copy(), fine.copy()]):
            result = eye.eye.run(self.app, goal)
        box = result['zoom_pixels']
        expected = [(box[2] - 1) * 1000 / 20, box[1] * 1000 / 10]
        self.assertEqual(result['coordinate'], expected)
        self.assertEqual(grounding.point_pixel(result['coordinate'], (21, 11)), [box[2] - 1, box[1]])
        record = json.loads(grounding.record_path(self.work, result['action_id']).read_text())
        self.assertEqual(record['point'], expected)

    def test_guard_unchanged_and_consumed(self):
        action_id = self.receipt()
        desk.hand._run(self.desk, 'fara ' + action_id)
        self.assertEqual(len(self.desk.events), 1)
        self.desk.events.clear()
        with self.assertRaisesRegex(base.Fail, 'consumed'):
            grounding.execute(self.desk, action_id)
        self.assertEqual(self.desk.events, [])

    def test_guard_changed_patch_and_geometry_zero_input(self):
        for change in ('pixel', 'region', 'box', 'size', 'window', 'run'):
            with self.subTest(change=change):
                self.desk = FakeDesk(self.work)
                self.app.desk = self.desk
                action_id = self.receipt()
                if change == 'pixel':
                    pixels = bytearray(self.desk.pixels)
                    pixels[(5 * 21 + 10) * 4] ^= 1
                    self.desk.pixels = bytes(pixels)
                elif change == 'region': self.desk.region = '0,0,500,1000'
                elif change == 'box': self.desk.box[0] = 1
                elif change == 'size': self.desk.size = (11, 21)
                elif change == 'window': self.desk.windows[0]['pixel_box'][0] = 2
                elif change == 'run': base.SET['run'] = 'another'
                with self.assertRaises(base.Fail):
                    grounding.execute(self.desk, action_id)
                self.assertEqual(self.desk.events, [])
                base.SET['run'] = 'test'

    def test_clipped_patch_and_other_pixels(self):
        image = (self.desk.pixels, *self.desk.size)
        self.assertEqual(grounding.patch(image, [0, 0])['box'], [0, 0, 3, 3])
        self.assertEqual(grounding.patch(image, [1000, 1000])['box'], [18, 8, 21, 11])
        action_id = self.receipt()
        pixels = bytearray(self.desk.pixels)
        pixels[0] ^= 1  # Outside the target patch: explicitly not a whole-image check.
        self.desk.pixels = bytes(pixels)
        grounding.execute(self.desk, action_id)
        self.assertEqual(len(self.desk.events), 1)

    def test_foreground_and_drag_provenance(self):
        action_id = self.receipt('type', text='hello')
        self.desk.windows[0]['foreground'] = False
        with self.assertRaisesRegex(base.Fail, 'foreground'):
            grounding.execute(self.desk, action_id)
        self.assertEqual(self.desk.events, [])
        self.desk.windows[0]['foreground'] = True
        action_id = self.receipt('left_click_drag')
        self.desk.cursor = [400, 500]
        with self.assertRaisesRegex(base.Fail, 'cursor'):
            grounding.execute(self.desk, action_id)
        self.assertEqual(self.desk.events, [])

    def test_every_executable_action(self):
        for action in eye.ACTIONS:
            if action == 'terminate': continue
            args = {} if action in eye.POINTER else {'key': dict(keys=['Control', 'ArrowUp']), 'type': dict(text='a\nb'),
                'scroll': dict(pixels=-500), 'hscroll': dict(pixels=500), 'wait': dict(time=0)}[action]
            with self.subTest(action=action):
                action_id = self.receipt(action, **args)
                grounding.execute(self.desk, action_id)
                record = json.loads(grounding.record_path(self.work, action_id).read_text())
                self.assertEqual(record['state'], 'consumed')
        self.assertEqual(len(self.desk.events), len(eye.ACTIONS) - 2)
        self.assertIn(('key', ('control+up',)), self.desk.events)
        self.assertIn(('wheel', (-16,), {'horizontal': False}), self.desk.events)

    def test_partial_failure_stays_consumed(self):
        action_id = self.receipt()
        with patch.object(self.desk, 'click', side_effect=base.Fail('delivery failed')):
            with self.assertRaises(base.Fail): grounding.execute(self.desk, action_id)
        with self.assertRaisesRegex(base.Fail, 'consumed'): grounding.execute(self.desk, action_id)
        self.assertEqual(self.desk.events, [])

    def test_legacy_hand_compatibility(self):
        desk.hand._run(self.desk, 'click 5 6\nkey ctrl+s\ntype abc\ndrag 1 2 3 4')
        self.assertEqual(len(self.desk.events), 4)
        desk.hand._run(self.desk, 'desk.move(2, 3)')
        self.assertEqual(self.desk.events[-1], ('move', (2, 3)))

    def test_measure_denominator_exclusions_and_scope(self):
        screen = self.desk.capture()
        spec = dict(capture=str(self.work / (screen['id'] + '.json')), bounds=[0, 0, 21, 11], exclusions=[[0, 0, 2, 2]],
                    classes=[dict(name='color', rgb=[60, 40, 20]), dict(name='overlap', rgb=[60, 40, 20])])
        path = self.work / 'spec.json'
        json_file(path, spec)
        result = measure.run(self.app, path)
        self.assertEqual(result['denominator'], 227)
        self.assertEqual(result['counts'], dict(color=227, overlap=0, other=0))
        self.assertEqual(sum(result['counts'].values()), result['denominator'])
        self.assertEqual(result['scope'], 'visible_region')
        spec['bounds'] = [0, 0, 22, 11]
        json_file(path, spec)
        with self.assertRaises(base.Fail): measure.run(self.app, path)

    def test_clock_forwarding_and_ledger_seal(self):
        self.assertIsNone(base.clock()['clock']['used_seconds'])
        memory.enter(self.work, 'unknown timing', 'open')
        self.assertIn('used unknown', (self.work / 'ledger.md').read_text())
        start = time.time() - 120
        base.SET.update(wake_start=str(start), wake_end=str(start + 240), minutes='4', vision_url='http://example:123',
                        vision_model='custom', mind_model=mind.DEFAULT, decide_model='custom-d', shot_delay='0.2', coordinate_mode='pixels')
        command = base.command()
        for key in ('wake-start', 'wake-end', 'vision-url', 'vision-model', 'mind-model', 'decide-model', 'shot-delay', 'coordinate-mode'):
            self.assertIn('--' + key, command)
        self.assertAlmostEqual(base.clock()['clock']['used_seconds'], 120, delta=1)
        memory.enter(self.work, 'final lesson', 'deadline')
        ledger = (self.work / 'ledger.md').read_text()
        self.assertIn('used 2.0 of 4 min, deadline', ledger)
        self.assertEqual(ledger.count('## wake test '), 1)

    def test_prompts_provenance_and_rotation(self):
        sentence = 'READ ALL PYTHON FILES end-to-end. Paint a cat.'
        (self.work / 'story.txt').write_text('old story')
        text = mind.prompt(self.app, sentence, None, None, main.docs_of({}))
        self.assertIn('inherited; not current progress', text)
        self.assertIn('No end time was given', text)
        source = self.work / 's.txt'
        source.write_text('current first chat')
        memory.story.run(self.app, source)
        first = mind.prompt(self.app, sentence, 20, time.time() + 1200, ())
        self.assertIn('current wake', first)
        self.assertIn('test:1', first)
        self.assertIn('After 9 completed tool calls', first)
        self.assertIn('reset after 42 completed tool calls', first)
        self.assertIn('then continue the task', first)
        os.environ['TRIDENT_CHAT_ID'] = 'test:2'
        source.write_text('updated after rotation')
        memory.story.run(self.app, source)
        second = mind.prompt(self.app, sentence, 20, time.time() + 1200, ())
        self.assertIn('updated after rotation', second)
        self.assertIn('test:2', second)
        self.assertTrue(second.endswith(sentence))
        self.assertIn('20 percent', second)
        self.assertRegex(mind.budget(20, time.time()), r'\d\d:\d\d:\d\d')

    def test_measured_whole_wake_and_conflicts(self):
        past = self.work / 'run_past.md'
        past.write_text('## 10:00:00 windows\n\n{}\n\n## 10:01:00 capture\n\n{"id":"a"}\n\n## 10:01:01 capture\n\n{"id":"b"}\n')
        ledger = '## wake past date\nNo capture was taken. This wake took its first action at 10:02:00.\n'
        result = evidence.context(self.work, 'test', ledger)
        self.assertEqual(len(result['contradictions']), 2)
        base.event('component', dict(id='c'), component='capture')
        base.event('capture_saved', dict(id='guard-internal'))
        for _ in range(2): base.event('agent', dict(type='tool_call', subtype='completed'))
        result = evidence.context(self.work, 'test', ledger)['current']
        self.assertEqual(result['capture_ids'], ['c', 'guard-internal'])
        self.assertEqual(result['component_actions'], dict(capture=1))
        self.assertEqual(result['harness_calls'], 2)
        base.configure(self.work, 'failed-jsonl-only')
        base.event('wake_error', dict(error='startup failure'))
        base.configure(self.work, 'next')
        learned = memory.learn(self.work)
        self.assertEqual(learned['errors'], 1)
        self.assertIn('failed-jsonl-only', [row['run'] for row in evidence.context(self.work, 'next', '')['historical']])

    def test_numeric_probabilities_unknown_cost(self):
        self.assertEqual(decide.format_noul('x', .04), 'x: P(yes)=0.04')
        self.assertEqual(decide.summarize('x', dict(type='noul')), 'x: P(yes)=unknown')
        self.assertIsNone(evidence.cost(None)['estimated_usd'])
        self.assertIsNone(evidence.cost(dict(prompt_tokens=100))['estimated_usd'])
        rates = dict(usage_schema='input_includes_cached', input_tokens=4, cached_input_tokens=1, output_tokens=12)
        self.assertAlmostEqual(evidence.cost(dict(input_tokens=100, cached_input_tokens=20, output_tokens=5), rates)['estimated_usd'], .0004)
        base.event('eye_response', dict(model='fara', usage=dict(prompt_tokens=10, completion_tokens=4)))
        base.event('agent', dict(type='result', usage=dict(total_tokens=100)))
        summary = evidence.usage_summary(base.RUN)
        self.assertEqual(len(summary['calls']), 2)
        self.assertIn('test:1', summary['chats'])
        self.assertIsNone(summary['tokens'])

    def test_measured_original_history_contradictions(self):
        # Read-only fixture: existing journals are not opened as SQLite or edited.
        ledger = (ROOT / 'ledger.md').read_text(encoding='utf-8') if (ROOT / 'ledger.md').exists() else ''
        if not ledger:
            self.skipTest('historical fixture is intentionally not published')
        result = evidence.context(ROOT, 'test-not-a-wake', ledger)
        claims = result['contradictions']
        self.assertEqual(len(claims), 3)
        self.assertEqual(len(claims[0]['capture_ids']), 8)
        self.assertEqual(len(claims[1]['capture_ids']), 4)
        self.assertEqual(claims[2]['first_component'], '12:26:30')

    def test_advertised_perception_contract(self):
        docs = main.docs_of({})
        text = '\n'.join(docs)
        self.assertEqual(len(docs), 14)
        self.assertIn('fara ACTION_ID', text)
        self.assertIn('not a security boundary', text)
        self.assertNotIn('improvise', text.lower())
        self.assertNotIn('then yes or no', text)
        self.assertIn('do not request a box or attach a screenshot', text)
        self.assertIn('Ordinary source-file reads and nonvisual', mind.MIND)
        self.assertEqual((mind.CHAT_CALLS, mind.CHAT_CHECKPOINT_CALLS, mind.DEFAULT), (42, 9, 'grok-4.7-xhigh-fast'))

    def test_measure_action_refs(self):
        first = self.receipt(point=[0, 0])
        # Both points must refer to the identical pinned bitmap.
        second = self.receipt(point=[1000, 1000])
        screen = json.loads((self.work / 'screen.json').read_text())
        spec = dict(capture=str(self.work / (screen['id'] + '.json')), action_refs=[first, second], exclusions=[], classes=[])
        path = self.work / 'spec.json'
        json_file(path, spec)
        result = measure.run(self.app, path)
        self.assertEqual(result['bounds'], [0, 0, 20, 10])
        self.assertEqual(result['counts'], {'other': 200})

    def test_story_hash_detects_external_write_and_archives_lessons(self):
        source = self.work / 's.txt'
        source.write_text('one')
        memory.story.run(self.app, source)
        self.assertEqual(memory.snapshot(self.work)[2]['run'], 'test')
        (self.work / 'story.txt').write_text('external')
        self.assertIsNone(memory.snapshot(self.work)[2]['run'])
        memory.enter(self.work, 'earlier lesson', 'open')
        memory.enter(self.work, 'replaced lesson', 'completed')
        archive = [json.loads(line) for line in (self.work / 'ledger.history.jsonl').read_text().splitlines()]
        self.assertIn('earlier lesson', archive[-1]['previous'])

    def test_interprocess_journal_append(self):
        code = 'import base,sys;from pathlib import Path;base.configure(Path(sys.argv[1]),"parallel");[(base.log("test",str(i)*5000),base.event("test",{"i":i,"data":"x"*5000})) for i in range(15)]'
        children = [subprocess.Popen([sys.executable, '-B', '-c', code, str(self.work)], env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=0x08000000) for _ in range(4)]
        for child in children:
            output, errors = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, errors)
        rows = [json.loads(line) for line in (self.work / 'run_parallel.jsonl').read_text().splitlines()]
        self.assertEqual(len(rows), 60)
        self.assertEqual((self.work / 'run_parallel.md').read_text().count('## '), 60)

    def test_journal_port_identity(self):
        with patch.dict(os.environ):
            os.environ.pop('TRIDENT_JOURNAL', None)
            with patch('base._port_open', return_value=True), patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"service":"other"}')), patch('base.subprocess.Popen') as spawn:
                with self.assertRaisesRegex(base.Fail, 'unrelated listener'): base.ensure_journal()
                spawn.assert_not_called()
            identity = json.dumps(dict(service='trident-journal', code=str(base.CODE))).encode()
            with patch('base._port_open', return_value=True), patch('urllib.request.urlopen', return_value=io.BytesIO(identity)), patch('base.subprocess.Popen') as spawn:
                base.ensure_journal()
                spawn.assert_not_called()

    def test_guard_stopped_and_deadline(self):
        action_id = self.receipt()
        for stopped in (True, False):
            if stopped: (self.work / 'stop').touch()
            else: base.SET['wake_end'] = str(time.time() - 1)
            with self.assertRaises(base.Fail): grounding.execute(self.desk, action_id)
            self.assertEqual(self.desk.events, [])
            (self.work / 'stop').unlink(missing_ok=True)

    def test_drive_rotation_with_fake_components_only(self):
        self.desk.open_job = Mock(return_value=1)
        self.desk.close_job = Mock()
        source = self.work / 's.txt'
        seen_prompts = []
        def fake_spawn(app, model, path, job_name):
            seen_prompts.append(path.read_text(encoding='utf-8'))
            self.assertTrue(base.SET['wake_start'])
            self.assertTrue(base.SET['wake_end'])
            return SimpleNamespace(poll=lambda: 0, wait=lambda **kw: 0, returncode=0, stdout=io.BytesIO())
        results = iter(['rotate', 'completed'])
        def fake_read(proc, app, end):
            result = next(results)
            source.write_text('checkpoint before rotation')
            memory.story.run(app, source)
            memory.enter(self.work, 'lesson', 'open')
            return result, 'result'
        with patch('mind.spawn', side_effect=fake_spawn), patch('mind.read_agent', side_effect=fake_read):
            result = mind.drive(self.app, 'Owner READ ALL PYTHON FILES', 20, lambda: ())
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(len(seen_prompts), 2)
        self.assertIn('checkpoint before rotation', seen_prompts[1])
        self.assertIn('test:1', seen_prompts[1])
        self.assertIn('completed', (self.work / 'ledger.md').read_text())
        self.assertEqual(self.desk.open_job.call_count, 2)
        self.assertEqual(self.desk.events, [])

    def test_drive_startup_failure_seals_and_closes_job(self):
        self.desk.open_job = Mock(return_value=12)
        self.desk.close_job = Mock()
        with patch('mind.spawn', side_effect=base.Fail('launch failed')):
            with self.assertRaises(base.Fail): mind.drive(self.app, 'owner', 20, lambda: ())
        self.desk.close_job.assert_any_call(12)
        self.assertIn('failed', (self.work / 'ledger.md').read_text())

    def test_input_failure_releases_buttons_and_keys(self):
        events = []
        def send(flags, *args, **kwargs):
            events.append(flags)
            if flags == desk.LEFT_DOWN: raise base.Fail('failed')
        real = desk.Desk(self.work)
        with patch('desk.send', side_effect=send), patch.object(real, 'point', return_value=(1, 2)), patch('desk.time.sleep'):
            with self.assertRaises(base.Fail): real.click(500, 500)
            self.assertEqual(events[-1], desk.LEFT_UP)
            with self.assertRaises(base.Fail): real.drag([(1, 2), (3, 4)])
            self.assertEqual(events[-1], desk.LEFT_UP)
        keys = []
        def key(vk, up=False):
            keys.append((vk, up))
            if vk == 65 and not up: raise base.Fail('failed')
        with patch.object(real, '_key', side_effect=key), patch('desk.time.sleep'):
            with self.assertRaises(base.Fail): real.key('ctrl+a')
        self.assertEqual(keys[-2:], [(65, True), (17, True)])
        with patch.object(desk.user32, 'SendInput', return_value=0):
            with self.assertRaisesRegex(base.Fail, 'did not deliver'): real._key(65)
        with patch.object(real, '_key'), patch.object(real, 'key') as key, patch('desk.time.sleep'):
            real.type_text('a\r\nb\tc')
        self.assertEqual(key.call_args_list, [unittest.mock.call('enter'), unittest.mock.call('tab')])

    def test_concurrent_logs_and_multi_objects(self):
        def write(index):
            base.log('test', str(index) * 10000)
            base.event('component', dict(index=index, data='z' * 10000), component='story')
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            list(pool.map(write, range(30)))
        rows = [json.loads(line) for line in base.RUN.with_suffix('.jsonl').read_text().splitlines()]
        self.assertEqual(len(rows), 30)
        self.assertTrue(all(len(row['payload']['data']) == 10000 for row in rows))
        md = base.RUN.read_text()
        self.assertEqual(md.count('## '), 30)
        self.assertEqual(evidence.objects('{"story":1}\n{"ledger":2}\n{"stop":true}'), [dict(story=1), dict(ledger=2), dict(stop=True)])

    def test_cli_compatibility_dry_only(self):
        goal = self.work / 'q.txt'
        goal.write_text('Paint cyan visible?')
        for args in (['--eye-file', str(goal)], ['--mind-file', str(goal), '--minutes', '20'], ['--hands-file', str(goal)], ['--story-file', str(goal)], ['--ledger-file', str(goal)], ['--region', 'NONE'], ['--place', '1', '0', '0', '500', '1000']):
            proc = subprocess.run([sys.executable, '-B', str(ROOT / 'main.py'), '--work', str(self.work), '--dry', *args], capture_output=True, env=os.environ.copy(), timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(json.loads(proc.stdout)['dry'])


class ProcessTests(BodyCase):
    def child(self, code):
        proc = subprocess.Popen([sys.executable, '-u', '-c', code], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=0x08000000)
        def cleanup():
            if proc.poll() is None: proc.kill()
            proc.wait(timeout=5)
            proc.stdout.close()
        self.addCleanup(cleanup)
        return proc

    def test_silent_deadline_and_stop_grace(self):
        for stopped in (False, True):
            proc = self.child('import time; time.sleep(10)')
            if stopped: (self.work / 'stop').touch()
            started = time.monotonic()
            result = mind.read_agent(proc, self.app, None if stopped else time.time() + .12, stop_grace=.12)
            self.assertEqual(result[0], 'stopped' if stopped else 'deadline')
            self.assertLess(time.monotonic() - started, .7)
            (self.work / 'stop').unlink(missing_ok=True)

    def test_result_failure_and_unexpected_eof(self):
        for event, reason in ((None, 'failed_eof'), (dict(type='result', is_error=True), 'failed_agent'), (dict(type='result', result='ok'), 'completed')):
            code = 'pass' if event is None else 'print(' + repr(json.dumps(event)) + ',flush=True)'
            proc = self.child(code)
            self.assertEqual(mind.read_agent(proc, self.app, None)[0], reason)
        proc = self.child('print(' + repr(json.dumps(dict(type='result', result='claimed success'))) + ',flush=True);raise SystemExit(3)')
        self.assertEqual(mind.read_agent(proc, self.app, None)[0], 'failed_agent')

    def test_rotation_42_waits_until_idle(self):
        for count in (9, 41, 42):
            events = [dict(type='tool_call', subtype='completed')] * count + [dict(type='result', result='ok')]
            proc = self.child('print(' + repr('\n'.join(map(json.dumps, events))) + ',flush=True);import time;time.sleep(.2)')
            self.assertEqual(mind.read_agent(proc, self.app, None)[0], 'rotate' if count == 42 else 'completed')
        events = [dict(type='tool_call', subtype='started')] * 43 + [dict(type='tool_call', subtype='completed')] * 42 + [dict(type='assistant', message=dict(content='still running')), dict(type='tool_call', subtype='completed')]
        proc = self.child('print(' + repr('\n'.join(map(json.dumps, events))) + ',flush=True);import time;time.sleep(.2)')
        result = mind.read_agent(proc, self.app, None)
        self.assertEqual(result, ('rotate', 'still running'))

    def test_job_failure_does_not_launch_vendor(self):
        marker = self.work / 'vendor-started'
        proc = subprocess.run([sys.executable, '-B', str(ROOT / 'job_boot.py'), 'Trident-nonexistent-' + uuid.uuid4().hex,
                               sys.executable, '-c', 'from pathlib import Path;Path(' + repr(str(marker)) + ').touch()'], capture_output=True, timeout=5, creationflags=0x08000000)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(b'vendor was not launched', proc.stderr)
        self.assertFalse(marker.exists())
        with patch.object(desk.kernel32, 'CreateJobObjectW', return_value=123), patch.object(desk.kernel32, 'SetInformationJobObject', return_value=0), patch.object(desk.kernel32, 'CloseHandle') as close:
            with self.assertRaises(base.Fail): desk.Desk(self.work).open_job()
            close.assert_called_once_with(123)

    def test_owned_job_descendant_cleanup(self):
        real = desk.Desk(self.work)
        name = 'Trident-test-' + uuid.uuid4().hex
        job = real.open_job(name=name)
        marker = self.work / 'child.pid'
        code = 'import subprocess,sys,time;from pathlib import Path;p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);Path(' + repr(str(marker)) + ').write_text(str(p.pid));print("ready",flush=True);time.sleep(60)'
        proc = self.child('import job_boot,sys;sys.exit(job_boot.launch(' + repr(name) + ',[sys.executable,"-u","-c",' + repr(code) + ']))')
        try:
            until = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < until:
                time.sleep(.02)
            self.assertTrue(marker.exists(), 'owned child was not launched')
            pid = int(marker.read_text())
        finally:
            real.close_job(job)
        proc.wait(timeout=5)
        import ctypes
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
        api.OpenProcess.restype = ctypes.c_void_p
        api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        api.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = api.OpenProcess(0x100000, False, pid)
        if handle:
            try: self.assertEqual(api.WaitForSingleObject(handle, 3000), 0)
            finally: api.CloseHandle(handle)


if __name__ == '__main__':
    unittest.main()
