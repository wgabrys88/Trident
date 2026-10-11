import json, shutil, signal, subprocess, sys, time
from contextlib import ExitStack
from datetime import datetime
sys.dont_write_bytecode = True
from bus import Config, journal

class Process:
    def __init__(self, cfg, role, run, engine, log):
        self.cfg, self.role, self.run, self.home, self.log = cfg, role, run, cfg.root / 'wire' / cfg['address'][role], log
        self.command = [sys.executable, '-B', str(cfg.root / f'{role}.py'), str(run), engine]
        self.failures, self.ready, self.retry = 0, None, 0
        self.spawn()
    def spawn(self):
        self.process = subprocess.Popen(self.command, cwd=self.cfg.root, stdout=self.log, stderr=self.log, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        journal(self.run, 'device_start', role=self.role, pid=self.process.pid)
        self.ready, self.retry = None, 0
    def signal(self):
        if self.process.poll() is None: self.process.send_signal(signal.CTRL_BREAK_EVENT)
    def finish(self, deadline):
        while self.process.poll() is None and time.monotonic() < deadline: time.sleep(self.cfg['bus']['poll'])
        if self.process.poll() is None: self.process.kill(); self.process.wait()
    def inspect(self):
        now = time.monotonic()
        if self.process.poll() is None:
            if (self.home / 'alive').exists() and self.ready is None: self.ready = now
            if any(time.time() - float(marker.name.rsplit('-', 1)[1]) >= self.cfg['holds'][self.role] for marker in self.home.glob('hold-*')):
                self.signal(); self.finish(now + self.cfg['supply']['stop_seconds'])
            return
        if self.retry == 0:
            self.failures = 1 if self.ready is not None and now - self.ready >= self.cfg['supply']['stable_seconds'] else self.failures + 1
            for marker in [self.home / 'alive', self.home / 'busoff', *self.home.glob('hold-*')]: marker.unlink(missing_ok=True)
            state = json.loads((self.home / 'state.json').read_text()) | {'tx': 0, 'rx': 0}
            self.cfg.publish(self.home / 'state.json', json.dumps(state))
            journal(self.run, 'device_exit', role=self.role, code=self.process.returncode, failures=self.failures)
            self.retry = now + self.cfg['supply']['backoff'] * self.failures
            if self.failures >= self.cfg['supply']['restart_cap']:
                self.retry = float('inf')
                journal(self.run, 'device_unavailable', role=self.role, failures=self.failures)
        if now >= self.retry: self.spawn()

def halt(processes):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    for process in processes: process.signal()
    deadline = time.monotonic() + processes[0].cfg['supply']['stop_seconds'] if processes else time.monotonic()
    for process in processes: process.finish(deadline)

def start(engine):
    cfg = Config()
    cfg['mind'][engine]
    with cfg.lock(cfg.root / '.supply.lock'), ExitStack() as cleanup:
        wire = cfg.root / 'wire'
        if wire.exists(): shutil.rmtree(wire)
        cleanup.callback(shutil.rmtree, wire, ignore_errors=True)
        run = cfg.root / f'run_{datetime.now().strftime("%Y%m%dT%H%M%S%f")}'
        run.mkdir()
        journal(run, 'run_start', engine=engine, transport=cfg['mind'][engine]['transport'])
        cleanup.callback(journal, run, 'run_stop')
        processes = []
        cleanup.callback(halt, processes)
        for role, address in cfg['address'].items():
            (home := wire / address).mkdir(parents=True)
            cfg.publish(home / 'state.json', json.dumps(dict(sequence=0, tx=0, rx=0)))
            processes.append(Process(cfg, role, run, engine, cleanup.enter_context((run / f'{role}.log').open('ab'))))
        print(run, flush=True)
        while True:
            for process in processes: process.inspect()
            time.sleep(cfg['bus']['poll'])

signal.signal(signal.SIGBREAK, signal.default_int_handler)
try: start(sys.argv[1])
except KeyboardInterrupt: pass
