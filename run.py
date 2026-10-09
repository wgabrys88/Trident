import json
import shutil
import signal
import subprocess
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime

from i2c import Configuration


@dataclass(slots=True)
class DeviceProcess:
    command: list
    log: object
    process: subprocess.Popen | None = None
    started: float = 0
    failures: int = 0
    restart_at: float = 0

    def spawn(self):
        self.process = subprocess.Popen(self.command, stdout=self.log, stderr=self.log,
                                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        self.started, self.restart_at = time.monotonic(), 0

    def stop(self, seconds):
        if self.process.poll() is None:
            self.process.send_signal(signal.CTRL_BREAK_EVENT)
            try:
                self.process.wait(timeout=seconds)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


class Supply:
    def __init__(self, part):
        self.cfg = Configuration()
        self.cfg.mind(part)
        self.part, self.processes = part, {}
        self.run = self.cfg.root / "runs" / datetime.now().strftime("%Y%m%dT%H%M%S%f")

    def start(self, cleanup):
        wire = self.cfg.root / "wire"
        if wire.exists():
            shutil.rmtree(wire)
        self.run.mkdir(parents=True)
        (self.run / "bus.log").touch()
        for name, address in self.cfg["address"].items():
            home = wire / address
            home.mkdir(parents=True)
            (home / "state.json").write_text(json.dumps({"sequence": 0, "tx": 0, "rx": 0}))
            command = [sys.executable, str(self.cfg.root / self.cfg["modules"][name]), str(self.run)]
            command.append(self.part if name == "mind" else name)
            log = cleanup.enter_context((self.run / f"{name}.log").open("ab"))
            process = DeviceProcess(command, log)
            process.spawn()
            self.processes[name] = process
            cleanup.callback(process.stop, self.cfg["supply"]["stop_seconds"])

    def supervise(self):
        with ExitStack() as cleanup:
            self.start(cleanup)
            while True:
                for name, device in self.processes.items():
                    home = self.cfg.root / "wire" / self.cfg["address"][name]
                    hold = home / "hold"
                    if device.process.poll() is None and hold.is_file():
                        held_at = float(hold.read_text().split()[1])
                        if time.time() - held_at > self.cfg["holds"][name]:
                            device.stop(self.cfg["supply"]["stop_seconds"])
                    now = time.monotonic()
                    stable = now - device.started >= self.cfg["supply"]["stable_seconds"]
                    if device.process.poll() is None:
                        if stable:
                            device.failures = 0
                        continue
                    if not device.restart_at:
                        for marker in ("alive", "hold"):
                            (home / marker).unlink(missing_ok=True)
                        if (home / "busoff").is_file():
                            state = json.loads((home / "state.json").read_text())
                            state["tx"], state["rx"] = 0, 0
                            (home / "state.json").write_text(json.dumps(state))
                            (home / "busoff").unlink()
                        device.failures = 0 if stable else device.failures + 1
                        if device.failures >= self.cfg["supply"]["restart_cap"]:
                            raise RuntimeError(f"{name} exceeded its restart cap; see {name}.log")
                        delay = min(self.cfg["supply"]["backoff"] * self.cfg["supply"]["backoff_multiplier"] ** max(0, device.failures - 1),
                                    self.cfg["supply"]["backoff_cap"])
                        device.restart_at = now + delay
                    elif now >= device.restart_at:
                        device.spawn()
                time.sleep(self.cfg["bus"]["poll"])
if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise ValueError("Run requires one configured mind part: run.py luna or run.py lfm")
    signal.signal(signal.SIGBREAK, signal.default_int_handler)
    Supply(sys.argv[1]).supervise()
