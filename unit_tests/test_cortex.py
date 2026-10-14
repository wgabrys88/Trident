"""Run the actual Cortex AST on fakes: Linux offline, no Windows/remote model needed."""
import ast
import asyncio
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest

from perception import select_target, validate_target

ROOT = Path(__file__).resolve().parents[1]

class NotAttempted(ValueError): pass

class FakeBus:
    def __init__(self, session, sender):
        self.session = session

    async def request(self, role, operation, args, *unused):
        self.session.calls.append((role, operation, args))
        if role == 'manipulator':
            after = dict(image='after.png', desktop=[0, 0, 1920, 1080],
                         window={'id': '42'}, at=time.time())
            self.session.state['screen'] = after
            return dict(outcome='completed', operation_id='action1', image='after.png', prediction=args['prediction'])
        if role == 'retina':
            return dict(outcome='completed', operation_id='look1', image='after.png', complete=True,
                        structured=True, answer='The expected app page is visible.', image_at=time.time(),
                        scene={'premise':'supported', 'scene':'App page', 'answer':'App page',
                               'elements':[], 'surfaces':[], 'uncertainties':[]})
        return dict(outcome='completed', answers={}, passed_state=args['state'])


class FakeSession:
    def __init__(self):
        self.calls = []
        self.state = dict(owner={'id':'owner'}, memory={},
                          episode={'id':'episode1', 'native_actions':0}, operations={},
                          screen={'image':'before.png', 'desktop':[0,0,1920,1080],
                                  'window':{'id':'42'}, 'at':time.time()})

    def load(self):
        return copy.deepcopy(self.state)

    def edit(self, callback):
        return callback(self.state)


# Extract only the real Cortex class from run.py, avoiding Windows-only core import.
tree = ast.parse((ROOT / 'run.py').read_text('utf-8'))
klass = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Cortex')
namespace = dict(ast=ast, asyncio=asyncio, copy=copy, json=json, time=time,
                 os=__import__('os'), Path=Path, Bus=FakeBus, NotAttempted=NotAttempted,
                 CFG={'look_seconds':60, 'decision_seconds':15, 'native_per_episode':1},
                 select_target=select_target, validate_target=validate_target)
exec(compile(ast.Module(body=[klass], type_ignores=[]), '<Cortex source>', 'exec'), namespace)
Cortex = namespace['Cortex']


class CortexEpisodeTests(unittest.TestCase):
    def test_action_look_handoff_persists_without_remember(self):
        session = FakeSession()
        async def scenario():
            cortex = Cortex(session)
            result = await cortex.native("gui.hotkey('ctrl','l')", 'Address bar receives focus')
            self.assertEqual(result['outcome'], 'completed')
            pending = session.load()['memory']['pending_action']
            self.assertEqual(pending['after_image'], 'after.png')
            self.assertNotIn('post_action_look', pending)
            await cortex.observe('Is the address bar focused?')
            self.assertTrue(session.load()['memory']['pending_action']['post_action_look']['complete'])
            # A fresh model context is required for the next native action.
            with self.assertRaisesRegex(NotAttempted, 'budget'):
                await cortex.native("gui.press('esc')", 'Dismiss focus')
            session.state['episode'] = {'id':'episode2','native_actions':0}
            renewed = Cortex(session)
            await renewed.native("gui.press('esc')", 'Dismiss focus')
            self.assertEqual(session.load()['episode']['native_actions'], 1)
        asyncio.run(scenario())

    def test_action_without_postlook_cannot_repeat(self):
        session = FakeSession()
        async def scenario():
            await Cortex(session).native("gui.press('esc')", 'Close dialog')
            session.state['episode'] = {'id':'episode2','native_actions':0}
            with self.assertRaisesRegex(NotAttempted, 'Observe the previous'):
                await Cortex(session).native("gui.press('enter')", 'Accept dialog')
        asyncio.run(scenario())

    def test_invalid_mouse_actions_never_dispatched(self):
        session = FakeSession()
        async def scenario():
            with self.assertRaisesRegex(ValueError, 'Retina-grounded'):
                await Cortex(session).native('gui.click(100,200)', 'Click supposed bot')
            self.assertFalse(session.calls)
        asyncio.run(scenario())

    def test_stroke_requires_visual_target(self):
        session = FakeSession()
        async def scenario():
            with self.assertRaisesRegex(ValueError, 'Retina-grounded'):
                await Cortex(session).native('stroke(targets, [(0,0)], click=True)', 'Draw')
        asyncio.run(scenario())

    def test_jev_receives_last_look_across_python_tool_boundaries(self):
        session = FakeSession()
        look = dict(outcome='completed', complete=True, structured=True, image='before.png', image_at=20,
                    scene={'scene':'The app content is an online chess game, not a bot chooser.',
                           'premise':'contradicted', 'answer':'No bot chooser', 'surfaces':[],
                           'elements':[], 'uncertainties':[]})
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'receipt.json'
            path.write_text(json.dumps(look), 'utf-8')
            session.state['last_retina'] = 'retina1'
            session.state['operations']['retina1'] = dict(role='retina', outcome='completed', response=str(path))
            async def scenario():
                result = await Cortex(session).decide('Computer button?', {'choice': {'type': 'choice',
                    'instructions':'Choose visual interpretation',
                    'criteria':{'confirm':'bot', 'inspect_again':'uncertain'}}})
                evidence = result['passed_state']['retina_evidence']
                self.assertEqual(evidence['scene']['premise'], 'contradicted')
                self.assertEqual(evidence['operation_id'], 'retina1')
            asyncio.run(scenario())

    def test_jev_does_not_see_stale_scene(self):
        session = FakeSession()
        session.state['last_retina'] = 'retina1'
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'receipt.json'
            path.write_text(json.dumps(dict(image='stale.png', structured=True, complete=True,
                scene={'premise':'supported', 'scene':'old', 'answer':'old'})), 'utf-8')
            session.state['operations']['retina1'] = dict(role='retina', outcome='completed', response=str(path))
            result = asyncio.run(Cortex(session).decide('Where?', {'q':{'type':'noul','instructions':'there'}}))
            self.assertFalse(result['passed_state']['retina_evidence']['available'])


if __name__ == '__main__': unittest.main()
