import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import i2c

NAMES = ["memory", "telegram", "ears", "voice", "tools", "luna", "timer"]


class Supply:
    def __init__(self, root, cfg, run, commands=None):
        self.root = Path(root)
        self.cfg = cfg
        self.folder = Path(run)
        self.commands = commands or {}
        self.procs = {}
        self.when = {}
        self.restart = {}
        self.fails = {}
        self.backoff = {}
        self.wire = self.root / "wire"
        self.cap = int(cfg["bus"]["luna_start_cap"])
        self.stable = float(cfg["bus"]["stable_seconds"])
        self.step = float(cfg["bus"]["backoff"])

    def command(self, name):
        return self.commands.get(name) or [sys.executable, str(self.root / f"{name}.py"), str(self.folder)]

    def spawn(self, name):
        self.procs[name] = subprocess.Popen(self.command(name))
        self.when[name] = time.monotonic()
        self.fails.setdefault(name, 0)
        self.backoff.setdefault(name, self.step)

    def clear_lines(self, name):
        address = f"{i2c.addr(self.cfg, name):02x}"
        i2c.remove(self.wire / "scl" / address)
        i2c.remove(self.wire / address / "alive")

    def hung(self):
        folder = self.wire / "scl"
        if not folder.is_dir():
            return
        now = time.time()
        for path in list(folder.iterdir()):
            if not path.is_file():
                continue
            started = float(path.read_text(encoding="utf-8").split()[1])
            address = int(path.name, 16)
            name = next(key for key, value in self.cfg["address"].items() if int(value, 16) == address)
            proc = self.procs.get(name)
            if proc is not None and proc.poll() is not None:
                i2c.remove(path)
                continue
            if proc is not None and proc.poll() is None and now - started > float(self.cfg["busy"][name]):
                proc.terminate()
                i2c.remove(path)

    def run(self):
        if self.wire.exists():
            shutil.rmtree(self.wire)
        self.wire.mkdir()
        self.folder.mkdir(parents=True, exist_ok=True)
        names = list(self.commands) or list(NAMES)
        for name in names:
            self.spawn(name)
        while True:
            self.hung()
            for name in names:
                if name in self.restart:
                    if time.monotonic() >= self.restart[name]:
                        self.restart.pop(name)
                        self.spawn(name)
                    continue
                proc = self.procs[name]
                if proc.poll() is None:
                    if time.monotonic() - self.when[name] > self.stable:
                        self.fails[name] = 0
                        self.backoff[name] = self.step
                    continue
                self.clear_lines(name)
                if time.monotonic() - self.when[name] < self.stable:
                    self.fails[name] += 1
                else:
                    self.fails[name] = 0
                    self.backoff[name] = self.step
                if name == "luna" and self.fails[name] >= self.cap:
                    print("Luna cannot start", file=sys.stderr)
                    home = self.wire / "16"
                    home.mkdir(parents=True, exist_ok=True)
                    (home / "down").write_text("Luna cannot start\n", encoding="utf-8")
                    self.stop()
                    return 1
                self.restart[name] = time.monotonic() + self.backoff[name]
                self.backoff[name] = min(self.backoff[name] * 2, 30.0)
            time.sleep(0.05)

    def stop(self):
        for proc in self.procs.values():
            if proc.poll() is None:
                proc.terminate()


def main():
    root, cfg = i2c.load()
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    run = root / f"RUN_{stamp}"
    (root / "session" / stamp).mkdir(parents=True)
    code = Supply(root, cfg, run).run()
    raise SystemExit(code)


def test():
    import tempfile

    root = Path(tempfile.mkdtemp())
    cfg = i2c.test_cfg()
    cfg["bus"]["backoff"] = 0.05
    cfg["bus"]["luna_start_cap"] = 2
    cfg["busy"]["tools"] = 1
    supply = Supply(root, cfg, root / "RUN_test")
    supply.wire.mkdir(parents=True)
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    supply.procs["tools"] = sleeper
    scl = supply.wire / "scl"
    scl.mkdir()
    (scl / "14").write_text(f"{sleeper.pid} {time.time() - 30}\n", encoding="utf-8")
    supply.hung()
    sleeper.wait(timeout=5)
    assert sleeper.poll() is not None
    dead = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"])
    dead.wait(timeout=5)
    supply.procs["ears"] = dead
    (scl / "12").write_text(f"{dead.pid} {time.time()}\n", encoding="utf-8")
    supply.hung()
    assert not (scl / "12").exists()
    import io
    import contextlib
    fail = Supply(
        root, cfg, root / "RUN_fail",
        {"luna": [sys.executable, "-c", "import sys; sys.exit(1)"]},
    )
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        code = fail.run()
    assert code == 1
    assert buf.getvalue().strip() == "Luna cannot start"
    assert (fail.wire / "16" / "down").is_file()
    import shutil
    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test() if "--test" in sys.argv else main()
