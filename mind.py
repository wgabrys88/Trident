import asyncio, json, os, sys, time
from pathlib import Path
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
        # #region agent log
        try:
            from pathlib import Path
            payload = {"sessionId": "f678ae", "hypothesisId": "E", "runId": "post-fix", "location": "mind.py:Cli.reply", "message": "allowed tools", "data": {"allowed_tools": "", "mode_flag": False, "home": home, "proposals_in_prompt": "proposals" in prompt}, "timestamp": int(time.time() * 1000)}
            with Path(__file__).with_name("debug-f678ae.log").open("a", encoding="utf-8") as stream: stream.write(json.dumps(payload) + "\n")
        except Exception: pass
        # #endregion
        # #region agent log
        try:
            payload = {"sessionId": "f678ae", "hypothesisId": "F", "runId": "post-fix", "location": "mind.py:Cli.reply", "message": "workspace", "data": {"workspace": workspace, "repo_parent": any((parent / '.git').exists() for parent in (Path(workspace), *Path(workspace).parents))}, "timestamp": int(time.time() * 1000)}
            with Path(__file__).with_name("debug-f678ae.log").open("a", encoding="utf-8") as stream: stream.write(json.dumps(payload) + "\n")
        except Exception: pass
        # #endregion
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
                    call = event.get('tool_call') if isinstance(event.get('tool_call'), dict) else {}
                    failed = any(isinstance(item, dict) and isinstance(item.get('result'), dict) and 'error' in item['result'] for item in call.values())
                    # #region agent log
                    try:
                        payload = {"sessionId": "f678ae", "hypothesisId": "H3", "runId": "post-fix", "location": "mind.py:Cli.reply", "message": "host tool", "data": {"failed": failed, "subtype": event.get("subtype"), "texts": len(texts)}, "timestamp": int(time.time() * 1000)}
                        with Path(__file__).with_name("debug-f678ae.log").open("a", encoding="utf-8") as stream: stream.write(json.dumps(payload) + "\n")
                    except Exception: pass
                    # #endregion
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
    signature = (action.get('do'), action.get('with'))
    blocked = previous is not None and previous == signature
    # #region agent log
    try:
        import json, time
        from pathlib import Path
        payload = {"sessionId": "f678ae", "hypothesisId": "C", "runId": "post-fix", "location": "mind.py:same_refusal", "message": "repeated refusal check", "data": {"blocked": blocked, "do": action.get("do"), "with_chars": len(action.get("with") or "")}, "timestamp": int(time.time() * 1000)}
        with Path(__file__).with_name("debug-f678ae.log").open("a", encoding="utf-8") as stream: stream.write(json.dumps(payload) + "\n")
    except Exception: pass
    # #endregion
    return blocked

