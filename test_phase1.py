"""Pure contract tests. No runner, desktop, network, or account access."""
import unittest

from jsonschema import Draft202012Validator
from mind import ModelOutputFault, decode_action
from tools import parse_keys
from bus import Frame

SCHEMA = {
    'type': 'object',
    'required': ['do', 'with', 'expect', 'check'],
    'additionalProperties': False,
    'properties': {
        'do': {'enum': ['stroke', 'screenshot', 'done']},
        'with': {'type': 'string'},
        'expect': {'type': 'string', 'minLength': 1},
        'check': {'type': 'string'},
    },
}
ACTION = '{"do":"stroke","with":"key ctrl+l","expect":"Chrome address bar is focused","check":"Is it focused?"}'


class OutputAdapterTests(unittest.TestCase):
    def setUp(self):
        self.validator = Draft202012Validator(SCHEMA)

    def test_plain_valid(self):
        value, normalized = decode_action(ACTION, self.validator)
        self.assertFalse(normalized)
        self.assertEqual(value['with'], 'key ctrl+l')

    def test_explanatory_prefix(self):
        value, normalized = decode_action('I should focus the address bar.\n' + ACTION, self.validator)
        self.assertTrue(normalized)
        self.assertEqual(value['do'], 'stroke')

    def test_fenced_action(self):
        value, normalized = decode_action('```json\n' + ACTION + '\n```', self.validator)
        self.assertTrue(normalized)
        self.assertEqual(value['do'], 'stroke')

    def test_rejects_multiple_objects(self):
        with self.assertRaisesRegex(ModelOutputFault, 'ambiguous'):
            decode_action(ACTION + '\n' + ACTION, self.validator)

    def test_rejects_nonobject_json(self):
        with self.assertRaises(ModelOutputFault):
            decode_action('[1,2]', self.validator)

    def test_rejects_nonjson_prose(self):
        with self.assertRaises(ModelOutputFault):
            decode_action('No action proposed.', self.validator)

    def test_rejects_wrong_action(self):
        with self.assertRaisesRegex(ModelOutputFault, 'schema'):
            decode_action(ACTION.replace('stroke', 'hack', 1), self.validator)

    def test_rejects_unknown_fields(self):
        with self.assertRaisesRegex(ModelOutputFault, 'schema'):
            decode_action(ACTION[:-1] + ',"extra":"x"}', self.validator)

    def test_rejects_oversized(self):
        with self.assertRaisesRegex(ModelOutputFault, 'oversized'):
            decode_action('x' * 1_000_001, self.validator)


class KeyContracts(unittest.TestCase):
    valid = {'ctrl', 'shift', 'alt', 'win', 's', 'l', 'enter'}

    def test_single_key(self):
        self.assertEqual(parse_keys('enter', self.valid), ['enter'])

    def test_shortcuts_seen_in_run(self):
        for text in ('ctrl+shift+s', 'ctrl+s', 'ctrl+l'):
            with self.subTest(shortcut=text):
                self.assertEqual(parse_keys(text, self.valid), text.split('+'))

    def test_reject_invalid_chord(self):
        for text in ('ctrl++s', 'ctrl+ctrl+s', 's+ctrl', 'not-a-key', 'ctrl+shift'):
            with self.subTest(shortcut=text):
                with self.assertRaises(ValueError): parse_keys(text, self.valid)


class FrameContracts(unittest.TestCase):
    def test_read_reply(self):
        frame = Frame('14', '01', 'shot.png', True)
        self.assertEqual(frame.result(frame.trace(b'path.png')), b'path.png')

    def test_write_reply(self):
        frame = Frame('16', '01', 'plan', False)
        self.assertEqual(frame.result(frame.trace()), b'')


if __name__ == '__main__': unittest.main()
