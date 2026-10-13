import asyncio, ctypes, json, os, sys, threading
from pathlib import Path
from urllib.parse import urlparse

sys.dont_write_bytecode = True
if __name__=='__main__':
    python = Path(__file__).resolve().parent/'runtime'/'python'/'Scripts'/'python.exe'
    if not python.is_file():
        raise SystemExit('Run python install.py first')
    if Path(sys.executable).resolve()!=python.resolve():
        os.execv(str(python), [str(python), '-B', str(Path(__file__).resolve()), *sys.argv[1:]])

from core import CFG, ROOT, ROLES, Bus, Job, ProcessGuard, Session, write
from mind import Mind

class Body:
    def __init__(self, session):
        self.session, self.bus, self.jobs = session, Bus(session), {}

    def spawn(self, role, command=None):
        self.jobs[role] = Job(command or [sys.executable, '-B', str(ROOT/'devices.py'), role], cwd=ROOT)

    def stop(self, role):
        job = self.jobs[role]
        try:
            job.close(); job.process.wait(timeout=3)
        finally:
            self.session.file(role+'.receiver').unlink(missing_ok=True)
            if role=='manipulator':
                ctypes.windll.user32.mouse_event(4, 0, 0, 0, 0)
                for key in (0x10, 0x11, 0x12, 0x5B):
                    ctypes.windll.user32.keybd_event(key, 0, 2, 0)

    async def call(self, role, operation, arguments, seconds):
        job = self.jobs.get(role)
        if not job or job.process.poll() is not None:
            return dict(outcome='not_attempted', error=f'{role} unavailable')
        result = await self.bus.request(role, operation, arguments, seconds)
        if result['outcome']=='unknown':
            self.stop(role)
        return result

    def close(self):
        for role in self.jobs:
            try:
                self.stop(role)
            except Exception as error:
                print(f'{role} shutdown: {type(error).__name__}: {error}', file=sys.stderr, flush=True)

    def start(self):
        models = ROOT/'runtime'/'models'
        self.spawn('vision-server', [str(ROOT/'runtime'/'llama'/'llama-server.exe'), '-m', str(models/CFG['vision_model']),
            '--mmproj', str(models/CFG['vision_projector']), '--device', 'CUDA0', '--mmproj-device', 'CUDA0',
            '-ngl', '999', '--fit', 'off', '--ctx-size', str(CFG['vision_context']), '--parallel', '1',
            '--reasoning', 'off', '--host', '127.0.0.1', '--port', str(urlparse(CFG['vision_url']).port),
            '--alias', CFG['vision_alias']])
        for role in ROLES:
            self.spawn(role)

class Organism:
    def __init__(self, session):
        self.session, self.body, self.mind = session, Body(session), Mind(session)

    def console(self):
        for text in sys.stdin:
            if text.strip():
                self.body.bus.publish_owner(text.strip(), 'local-owner')

    async def live(self):
        session, body = self.session, self.body
        announced = set()
        try:
            body.start()
            threading.Thread(target=self.console, daemon=True).start()
            print(f'Trident alive. Run: {session.directory}\nOwner input: stdin, Telegram text, voice note or call.', flush=True)
            if len(sys.argv)>1:
                body.bus.publish_owner(' '.join(sys.argv[1:]), 'local-owner')
            while not session.file('stop').exists():
                state = session.load(); updates = []
                for role, job in body.jobs.items():
                    if job.process.poll() is not None and role not in announced:
                        announced.add(role); body.stop(role)
                        updates.append(dict(device=role, stopped=job.process.returncode))
                for role in state['memory'].pop('restart_devices', []):
                    if role in ROLES:
                        body.stop(role); body.spawn(role); announced.discard(role)
                for path in session.inbox():
                    message = json.loads(path.read_text('utf-8')); path.unlink(); updates.append(message)
                    if message['kind']=='owner':
                        state['owner'] = message; state['memory']['goal'] = message['text']
                        write(session.file('owner.json'), message)
                screen = await body.call('manipulator', 'capture', {}, 10)
                if not screen.get('image'):
                    updates.append(dict(capture_failure=screen, stale_pixels=True))
                    screen = state['screen']
                    if not screen.get('image'):
                        session.save(state)
                        print('Capture unavailable; waiting for a working device.', file=sys.stderr, flush=True)
                        await asyncio.sleep(15); continue
                state['screen'] = screen; session.save(state)
                orientation = session.file('orientation.json'); write(orientation, dict(status='pending'))
                async def describe():
                    result = await body.call('retina', 'inspect', dict(image=screen['image'],
                        question='Describe visible applications, controls, text and precise spatial relationships without assuming an intended task.'), CFG['look_seconds'])
                    write(orientation, result)
                vision = asyncio.create_task(describe())
                context = dict(goal=state['memory']['goal'], narrative=state['memory']['narrative'],
                    owner=state['owner'], updates=updates, receivers=body.bus.receivers(), previous=state['previous'],
                    previous_proposal=state.get('proposal'), unknown=state['unknown'], screen=screen,
                    orientation=str(orientation), tools=str(session.file('tools.py')), output_directory=str(session.directory))
                try:
                    answer = await asyncio.to_thread(self.mind.infer, context)
                    state = session.load(); state['proposal'] = dict(text=answer); session.save(state)
                    print(answer, flush=True)
                except Exception as error:
                    failure = f'{type(error).__name__}: {error}'
                    print(failure, file=sys.stderr, flush=True)
                    state = session.load(); state['previous'].append(dict(error=failure, outcome='processing_failed'))
                    session.save(state)
                finally:
                    await vision
        finally:
            body.close()

async def main():
    guard = ProcessGuard()
    try:
        session = Session.create()
        try:
            session.prepare()
            await Organism(session).live()
        finally:
            state = session.load(); state.update(active=False, process_state='stopped'); session.save(state)
    finally:
        guard.close()

if __name__=='__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