class Mind(Device):
    async def start(self):
        self.settings = self.cfg['mind'][sys.argv[2]]
        self.server, self.number, self.used, self.busy, self.generation, self.stopped, self.refused = None, 0, 0, False, 0, False, None
        self.history, self.queue = [], asyncio.PriorityQueue()
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
                lines.append(json.dumps({'do': action, 'with': example}) + ' -> ' + returns)
        for action, spec in self.cfg['actions'].items():
            example, _, returns = spec.partition('|')
            lines.append(json.dumps({'do': action, 'with': example.strip()}) + ' -> ' + returns.strip())
        self.template = '\n'.join(lines)
        names = [*self.by_name, *self.cfg['actions']]
        self.schema = dict(type='object', additionalProperties=False, required=['do', 'with'], properties=dict(do=dict(enum=names), **{'with': dict(type='string')}))
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
    async def work(self, source, frame):
        self.number, self.busy = self.number + 1, True
        generation = self.generation
        brief = (self.cfg.root / 'prompt.txt').read_text('utf-8')
        if brief != getattr(self, 'brief', None):
            self.brief = brief
            journal(self.run, 'instructions', turn=self.number,
                    text=evidence(self.run, f'instructions-{self.number}', brief))
        owners = (self.cfg['address']['telegram'], self.cfg['address']['ears'], self.cfg['address']['timer'])
        current = f'Owner: {frame.text}' if source in owners else frame.text
        prompt = '\n'.join((brief.rstrip(), self.template, *self.history, current))
        journal(self.run, 'turn_input', n=self.number, src=source, generation=generation, history=len(self.history), text=evidence(self.run, f'input-{self.number}', frame.text))
        try: raw = await asyncio.wait_for(self.engine.reply(prompt, self.schema), self.settings['turn_seconds'])
        except (RuntimeError, TimeoutError) as error:
            journal(self.run, 'turn_fault', n=self.number, error=str(error))
            self.busy = False
            if generation != self.generation: return
            self.history.append(current + f'\nFault: {error}')
            await self.follow(f'Fault: {error}. Decide one valid next action. Do not choose nothing unless a look already showed the requested effect.')
            return
        journal(self.run, 'turn_output', n=self.number, reply=evidence(self.run, f'output-{self.number}', raw))
        if generation != self.generation:
            self.busy = False
            journal(self.run, 'turn_superseded', n=self.number)
            return
        try:
            action = self.action_json(raw)
            self.validator.validate(action)
            if action['do'] in ('say', 'done') and not action['with']: raise ValueError('Empty owner message')
            self.history.append(current + '\nDid: ' + json.dumps({'do': action['do'], 'with': action['with']}))
            while sum(map(len, self.history)) > self.settings['context_chars']: self.history.pop(0)
            if action['do'] == 'say':
                await self.bus.transfer(Frame(self.cfg['address']['telegram'], '10', action['with']))
                self.busy = False
                await self.follow('say returned. Continue the goal unless the requested effect has been checked.')
                return
            if action['do'] == 'done':
                await self.bus.transfer(Frame(self.cfg['address']['telegram'], '10', action['with']))
                return
            if action['do'] == 'nothing':
                if frame.text != self.cfg['timer']['idle_text']:
                    self.busy = False
                    await self.follow('Fault: nothing sends no report. Continue acting, or done with exactly what the latest look said.')
                return
            if same_refusal(self.refused, action):
                journal(self.run, 'repeated_refusal', n=self.number, do=action['do'])
                self.history.append(current + '\nFault: repeated UNKNOWN_DATA')
                await self.send('telegram', 'That request was refused, so I will not repeat it.', '10', once=True)
                return
            address, register, read = self.by_name[action['do']]
            result = await self.bus.transfer(Frame(address, register, action['with'], read))
            if generation != self.generation:
                journal(self.run, 'turn_superseded', n=self.number, after_transfer=True)
                return
            self.busy = False
            if read: await self.follow(f'{action["do"]} returned {result.decode()}')
            else: await self.follow(f'{action["do"]} accepted; effect not verified. Observe before repeating.')
        except (ValueError, ValidationError, Refused, TimeoutError) as error:
            self.busy = False
            description = ('Action not allowed by the register schema' if isinstance(error, ValidationError) else
                           'Model returned text instead of a JSON action' if isinstance(error, json.JSONDecodeError) else str(error))
            journal(self.run, 'turn_fault', n=self.number, error=description)
            self.history.append(current + f'\nFault: {description}')
            # #region agent log
            try:
                import time
                from pathlib import Path
                payload = {"sessionId": "f678ae", "hypothesisId": "C" if description == "UNKNOWN_DATA" else "D", "runId": "post-fix", "location": "mind.py:work", "message": "fault continuation", "data": {"description": description, "will_follow": not (isinstance(error, Refused) and description == "NO_RECEIVER"), "schema_enforced_on_cli": False}, "timestamp": int(time.time() * 1000)}
                with Path(__file__).with_name("debug-f678ae.log").open("a", encoding="utf-8") as stream: stream.write(json.dumps(payload) + "\n")
            except Exception: pass
            # #endregion
            if isinstance(error, Refused) and description == 'NO_RECEIVER':
                target = self.by_name.get(action.get('do'), (self.cfg['address']['telegram'],))[0]
                role = self.cfg['roles'].get(target, target)
                journal(self.run, 'capability_unavailable', n=self.number, role=role)
                if target != self.cfg['address']['telegram']:
                    await self.send('telegram', f'{role} is unavailable. I cannot complete that operation until it recovers.', '10', once=True)
                return
            if isinstance(error, Refused) and description == 'UNKNOWN_DATA':
                self.refused = (action.get('do'), action.get('with'))
                await self.follow(f'Fault: {action.get("do")} was refused. Do not resend those bytes. Replace the placeholders in that action published shape with a path or coordinates a result already returned.')
                return
            if isinstance(error, json.JSONDecodeError):
                await self.follow('Fault: Model returned text instead of a JSON action. Return one schema object only.')
                return
            if isinstance(error, TimeoutError):
                await self.follow(f'Fault: {description}. Observe before any repetition; do not repeat the effect.')
                return
            await self.follow(f'Fault: {description}. Decide one valid next action. Do not choose nothing unless a look already showed the requested effect.')
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
            # #region agent log
            try:
                import time
                from pathlib import Path
                payload = {"sessionId": "f678ae", "hypothesisId": "J", "runId": "post-fix", "location": "mind.py:action_json", "message": "trailing object", "data": {"prefix": start, "chars": len(raw)}, "timestamp": int(time.time() * 1000)}
                with Path(__file__).with_name("debug-f678ae.log").open("a", encoding="utf-8") as stream: stream.write(json.dumps(payload) + "\n")
            except Exception: pass
            # #endregion
            return json.loads(raw[start:end + 1], object_pairs_hook=cls.object)
if __name__ == '__main__': Mind('mind').launch()
