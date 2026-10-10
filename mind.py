import asyncio, json, os, sys, time
from pathlib import Path
from jsonschema import Draft202012Validator, ValidationError
from bus import Device, Frame, Refused, Server, journal, evidence

class Cli:
    def __init__(self, device):
        self.device, self.settings = device, device.settings
        self.folder = max((path for path in device.cfg.path(self.settings['versions']).iterdir() if path.is_dir()), key=lambda path: path.name)
    async def reply(self, prompt, schema):
        names = [item for item in self.settings['excluded'].split(',') if item]
        workspace = os.path.join(os.environ['TEMP'], 'trident-mind')
        os.makedirs(workspace, exist_ok=True)
        command = [str(self.folder / 'node.exe'), '-e', "process.argv.push(require('fs').readFileSync(0,'utf8'));require(process.argv[1]);", str(self.folder / 'index.js'), '-p', '--mode', self.settings['mode'], '--model', self.settings['model'], '--output-format', 'stream-json', '--workspace', workspace, '--trust']
        command += [part for name in names for part in ('--exclude-tools', name)]
        # #region agent log
        try:
            from pathlib import Path
            payload = {"sessionId": "f678ae", "hypothesisId": "E", "runId": "post-fix", "location": "mind.py:Cli.reply", "message": "exclude flags", "data": {"flags": len(names), "read_separate": "read_tool_call" in names, "proposals_in_prompt": "proposals" in prompt}, "timestamp": int(time.time() * 1000)}
            with Path(__file__).with_name("debug-f678ae.log").open("a", encoding="utf-8") as stream: stream.write(json.dumps(payload) + "\n")
        except Exception: pass
        # #endregion
        # #region agent log
        try:
            payload = {"sessionId": "f678ae", "hypothesisId": "F", "runId": "post-fix", "location": "mind.py:Cli.reply", "message": "workspace", "data": {"workspace": workspace, "repo_parent": any((parent / '.git').exists() for parent in (Path(workspace), *Path(workspace).parents))}, "timestamp": int(time.time() * 1000)}
            with Path(__file__).with_name("debug-f678ae.log").open("a", encoding="utf-8") as stream: stream.write(json.dumps(payload) + "\n")
        except Exception: pass
        # #endregion
        process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=self.device.stderr, cwd=workspace, limit=self.settings['event_bytes'])
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
    signature = (action.get('action'), action.get('address'), action.get('register'), action.get('text'))
    blocked = previous is not None and previous == signature
    # #region agent log
    try:
        import json, time
        from pathlib import Path
        payload = {"sessionId": "f678ae", "hypothesisId": "C", "runId": "post-fix", "location": "mind.py:same_refusal", "message": "repeated refusal check", "data": {"blocked": blocked, "address": action.get("address"), "register": action.get("register"), "text_chars": len(action.get("text") or "")}, "timestamp": int(time.time() * 1000)}
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
        choices = [dict(type='object', additionalProperties=False, required=['action'], properties=dict(action=dict(const='nothing'))), dict(type='object', additionalProperties=False, required=['action', 'text'], properties=dict(action=dict(const='say'), text=dict(type='string', minLength=1)))]
        for name, registers in self.cfg['registers'].items():
            for direction in ('read', 'write'):
                allowed = [key for key, value in registers.items() if direction in value.partition(':')[0].split('/')]
                if allowed: choices.append(dict(type='object', additionalProperties=False, required=['action', 'address', 'register', 'text'], properties=dict(action=dict(const=direction), address=dict(const=self.cfg['address'][name]), register=dict(enum=allowed), text=dict(type='string'))))
        self.schema, self.validator = {'oneOf': choices}, Draft202012Validator({'oneOf': choices})
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
        context = dict(registers={self.cfg['address'][name]: dict(role=name, registers=registers) for name, registers in self.cfg['registers'].items()}, schema=self.schema)
        brief = (self.cfg.root / 'prompt.txt').read_text('utf-8')
        if brief != getattr(self, 'brief', None):
            self.brief = brief
            journal(self.run, 'instructions', turn=self.number,
                    text=evidence(self.run, f'instructions-{self.number}', brief))
        prompt = '\n'.join((json.dumps(context), *self.history, f'Controller {source}: {frame.text}', brief))
        journal(self.run, 'turn_input', n=self.number, src=source, generation=generation, history=len(self.history), text=evidence(self.run, f'input-{self.number}', frame.text))
        try: raw = await asyncio.wait_for(self.engine.reply(prompt, self.schema), self.settings['turn_seconds'])
        except (RuntimeError, TimeoutError) as error:
            journal(self.run, 'turn_fault', n=self.number, error=str(error))
            self.busy = False
            if generation != self.generation: return
            self.history.append(f'Controller {source}: {frame.text}\nFault: {error}')
            await self.follow(f'Fault: {error}. Decide one valid next action; do not repeat unknown effects.')
            return
        journal(self.run, 'turn_output', n=self.number, reply=evidence(self.run, f'output-{self.number}', raw))
        if generation != self.generation:
            self.busy = False
            journal(self.run, 'turn_superseded', n=self.number)
            return
        try:
            action = json.loads(raw, object_pairs_hook=self.object)
            self.validator.validate(action)
            self.history.append(f'Controller {source}: {frame.text}\nSelected: {json.dumps(action)}')
            while sum(map(len, self.history)) > self.settings['context_chars']: self.history.pop(0)
            if action['action'] == 'say': await self.bus.transfer(Frame(self.cfg['address']['telegram'], '10', action['text']))
            if action['action'] in ('read', 'write'):
                if same_refusal(self.refused, action):
                    journal(self.run, 'repeated_refusal', n=self.number, address=action['address'], register=action['register'])
                    self.history.append(f'Controller {source}: {frame.text}\nFault: repeated UNKNOWN_DATA')
                    await self.send('telegram', 'That request was refused, so I will not repeat it.', '10', once=True)
                    return
                result = await self.bus.transfer(Frame(action['address'], action['register'], action['text'], action['action'] == 'read'))
                if generation != self.generation:
                    journal(self.run, 'turn_superseded', n=self.number, after_transfer=True)
                    return
                self.busy = False
                if action['action'] == 'read': await self.follow(f'Read {action["address"]}/{action["register"]}: {result.decode()}')
                else: await self.follow(f'Write {action["address"]}/{action["register"]}: ACK accepted the request; effect not verified. Observe the result before proceeding.')
        except (ValueError, ValidationError, Refused, TimeoutError) as error:
            self.busy = False
            description = ('Action not allowed by the register schema' if isinstance(error, ValidationError) else
                           'Model returned text instead of a JSON action' if isinstance(error, json.JSONDecodeError) else str(error))
            journal(self.run, 'turn_fault', n=self.number, error=description)
            self.history.append(f'Controller {source}: {frame.text}\nFault: {description}')
            # #region agent log
            try:
                import time
                from pathlib import Path
                payload = {"sessionId": "f678ae", "hypothesisId": "C" if description == "UNKNOWN_DATA" else "D", "runId": "post-fix", "location": "mind.py:work", "message": "fault continuation", "data": {"description": description, "will_follow": not (isinstance(error, Refused) and description == "NO_RECEIVER"), "schema_enforced_on_cli": False}, "timestamp": int(time.time() * 1000)}
                with Path(__file__).with_name("debug-f678ae.log").open("a", encoding="utf-8") as stream: stream.write(json.dumps(payload) + "\n")
            except Exception: pass
            # #endregion
            if isinstance(error, Refused) and description == 'NO_RECEIVER':
                target = action.get('address', self.cfg['address']['telegram'])
                role = self.cfg['roles'].get(target, target)
                journal(self.run, 'capability_unavailable', n=self.number, role=role)
                if target != self.cfg['address']['telegram']:
                    await self.send('telegram', f'{role} is unavailable. I cannot complete that operation until it recovers.', '10', once=True)
                return
            if isinstance(error, Refused) and description == 'UNKNOWN_DATA':
                self.refused = (action['action'], action.get('address'), action.get('register'), action.get('text'))
                meaning = self.cfg['registers'].get(self.cfg['roles'].get(action.get('address'), ''), {}).get(action.get('register'), '')
                await self.follow(f'Fault: UNKNOWN_DATA. Register meaning: {meaning}. Those bytes were refused. Choose a different argument; do not resend them.')
                return
            if isinstance(error, json.JSONDecodeError):
                await self.follow('Fault: Model returned text instead of a JSON action. Return one schema object only.')
                return
            if isinstance(error, TimeoutError):
                await self.follow(f'Fault: {description}. Observe before any repetition; do not repeat the effect.')
                return
            await self.follow(f'Fault: {description}. Decide one valid next action; do not repeat unknown effects.')
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
if __name__ == '__main__': Mind('mind').launch()
