"""Phase 3 offline contracts: no CUDA, desktop input, model or external service launched."""
import asyncio
import base64
import io
import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from PIL import Image
from bus import Server
from tools import Tools, parse_visual_boxes, write_full_resolution

ROOT = Path(__file__).resolve().parent


class VisionPixelTests(unittest.TestCase):
    def test_image_preparation_retains_full_resolution_and_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'image.png'
            image = Image.new('RGB', (1920, 1080), (18, 27, 39))
            image.putpixel((1919, 1079), (254, 14, 4))
            self.assertEqual(write_full_resolution(image, target), (1920, 1080))
            with Image.open(target) as result:
                self.assertEqual(result.size, (1920, 1080))
                self.assertEqual(result.getpixel((1919, 1079)), (254, 14, 4))
                self.assertEqual(result.tobytes(), image.tobytes())

    def test_archived_screenshot_is_full_resolution(self):
        source = ROOT.parent.parent / 'Trident-runner-h-extracted' / 'run_20261011T125752955274' / 'house-1791716448425595900.png'
        if not source.is_file(): self.skipTest('Archival screenshot not mounted')
        with tempfile.TemporaryDirectory() as directory, Image.open(source) as src:
            output = Path(directory) / 'vision.png'
            write_full_resolution(src, output)
            with Image.open(output) as sent:
                self.assertEqual(src.size, (1920, 1080))
                self.assertEqual(sent.size, src.size)
                self.assertEqual(src.tobytes(), sent.tobytes())

    def test_model_payload_has_exact_pixels_and_prompt(self):
        class StubServer:
            async def chat(self, messages, **kwargs):
                self.messages, self.options = messages, kwargs
                return 'I can see a desktop.'
        with tempfile.TemporaryDirectory() as directory:
            subject = Tools.__new__(Tools)
            subject.name, subject.run = 'tools', Path(directory)
            subject.cfg = {'vision': {'text_system': 'Observe only', 'boxes_system':'Return JSON', 'temperature':0}}
            subject.server = StubServer()
            image = Image.new('RGB', (1600, 900), 'white')
            image.putpixel((1599, 899), (2, 3, 4))
            path, reply = asyncio.run(subject.look(image, 'What is visible?', False))
            self.assertEqual(reply, 'I can see a desktop.')
            messages = subject.server.messages
            self.assertEqual(messages[1]['content'][0], {'type':'text', 'text':'What is visible?'})
            blob = messages[1]['content'][1]['image_url']['url']
            self.assertTrue(blob.startswith('data:image/png;base64,'))
            with Image.open(io.BytesIO(base64.b64decode(blob.split(',',1)[1]))) as decoded:
                self.assertEqual(decoded.size, image.size)
                self.assertEqual(decoded.getpixel((1599,899)), (2,3,4))
            event = json.loads((subject.run/'events.jsonl').read_text().splitlines()[0])
            self.assertEqual(event['event'], 'vision_input')
            self.assertEqual(event['submitted_size'], [1600, 900])
            self.assertEqual(Path(event['image']),path)


class BoxAdapterTests(unittest.TestCase):
    def test_plain_and_fenced_array(self):
        array='[{"bbox_2d":[3,4,10,20],"label":"menu","image_id":0}]'
        self.assertEqual(parse_visual_boxes(array), parse_visual_boxes('```json\n'+array+'\n```'))
        self.assertEqual(parse_visual_boxes('[]'), [])

    def test_extraneous_text_is_not_accepted(self):
        for raw in ('These are the boxes:\n[]', '{"boxes": []}', '```json\n[]\n``` additional'):
            with self.subTest(raw=raw), self.assertRaises((ValueError,json.JSONDecodeError)):
                parse_visual_boxes(raw)


class ModelResponseTests(unittest.TestCase):
    def chat_result(self, content, reason='stop', usage=None):
        with tempfile.TemporaryDirectory() as directory:
            dev = type('Fake', (), {})()
            dev.run, dev.name = Path(directory), 'tools'
            dev.cfg = {'sampling': {'temperature':0.2, 'top_k':50}}
            server = Server(dev, {'model':'lfm', 'max_tokens':8192})
            response = {'choices':[{'finish_reason':reason, 'message':{'content':content}}],
                        'usage': usage or {'prompt_tokens':124, 'completion_tokens':31, 'total_tokens':155}}
            server.request = lambda endpoint, body: json.dumps(response).encode()
            try:
                reply = asyncio.run(server.chat([{'role':'user','content':'hi'}]))
                return reply, json.loads((dev.run/'events.jsonl').read_text().splitlines()[0])
            except RuntimeError as exc:
                return exc, json.loads((dev.run/'events.jsonl').read_text().splitlines()[0])

    def test_result_and_telemetry(self):
        reply, event = self.chat_result('It is visible.')
        self.assertEqual(reply, 'It is visible.')
        self.assertEqual(event['event'], 'model_http_response')
        self.assertEqual((event['prompt_tokens'],event['completion_tokens'],event['finish_reason']),(124,31,'stop'))

    def test_cutoff_fails_instead_of_claiming_success(self):
        reply, event = self.chat_result('partial output', 'length')
        self.assertIsInstance(reply, RuntimeError)
        self.assertIn('truncated', str(reply))
        self.assertEqual(event['finish_reason'], 'length')

    def test_empty_content_fails(self):
        reply, event = self.chat_result(None)
        self.assertIsInstance(reply, RuntimeError)
        self.assertIn('no text',str(reply))


class SettingsTests(unittest.TestCase):
    def test_model_native_capacity_and_unquantized_weights_selected(self):
        data = tomllib.loads((ROOT/'config.toml').read_text())
        self.assertEqual(data['vision']['context'], 32768)
        self.assertEqual(data['vision']['max_tokens'],8192)
        self.assertNotIn('image_side',data['vision'])
        argv = data['llama']['command']
        self.assertNotIn('--image-max-tokens', argv)
        self.assertNotIn('--image-min-tokens', argv)
        self.assertIn('--check-tensors',argv)
        self.assertIn('--fit',argv)
        self.assertEqual(argv[argv.index('--fit')+1], 'off')
        self.assertEqual(argv[argv.index('--n-gpu-layers')+1], '999')
        self.assertEqual(argv[argv.index('--split-mode')+1], 'none')
        self.assertEqual(argv[argv.index('--device')+1], 'CUDA0')
        self.assertIn('BF16',data['llama']['model_file'])
        self.assertIn('BF16',data['llama']['projector_file'])
        for name in (data['llama']['model_file'], data['llama']['projector_file']):
            self.assertIn(name, data['downloads']['models'])


if __name__=='__main__':unittest.main()
