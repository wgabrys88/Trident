"""Offline unit regressions: python -m unittest discover -s unit_tests -v."""
import json
import unittest

from perception import InvalidPerception, map_box, parse_scene, select_target, validate_target


def fixture():
    return {
        'scene': 'Chrome displays chess.com with a browser profile popup over an aborted online game.',
        'premise': 'contradicted',
        'answer': 'No bot chooser is visible. "Your Chrome" belongs to Chrome profile controls, not a bot.',
        'surfaces': [
            {'id': 'chrome', 'kind': 'browser_chrome', 'box': [700, 0, 1000, 400],
             'description': 'Chrome browser toolbar and profile menu at the top right.'},
            {'id': 'game', 'kind': 'app_content', 'box': [0, 100, 1000, 1000],
             'description': 'Chess.com online game and analysis board, separate from profile popup.'},
            {'id': 'ad', 'kind': 'advertisement', 'box': [820, 450, 1000, 850],
             'description': 'An advertisement occupies a right-side region.'}],
        'elements': [
            {'id': 'profile', 'surface_id': 'chrome', 'kind': 'button', 'box': [810, 100, 930, 160],
             'text': 'Your Chrome', 'description': 'Browser profile control on a menu near the top toolbar. '
             'It does not belong to the Chess.com content area. Clicking it may change Chrome state.',
             'relations': 'Above and right of the online board.', 'actionable': 'yes'},
            {'id': 'analysis', 'surface_id': 'game', 'kind': 'text', 'box': [600, 300, 800, 370],
             'text': 'Stockfish 19 Lite', 'description': 'Analysis label within Chess.com, not a bot-selection button.',
             'relations': 'Within the analysis pane.', 'actionable': 'no'},
            {'id': 'bots', 'surface_id': 'game', 'kind': 'link', 'box': [90, 400, 200, 455],
             'text': 'Play Bots', 'description': 'Link inside Chess.com navigation with nearby Play Online and other modes.',
             'relations': 'Below the play-mode list.', 'actionable': 'yes'}],
        'uncertainties': ['A chess piece obscured by the modal cannot be located reliably.']}


def receipt(scene=None):
    return dict(outcome='completed', complete=True, structured=True, operation_id='retina-23',
                image='screen-23.png', image_at=123, scene=scene or fixture(),
                geometry=dict(crop=[0, 0, 1920, 1080], desktop=[0, 0, 1920, 1080],
                              window={'id': '333', 'title': 'Chrome'}))


class StructuredLookTests(unittest.TestCase):
    def test_rich_descriptions_are_preserved_verbatim(self):
        x = fixture()
        x['elements'][0]['description'] += (' More visual and geometric evidence.' * 20)
        self.assertEqual(parse_scene(json.dumps(x))['elements'][0]['description'], x['elements'][0]['description'])

    def test_browser_chrome_not_a_chess_app_target(self):
        x = receipt()
        with self.assertRaisesRegex(InvalidPerception, 'Wrong owning surface'):
            select_target(x, 'profile', 'app_content')
        allowed = select_target(x, 'profile', 'browser_chrome')
        self.assertEqual(allowed['surface_kind'], 'browser_chrome')
        self.assertEqual(allowed['element_id'], 'profile')

    def test_analysis_text_not_actionable(self):
        with self.assertRaisesRegex(InvalidPerception, 'not positively'):
            select_target(receipt(), 'analysis')

    def test_valid_chess_link_selectable(self):
        t = select_target(receipt(), 'bots')
        self.assertEqual(t['observation_id'], 'retina-23')
        self.assertTrue(t['box'][0] < t['box'][2])

    def test_stale_screenshot_fails(self):
        t = select_target(receipt(), 'bots')
        with self.assertRaisesRegex(InvalidPerception, 'superseded'):
            validate_target(t, dict(image='screen-24.png', desktop=[0,0,1920,1080], window={'id': '333'}))

    def test_window_switch_fails(self):
        t = select_target(receipt(), 'bots')
        with self.assertRaisesRegex(InvalidPerception, 'Foreground window changed'):
            validate_target(t, dict(image='screen-23.png', desktop=[0,0,1920,1080], window={'id': '999'}))

    def test_invalid_model_response_fails_closed(self):
        for text in ('clicked computer', '{"scene":"bad"}', 'not json'):
            with self.subTest(text=text), self.assertRaises(InvalidPerception):
                parse_scene(text)

    def test_false_surface_membership_fails(self):
        scene = fixture()
        scene['elements'][0]['surface_id'] = 'game'
        with self.assertRaisesRegex(InvalidPerception, 'crosses its declared surface'):
            parse_scene(json.dumps(scene))

    def test_one_word_actionable_description_is_rejected(self):
        scene = fixture()
        scene['elements'][0]['description'] = 'green'
        with self.assertRaisesRegex(InvalidPerception, 'too short'):
            parse_scene(json.dumps(scene))

    def test_bool_not_valid_coordinate(self):
        scene = fixture()
        scene['elements'][0]['box'][0] = True
        with self.assertRaisesRegex(InvalidPerception, 'invalid normalized'):
            parse_scene(json.dumps(scene))

    def test_crop_geometry_nonzero_desktop_origin(self):
        geometry = dict(crop=[10, 20, 210, 220], desktop=[-1920, -100, 3840, 1080])
        self.assertEqual(map_box([0, 0, 1000, 1000], geometry), [-1910, -80, -1711, 119])

    def test_incomplete_receipt_cannot_select(self):
        look = receipt()
        look['complete'] = False
        with self.assertRaisesRegex(InvalidPerception, 'completed'):
            select_target(look, 'bots')

    def test_app_element_within_surface_is_accepted(self):
        scene = fixture()
        scene['elements'][2]['box'] = [200, 200, 950, 550]
        # Still within the containing game region: spatial check preserves image geometry.
        self.assertEqual(parse_scene(json.dumps(scene))['elements'][2]['box'][2], 950)


if __name__ == '__main__':
    unittest.main()
