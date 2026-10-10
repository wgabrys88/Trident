import asyncio, hashlib, json, os, sys, time
from jsonschema import Draft202012Validator, ValidationError
from bus import Device, Frame, Refused, Server, journal, evidence

class Cli:
    def __init__(self, device):
        self.device, self.settings = device, device.settings
        self.folder = max((path for path in device.cfg.path(self.settings['versions']).iterdir() if path.is_dir()), key=lambda path: path.name)
    async def reply(self, prompt, schema):
        workspace = os.path.join(os.environ['TEMP'], 'trident-mind')
        os.makedirs(workspace, exist_ok=True)
        command = [str(self.folder / 'node.exe'), '-e', "process.argv.push(require('fs').readFileSync(0,'utf8'));require(process.argv[1]);", str(self.folder / 'index.js'), '-p', '--model', self.settings['model'], '--output-format', 'stream-json', '--workspace', workspace, '--trust', '--allowed-tools', '']
        home = os.path.join(os.environ['TEMP'], 'trident-cli-home')
        os.makedirs(home, exist_ok=True)
        drive, home_path = os.path.splitdrive(home)
        env = os.environ.copy()
        env.update(USERPROFILE=home, HOME=home, HOMEDRIVE=drive, HOMEPATH=home_path or '\\')
        process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=self.device.stderr, cwd=workspace, env=env, limit=self.settings['event_bytes'])
        texts = []
        try:
            process.stdin.write(prompt.encode())
            await process.stdin.drain()
            process.stdin.close()
            async for line in process.stdout:
                try: event = json.loads(line)
                except ValueError:
                    journal(self.device.run, 'model_transport_error', n=self.device.number,
                            payload=evidence(self.device.run, f'error-{self.device.number}', line.decode(errors='replace')))
                    raise RuntimeError('Malformed CLI event') from None
                if event['type'] == 'tool_call':
                    journal(self.device.run, 'native_tool_violation', turn=self.device.number,
                            payload=evidence(self.device.run, f'native-{self.device.number}', line.decode()))
                    raise RuntimeError('Native CLI tool call violates register-only transport')
                if event['type'] == 'result' and event.get('is_error'):
                    journal(self.device.run, 'model_transport_error', n=self.device.number, payload=evidence(self.device.run, f'error-{self.device.number}', line.decode()))
                if event['type'] == 'assistant': texts.append(''.join(part['text'] for part in event['message']['content'] if part['type'] == 'text'))
            if await process.wait(): raise RuntimeError('CLI failed; see diagnostics.log')
            if not texts: raise RuntimeError('CLI returned no assistant text')
            return texts[-1]
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

class Http:
    def __init__(self, device):
        self.device = device
        device.server = Server(device, device.cfg['llama'] | device.settings)
    async def reply(self, prompt, schema):
        return await self.device.server.chat([dict(role='user', content=prompt)], response_format=dict(type='json_object', schema=schema))

def same_refusal(previous, action):
    return previous is not None and previous == (action.get('do'), action.get('with'))

