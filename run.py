import json, shutil, signal, subprocess, sys, time
from contextlib import ExitStack
from datetime import datetime
from bus import Config

class Process:
    def __init__(self, cfg, role, run, engine, log):
        self.cfg, self.role, self.home, self.log = cfg, role, cfg.root / 'wire' / cfg['address'][role], log
        self.command = [sys.executable, str(cfg.root / f'{role}.py'), str(run), engine]
        self.failures, self.ready, self.retry = 0, None, 0
        self.spawn()
    def spawn(self):
        self.process = subprocess.Popen(self.command, cwd=self.cfg.root, stdout=self.log, stderr=self.log, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        self.ready, self.retry = None, 0
    def stop(self):
        if self.process.poll() is None:
            self.process.send_signal(signal.CTRL_BREAK_EVENT)
            try: self.process.wait(self.cfg['supply']['stop_seconds'])
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
    def inspect(self):
        now = time.monotonic()
        if self.process.poll() is None:
            if (self.home / 'alive').exists() and self.ready is None: self.ready = now
            for marker in self.home.glob('hold-*'):
                if time.time() - float(marker.name.rsplit('-', 1)[1]) >= self.cfg['holds'][self.role]: self.stop()
            return
        if self.retry == 0:
            self.failures = 1 if self.ready is not None and now - self.ready >= self.cfg['supply']['stable_seconds'] else self.failures + 1
            for marker in [self.home / 'alive', self.home / 'busoff', *self.home.glob('hold-*')]: marker.unlink(missing_ok=True)
            state = json.loads((self.home / 'state.json').read_text()) | {'tx': 0, 'rx': 0}
            self.cfg.publish(self.home / 'state.json', json.dumps(state))
            self.retry = now + self.cfg['supply']['backoff'] * self.failures
            if self.failures >= self.cfg['supply']['restart_cap']:
                self.retry = float('inf')
                print(f'{self.role} failed permanently; see its device log', flush=True)
        if now >= self.retry: self.spawn()

class Supply:
    def start(self, engine):
        cfg = Config()
        cfg['mind'][engine]
        wire = cfg.root / 'wire'
        if wire.exists(): shutil.rmtree(wire)
        run = cfg.root / f'run_{datetime.now().strftime("%Y%m%dT%H%M%S%f")}'
        run.mkdir(parents=True)
        (run / 'bus.log').touch()
        with ExitStack() as cleanup:
            processes = []
            for role, address in cfg['address'].items():
                (home := wire / address).mkdir(parents=True)
                cfg.publish(home / 'state.json', json.dumps(dict(sequence=0, tx=0, rx=0)))
                process = Process(cfg, role, run, engine, cleanup.enter_context((run / f'{role}.log').open('ab')))
                cleanup.callback(process.stop)
                processes.append(process)
            print(run, flush=True)
            while True:
                for process in processes: process.inspect()
                time.sleep(cfg['bus']['poll'])

signal.signal(signal.SIGBREAK, signal.default_int_handler)
Supply().start(sys.argv[1])
