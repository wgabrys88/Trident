import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import i2c

NAMES = ["timer", "telegram", "ears", "voice", "tools", "mind", "memory"]


class Supply:
    def __init__(self, root, cfg, run, part):
        self.root = Path(root)
        self.cfg = cfg
        self.folder = Path(run)
        self.part = part
        self.procs = {}
        self.when = {}
        self.restart = {}
        self.fails = {}
        self.backoff = {}
        self.wire = self.root / "wire"
        self.cap = int(cfg["bus"]["restart_cap"])
        self.stable = float(cfg["bus"]["stable_seconds"])
        self.step = float(cfg["bus"]["backoff"])

    def command(self, name):
        argv = [sys.executable, str(self.root / f"{name}.py"), str(self.folder)]
        if name == "mind":
            argv.append(self.part)
        return argv

    def spawn(self, name):
        self.procs[name] = subprocess.Popen(self.command(name))
        self.when[name] = time.monotonic()
        self.fails.setdefault(name, 0)
        self.backoff.setdefault(name, self.step)

    def clear_lines(self, name):
        address = i2c.addr(self.cfg, name)
        i2c.remove(i2c.scl_of(self.wire, address))
        i2c.remove(i2c.home_of(self.wire, address) / "alive")

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
            if proc is None:
                continue
            if proc.poll() is not None:
                i2c.remove(path)
                continue
            if name in self.cfg["busy"] and now - started > float(self.cfg["busy"][name]):
                proc.terminate()
                i2c.remove(path)

    def run(self):
        if self.wire.exists():
            shutil.rmtree(self.wire)
        self.wire.mkdir()
        self.folder.mkdir(parents=True, exist_ok=True)
        names = list(NAMES)
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
                lasted = time.monotonic() - self.when[name] > self.stable
                if proc.poll() is None:
                    if lasted:
                        self.fails[name] = 0
                        self.backoff[name] = self.step
                    continue
                self.clear_lines(name)
                address = i2c.addr(self.cfg, name)
                aa = f"{address:02x}"
                if (i2c.home_of(self.wire, address) / "busoff").is_file():
                    print(f"{aa} bus-off", file=sys.stderr)
                if lasted:
                    self.fails[name] = 0
                    self.backoff[name] = self.step
                else:
                    self.fails[name] += 1
                if self.fails[name] >= self.cap:
                    print(f"{aa} cannot start", file=sys.stderr)
                    home = i2c.home_of(self.wire, address)
                    home.mkdir(parents=True, exist_ok=True)
                    (home / "down").write_text(f"{aa} cannot start\n", encoding="utf-8")
                    self.stop()
                    return 1
                self.restart[name] = time.monotonic() + self.backoff[name]
                self.backoff[name] = min(self.backoff[name] * 2, float(self.cfg["bus"]["backoff_cap"]))
            time.sleep(float(self.cfg["bus"]["poll"]))

    def stop(self):
        for proc in self.procs.values():
            if proc.poll() is None:
                proc.terminate()


def main():
    root, cfg = i2c.load()
    if len(sys.argv) < 2 or not isinstance(cfg["mind"].get(sys.argv[1]), dict):
        raise RuntimeError("mind part is missing")
    part = sys.argv[1]
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    run = root / f"RUN_{stamp}"
    (root / "session" / stamp).mkdir(parents=True)
    code = Supply(root, cfg, run, part).run()
    raise SystemExit(code)


if __name__ == "__main__":
    main()