class Mind(Device):
    async def start(self):
        self.settings = self.cfg['mind'][sys.argv[2]]
        self.server, self.number, self.used, self.busy, self.generation, self.stopped, self.refused = None, 0, 0, False, 0, False, None
        self.goal, self.ledger = '', []
        self.unchecked, self.effect, self.need_check, self.stale = '', False, True, False
        self.queue = asyncio.PriorityQueue()
        self.stderr = (self.run / 'diagnostics.log').open('ab')
        self.cleanup.callback(self.stderr.close)
        self.engine = {'cli': Cli, 'http': Http}[self.settings['transport']](self)
        if self.server is not None: await self.server.start()
        self.by_name, lines = {}, []
        for role, registers in self.cfg['registers'].items():
            for register, value in registers.items():
                direction, _, rest = value.partition(':')
                action, example, returns = [part.strip() for part in rest.split('|')]
                if action == '-': continue
                if action in self.by_name: raise RuntimeError(f'Duplicate action {action}')
                self.by_name[action] = (self.cfg['address'][role], register, 'read' in direction.split('/'))
                lines.append(json.dumps({'do': action, 'with': example, 'expect': 'OUTCOME'}) + ' -> ' + returns)
        for action, spec in self.cfg['actions'].items():
            example, _, returns = spec.partition('|')
            lines.append(json.dumps({'do': action, 'with': example.strip(), 'expect': 'OUTCOME'}) + ' -> ' + returns.strip())
        self.template = '\n'.join(lines)
        names = [*self.by_name, *self.cfg['actions']]
        self.schema = dict(type='object', additionalProperties=False, required=['do', 'with', 'expect'], properties=dict(do=dict(enum=names), **{'with': dict(type='string'), 'expect': dict(type='string', minLength=1)}))
        self.validator = Draft202012Validator(self.schema)
        journal(self.run, 'model_contract', identity=self.settings['identity'],
                registers=evidence(self.run, 'registers', json.dumps(self.cfg['registers'])),
                schema=evidence(self.run, 'schema', json.dumps(self.schema)))
    async def receive(self, source, frame):
        if frame.read: return (f'{int(self.busy):02d}' if frame.register == '00' else self.settings['identity']).encode()
        owner = source in (self.cfg['address']['telegram'], self.cfg['address']['ears'], self.cfg['address']['timer'])
        if source == self.cfg['address']['timer'] and (self.busy or not self.queue.empty()): raise Refused('NOT_READY')
        if not owner and self.busy: raise Refused('NOT_READY')
        if owner: self.used, self.stopped, self.generation, self.refused = 0, False, self.generation + 1, None
        if source in (self.cfg['address']['telegram'], self.cfg['address']['ears']): (self.run / 'mind-activity').touch()
        if not owner:
            cost = (1, -(-self.cfg['bus']['self_turn_cap'] // self.cfg['bus']['fault_turns']))[frame.text.startswith('Fault:')]
            if self.used + cost > self.cfg['bus']['self_turn_cap']: raise Refused('FULL')
            self.used += cost
        self.queue.put_nowait((int(not owner), time.time_ns(), self.generation, source, frame))
        return b''
    async def worker(self):
        while True:
            _, _, generation, source, frame = await self.queue.get()
            if generation == self.generation: await self.work(source, frame)
            else: journal(self.run, 'turn_skipped', src=source, generation=generation)
    def clip(self, actual):
        return actual if len(actual) <= 4000 else actual[:4000] + '\n(truncated)'
    def record(self, action, actual):
        shown = self.clip(actual)
        self.ledger.append({'do': action.get('do', ''), 'with': action.get('with', ''), 'expect': action.get('expect', ''), 'actual': shown})
        journal(self.run, 'comparison', n=self.number, do=action.get('do', ''), expect=action.get('expect', ''),
                actual=evidence(self.run, f'actual-{self.number}-{len(self.ledger)}', actual))
    def compose(self, cue):
        blocks = [f'Decision: {json.dumps({"do": item["do"], "with": item["with"]}, ensure_ascii=False)}\nExpected: {item["expect"]}\nActual: {item["actual"]}' for item in self.ledger]
        while len(blocks) > 1 and sum(map(len, blocks)) > self.settings['context_chars']:
            blocks.pop(0)
            self.ledger.pop(0)
        parts = [self.brief.rstrip(), self.template, 'Goal:\n' + (self.goal or '(none)')]
        if cue and cue != self.goal: parts.append('Cue:\n' + cue)
        parts.append('Record:\n' + ('\n\n'.join(blocks) if blocks else '(none)'))
        return '\n\n'.join(parts)
    def apply(self, action, actual):
        png = '\n' not in actual and actual.lower().endswith('.png')
        if action['do'] == 'look':
            path, _, question = action['with'].partition('\n')
            try: boxes = isinstance(json.loads(actual), list)
            except json.JSONDecodeError: boxes = False
            if question.strip() and not boxes and self.effect and not self.stale and self.unchecked and path == self.unchecked:
                self.unchecked, self.need_check = '', False
            return
        if actual.startswith('exit ') or (png and action['do'] != 'screenshot'):
            self.effect, self.need_check = True, True
            self.stale, self.unchecked = not png, actual.strip() if png else ''
            return
        if action['do'] == 'screenshot' and png:
            self.unchecked, self.stale = actual.strip(), False
    def unmet(self):
        if not self.effect: return 'Fault: no effect has been accepted yet. Choose the action that moves the goal, and put its predicted outcome in expect.'
        if self.stale or not self.unchecked: return 'Fault: the effect is not on a checked picture. screenshot, then look at that new path and ask whether the change you expected is visible.'
        return f'Fault: the picture is unchecked. look at {self.unchecked} and ask whether the change you expected is visible. done repeats that answer and nothing else.'
    async def wake(self, generation, action, actual):
        if generation != self.generation:
            self.busy = False
            return
        self.record(action, actual)
        self.busy = False
        await self.follow(actual if actual.startswith('Fault:') else 'Record updated.')
    async def work(self, source, frame):
        self.number, self.busy = self.number + 1, True
        generation = self.generation
        brief = (self.cfg.root / 'prompt.txt').read_text('utf-8')
        if brief != getattr(self, 'brief', None):
            self.brief = brief
            journal(self.run, 'instructions', turn=self.number, policy=hashlib.sha256(brief.encode()).hexdigest()[:16],
                    text=evidence(self.run, f'instructions-{self.number}', brief))
        if source in (self.cfg['address']['telegram'], self.cfg['address']['ears']):
            self.goal, self.ledger = frame.text, []
            self.unchecked, self.effect, self.need_check, self.stale = '', False, True, False
        prompt = self.compose(frame.text)
        journal(self.run, 'turn_input', n=self.number, src=source, generation=generation, record=len(self.ledger), text=evidence(self.run, f'input-{self.number}', prompt))
        try: raw = await asyncio.wait_for(self.engine.reply(prompt, self.schema), self.settings['turn_seconds'])
        except (RuntimeError, TimeoutError) as error:
            journal(self.run, 'turn_fault', n=self.number, error=str(error))
            await self.wake(generation, {'do': '', 'with': '', 'expect': ''}, f'Fault: {error}. Decide one valid next action. Do not choose nothing unless the latest check showed the goal effect.')
            return
        journal(self.run, 'turn_output', n=self.number, reply=evidence(self.run, f'output-{self.number}', raw))
        if generation != self.generation:
            self.busy = False
            journal(self.run, 'turn_superseded', n=self.number)
            return
        action = None
        try:
            action = self.action_json(raw)
            self.validator.validate(action)
            if action['do'] in ('say', 'done') and not action['with']: raise ValueError('Empty owner message')
            if action['do'] == 'say':
                await self.bus.transfer(Frame(self.cfg['address']['telegram'], '10', action['with']))
                await self.wake(generation, action, 'accepted; the owner may hear it; the effect is not verified')
                return
            if action['do'] == 'done':
                if not self.effect or self.need_check or self.stale or self.unchecked:
                    await self.wake(generation, action, self.unmet())
                    return
                await self.bus.transfer(Frame(self.cfg['address']['telegram'], '10', action['with']))
                self.record(action, 'report accepted for delivery; delivery is not confirmed')
                return
            if action['do'] == 'nothing':
                if frame.text == self.cfg['timer']['idle_text'] and not self.goal: return
                await self.wake(generation, action, 'Fault: nothing sends no report. Continue from the Record, or done with exactly the latest check.')
                return
            if same_refusal(self.refused, action):
                journal(self.run, 'repeated_refusal', n=self.number, do=action['do'])
                self.record(action, 'Fault: repeated UNKNOWN_DATA')
                await self.send('telegram', 'That request was refused, so I will not repeat it.', '10', once=True)
                return
            address, register, read = self.by_name[action['do']]
            result = await self.bus.transfer(Frame(address, register, action['with'], read))
            if generation != self.generation:
                journal(self.run, 'turn_superseded', n=self.number, after_transfer=True)
                return
            actual = result.decode() if read else 'accepted; the effect is not verified'
            self.apply(action, actual)
            await self.wake(generation, action, actual)
        except (ValueError, ValidationError, Refused, TimeoutError) as error:
            self.busy = False
            if generation != self.generation: return
            description = ('Action not allowed by the register schema' if isinstance(error, ValidationError) else
                           'Model returned text instead of a JSON action' if isinstance(error, json.JSONDecodeError) else str(error))
            journal(self.run, 'turn_fault', n=self.number, error=description)
            if isinstance(error, Refused) and description == 'NO_RECEIVER':
                target = self.by_name.get((action or {}).get('do'), (self.cfg['address']['telegram'],))[0]
                role = self.cfg['roles'].get(target, target)
                journal(self.run, 'capability_unavailable', n=self.number, role=role)
                if target != self.cfg['address']['telegram']:
                    await self.send('telegram', f'{role} is unavailable. I cannot complete that operation until it recovers.', '10', once=True)
                return
            if isinstance(error, Refused) and description == 'UNKNOWN_DATA':
                self.refused = ((action or {}).get('do'), (action or {}).get('with'))
                await self.wake(generation, action or {'do': '', 'with': '', 'expect': ''}, f'Fault: {(action or {}).get("do")} was refused. Do not resend those bytes. Replace the placeholders in that action published shape with a path or coordinates a result already returned.')
                return
            if isinstance(error, json.JSONDecodeError):
                await self.wake(generation, {'do': '', 'with': '', 'expect': ''}, 'Fault: Model returned text instead of a JSON action. Return one schema object only.')
                return
            if isinstance(error, TimeoutError):
                await self.wake(generation, action or {'do': '', 'with': '', 'expect': ''}, f'Fault: {description}. Observe before any repetition; do not repeat the effect.')
                return
            await self.wake(generation, action or {'do': '', 'with': '', 'expect': ''}, f'Fault: {description}. Decide one valid next action. Do not choose nothing unless the latest check showed the goal effect.')
        finally: self.busy = False
    async def follow(self, text):
        try: await self.bus.transfer(Frame(self.address, '01', text))
        except Refused as error:
            if str(error) == 'FULL' and not self.stopped:
                self.stopped = True
                await self.send('telegram', 'I stopped. I need your help to continue.', '10', once=True)
            else: print(f'Continuation refused: {error}', flush=True)
    @staticmethod
    def object(pairs):
        result = dict(pairs)
        if len(result) != len(pairs): raise ValueError('Duplicate action property')
        return result
    @classmethod
    def action_json(cls, raw):
        try: return json.loads(raw, object_pairs_hook=cls.object)
        except json.JSONDecodeError:
            start, end = raw.rfind('{'), raw.rfind('}')
            if start < 0 or end < start: raise
            return json.loads(raw[start:end + 1], object_pairs_hook=cls.object)
if __name__ == '__main__': Mind('mind').launch()
