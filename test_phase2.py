"""Hermetic Mind action/check contract tests. No desktop, processes or network."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from jsonschema import Draft202012Validator
from mind import Mind
from bus import Frame

ACTION = json.dumps({'do':'stroke','with':'click 500 500','expect':'pixel changes','check':'Did a pixel change?'})

class FakeEngine:
    async def reply(self, prompt, schema): return ACTION

class FakeBus:
    def __init__(self, result):
        self.result = result.encode()
        self.calls = []
    async def transfer(self, frame):
        self.calls.append((frame.address, frame.register, frame.text, frame.read))
        if (frame.address, frame.register) == ('14', '04'): return self.result
        if (frame.address, frame.register) == ('14', '01'): return b'fresh-check.png'
        if (frame.address, frame.register) == ('14', '03'): return b'No'
        if (frame.address, frame.register) == ('16', '01'): return b''
        raise AssertionError(f'unexpected frame: {frame}')

class CheckResultTests(unittest.TestCase):
    def trial(self, stroke_result):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root/'prompt.txt').write_text('Policy')
            mind = Mind.__new__(Mind)
            mind.number=0
            mind.busy=False
            mind.generation=0
            mind.goal=''
            mind.ledger=[]
            mind.run=root
            mind.address='16'
            mind.cfg={'address':{'telegram':'11','ears':'12','tools':'14','mind':'16'}}
            mind.cfg = type('Cfg', (dict,), {'root':root})(mind.cfg)
            mind.settings={'context_chars':1000000,'turn_seconds':5}
            mind.engine=FakeEngine()
            mind.by_name={'stroke':('14','04',True)}
            mind.template=json.dumps({'stroke':{'arguments':'','returns':''}})
            mind.schema={'type':'object','additionalProperties':False,'required':['do','with','expect','check'],
                         'properties':{'do':{'enum':['stroke']},'with':{'type':'string'},
                                       'expect':{'type':'string','minLength':1},'check':{'type':'string'}}}
            mind.validator=Draft202012Validator(mind.schema)
            mind.bus=FakeBus(stroke_result)
            asyncio.run(mind.work('11',Frame('16','01','Test task')))
            return mind.bus.calls, mind.ledger[0], [json.loads(line) for line in (root/'events.jsonl').read_text().splitlines()]
    def test_successful_stroke_reuses_exact_after_image(self):
        calls, record, events=self.trial('after-stroke.png')
        self.assertFalse(any(reg=='01' and addr=='14' for addr,reg,*_ in calls))
        self.assertEqual(record['check_image'],'after-stroke.png')
        self.assertTrue(any(c[2].startswith('after-stroke.png\ntext\n') for c in calls if c[1]=='03'))
        self.assertFalse(any(e['event']=='action_check_fallback' for e in events))
    def test_outcome_unknown_uses_new_screen_and_preserves_error(self):
        actual = json.dumps({'started':True,'error':'RuntimeError','detail':'device error'})
        calls, record, events=self.trial(actual)
        self.assertEqual(sum(1 for c in calls if c[:2]==('14','01')),1)
        self.assertEqual(record['actual'],actual)
        self.assertEqual(record['check_image'],'fresh-check.png')
        self.assertTrue(any(c[2].startswith('fresh-check.png\ntext\n') for c in calls if c[1]=='03'))
        self.assertEqual(sum(e['event']=='action_check_fallback' for e in events),1)
        self.assertEqual(record['check_answer'],'No')

if __name__=='__main__':unittest.main()
