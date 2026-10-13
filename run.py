import asyncio, ctypes, json, os, signal, sys, threading, time
from urllib.parse import urlparse

sys.dont_write_bytecode = True
from core import CFG, ROOT, ROLES, Bus, Job, ProcessGuard, Session, write
from mind import Mind

class Organism:
    def __init__(self, session):
        self.session, self.bus, self.mind, self.jobs = session, Bus(session, 'organism'), Mind(session), {}

    def spawn(self, role):
        models = ROOT/'runtime'/'models'
        command = [sys.executable, '-B', str(ROOT/'devices.py'), role] if role!='vision-server' else [
            str(ROOT/'runtime'/'llama'/'llama-server.exe'), '-m', str(models/CFG['vision_model']),
            '--mmproj', str(models/CFG['vision_projector']), '--device', 'CUDA0', '--mmproj-device', 'CUDA0',
            '-ngl', '999', '--fit', 'off', '--ctx-size', str(CFG['vision_context']), '--parallel', '1',
            '--reasoning', 'off', '--host', '127.0.0.1', '--port', str(urlparse(CFG['vision_url']).port), '--alias', CFG['vision_alias']]
        self.jobs[role] = Job(command, cwd=ROOT)

    def stop(self, role):
        job = self.jobs.pop(role, None)
        if job:
            job.close()
        for path in self.session.directory.glob('*_'+role+'.receiver'):
            path.unlink(missing_ok=True)

    def console(self):
        for text in sys.stdin:
            if text.strip():
                self.bus.publish_owner(text.strip(), 'local-owner')

    async def live(self):
        session = self.session
        vision = None
        try:
            for role in ('vision-server', *ROLES):
                self.spawn(role)
            deadline = time.monotonic()+30
            while not {'manipulator', 'retina'}.issubset(self.bus.receivers()) and time.monotonic()<deadline:
                await asyncio.sleep(.05)
            threading.Thread(target=self.console, daemon=True).start()
            print(f'Trident alive. Run: {session.directory}\nOwner input: stdin, Telegram text, voice note or call.', flush=True)
            if len(sys.argv)>1:
                self.bus.publish_owner(' '.join(sys.argv[1:]), 'local-owner')
            while not any(session.directory.glob('*_stop')):
                state = session.load(); updates = []
                for role, job in list(self.jobs.items()):
                    if job.process.poll() is not None:
                        self.stop(role)
                        updates.append(dict(device=role, stopped=job.process.returncode))
                for role in state['memory'].pop('restart_devices', []):
                    if role in ROLES:
                        self.stop(role); self.spawn(role)
                for path in session.inbox():
                    message = json.loads(path.read_text('utf-8')); path.unlink(); updates.append(message)
                    if message['kind']=='owner':
                        state['owner'] = message; state['memory']['goal'] = message['text']
                        write(session.file('owner.json'), message)
                screen = await self.bus.request('manipulator', 'capture', {}, 10)
                if not screen.get('image'):
                    print('Capture unavailable: '+str(screen), file=sys.stderr, flush=True)
                    session.save(state); await asyncio.sleep(15); continue
                state['screen'] = screen; session.save(state)
                orientation = session.file('orientation.json'); write(orientation, dict(status='pending'))
                async def describe():
                    result = await self.bus.request('retina', 'inspect', dict(image=screen['image'],
                        question='Describe visible applications, controls, text and spatial relationships. Give normalized-1000 bounding boxes for visible windows, work areas and relevant controls. State occlusion and uncertainty; do not assume an intended task.'), CFG['look_seconds'])
                    write(orientation, result)
                vision = asyncio.create_task(describe())
                context = dict(goal=state['memory']['goal'], narrative=state['memory']['narrative'],
                    owner=state['owner'], updates=updates, receivers=self.bus.receivers(), previous=state['previous'],
                    previous_proposal=state.get('proposal'), unknown=state['unknown'], screen={key:screen[key] for key in ('desktop', 'window', 'at')},
                    orientation=str(orientation), tools=str(session.file('tools.py')), output_directory=str(session.directory))
                try:
                    answer = await self.mind.infer(context)
                    state = session.load(); state['proposal'] = dict(text=answer); session.save(state)
                    print(answer, flush=True)
                except Exception as error:
                    failure = f'{type(error).__name__}: {error}'
                    print(failure, file=sys.stderr, flush=True)
                    state = session.load(); state['previous'].append(dict(error=failure, outcome='processing_failed'))
                    session.save(state)
                finally:
                    if asyncio.current_task().cancelling(): vision.cancel()
                    await asyncio.gather(vision, return_exceptions=True)
        finally:
            session.file('stop').touch()
            if vision: vision.cancel()
            for role in list(self.jobs):
                self.stop(role)

def launch(python):
    guard, job, interrupted = ProcessGuard(), None, None
    session = Session.create()
    def interrupt(signum, frame):
        nonlocal interrupted
        interrupted = interrupted or time.monotonic()
    previous = signal.signal(signal.SIGINT, interrupt)
    try:
        job = Job([str(python), '-u', '-B', str(ROOT/'run.py'), *sys.argv[1:]], cwd=ROOT,
                  creationflags=0, env=os.environ|{'TRIDENT_BOOT_RUN':str(session.directory)})
        while job.process.poll() is None:
            if interrupted and time.monotonic()-interrupted>=5:
                job.close()
            time.sleep(.05)
        return job.process.returncode
    finally:
        if job:
            job.close()
        ctypes.windll.user32.mouse_event(4, 0, 0, 0, 0)
        for key in (0x10, 0x11, 0x12, 0x5B):
            ctypes.windll.user32.keybd_event(key, 0, 2, 0)
        for path in session.directory.glob('*.receiver'):
            path.unlink(missing_ok=True)
        state = session.load(); state.update(active=False, process_state='stopped'); session.save(state)
        signal.signal(signal.SIGINT, previous); guard.close()

if __name__=='__main__':
    if os.environ.pop('TRIDENT_BOOT_RUN', None):
        session = Session.attach(); session.prepare()
        try:
            asyncio.run(Organism(session).live())
        except KeyboardInterrupt:
            pass
    else:
        python = ROOT/'runtime'/'python'/'Scripts'/'python.exe'
        if not python.is_file(): raise SystemExit('Run python install.py first')
        raise SystemExit(launch(python))
