import os
import signal
import sys
import tomllib
import urllib.error
import urllib.request
import asyncio
import json
import re
import time
from dataclasses import dataclass
from contextlib import AsyncExitStack, suppress
from enum import IntEnum
from pathlib import Path

TRANSMIT_ERROR_STEP = 8
RECEIVE_ERROR_STEP = 1
ERROR_PASSIVE = 128
ERROR_ACTIVE_MAX = 127
RECEIVE_RECOVERY = 119
BUS_OFF = 256

class Refusal(IntEnum):
    NO_RECEIVER = 1
    NOT_READY = 2
    UNKNOWN_DATA = 3
    FULL = 4
    END_OF_READ = 5

class Nack(Exception):
    def __init__(self, cause: Refusal):
        self.cause = cause
        super().__init__(cause.name.replace("_", " ").lower())

@dataclass(frozen=True, slots=True)
class Frame:
    address: str
    data: bytes
    reading: bool = False

    def encode(self, result: bytes = b"", refusal: Refusal | None = None) -> str:
        if refusal in (Refusal.NO_RECEIVER, Refusal.NOT_READY):
            return f"S {self.address} W NA P"
        if refusal == Refusal.UNKNOWN_DATA:
            return f"S {self.address} W A {self.data[0]:02x} NA P"
        if refusal == Refusal.FULL:
            return Frame(self.address, self.data).encode().removesuffix(" A P") + " NA P"
        line = f"S {self.address} W A"
        line += "".join(f" {byte:02x} A" for byte in self.data)
        if self.reading:
            line += f" Sr {self.address} R A"
            line += "".join(f" {byte:02x} {'NA' if index == len(result) - 1 else 'A'}"
                            for index, byte in enumerate(result))
        return line + " P"

    @classmethod
    def decode(cls, line: str):
        if (match := re.fullmatch(r"S ([0-7][0-9a-f]) W A((?: [0-9a-f]{2} A)+)( Sr \1 R A)? P", line)) is None:
            raise ValueError("Invalid I2C transaction")
        return cls(match[1], bytes.fromhex(match[2].replace(" A", "")), bool(match[3]))

    def result(self, line: str) -> bytes:
        write = self.encode().removesuffix(" P")
        if not self.reading:
            if line != self.encode():
                raise ValueError("Invalid write acknowledgement")
            return b""
        if (match := re.fullmatch(re.escape(write) + r"((?: [0-9a-f]{2} A)* [0-9a-f]{2} NA)? P", line)) is None:
            raise ValueError("Invalid read acknowledgement")
        return bytes.fromhex((match[1] or "").replace(" NA", "").replace(" A", ""))

class Bus:
    def __init__(self, root: Path, run: Path, address: str, settings: dict, holds: dict):
        self.wire, self.run, self.address = root / "wire", run, address
        self.settings, self.holds = settings, holds
        self.home = self.wire / address
        self.state = json.loads((self.home / "state.json").read_text())
        self.lock = asyncio.Lock()
        self.incoming = set()

    def save(self):
        self.place(self.home / "state.json", json.dumps(self.state))

    @staticmethod
    def place(path: Path, text: str):
        staging = path.with_suffix(".pending")
        staging.write_text(text, encoding="utf-8")
        staging.replace(path)

    def present(self, address: str) -> bool:
        return (self.wire / address / "alive").is_file()

    @property
    def passive(self) -> bool:
        return max(self.state["tx"], self.state["rx"]) >= ERROR_PASSIVE

    def transmit_fault(self, acknowledgement: bool):
        if not (acknowledgement and self.passive):
            self.state["tx"] += TRANSMIT_ERROR_STEP
        self.save()
        if self.state["tx"] >= BUS_OFF:
            (self.home / "busoff").touch()
            raise RuntimeError("Bus off")

    def journal(self, source: str, target: str, line: str, outcome: str):
        with (self.run / "bus.log").open("a", encoding="utf-8") as journal:
            journal.write(f"{time.time():.6f} {source} {target} {outcome} {line} tx={self.state['tx']} rx={self.state['rx']}\n")

    async def serve(self, device):
        while True:
            for path in sorted(self.home.glob("q-*.frame")):
                if path not in self.incoming:
                    self.incoming.add(path)
                    device.tasks.create_task(self.receive(device, path))
            await asyncio.sleep(self.settings["poll"])

    async def receive(self, device, path):
        source, sequence = path.stem.split("-")[1:]
        line = path.read_text(encoding="utf-8")
        try:
            frame = Frame.decode(line)
            if frame.address != self.address or not (self.wire / source).is_dir():
                raise ValueError("Transaction delivered to the wrong address or from an absent controller")
        except ValueError:
            self.state["rx"] += RECEIVE_ERROR_STEP
            self.save()
            self.journal(source, self.address, line, "malformed")
            self.incoming.remove(path)
            return path.unlink()
        held = None
        try:
            try:
                if any(self.home.glob("hold-*")):
                    raise Nack(Refusal.NOT_READY)
                registers = device.cfg["registers"][device.name]
                register = f"{frame.data[0]:02x}"
                direction = "read" if frame.reading else "write"
                if register not in registers or direction not in registers[register].split(":")[0].split("/"):
                    raise Nack(Refusal.UNKNOWN_DATA)
                if self.address in self.holds and frame.reading:
                    (held := self.home / f"hold-{source}-{sequence}-{time.time()}").touch()
                result = await device.receive(source, frame)
                reply = frame.encode(result)
                self.state["rx"] = RECEIVE_RECOVERY if self.state["rx"] > ERROR_ACTIVE_MAX else max(0, self.state["rx"] - RECEIVE_ERROR_STEP)
                self.save()
                outcome = Refusal.END_OF_READ.name if frame.reading and result else "ack"
            except Nack as error:
                reply, outcome = frame.encode(refusal=error.cause), error.cause.name
            except Exception:
                sys.excepthook(*sys.exc_info())
                reply, outcome = frame.encode(refusal=Refusal.UNKNOWN_DATA), Refusal.UNKNOWN_DATA.name
            self.place(self.wire / source / f"r-{sequence}.frame", reply)
            path.unlink()
            self.incoming.remove(path)
            self.journal(source, self.address, reply, outcome)
        finally:
            if held:
                held.unlink()

    async def transfer(self, frame: Frame, once: bool = False) -> bytes:
        async with self.lock:
            retries = 0
            while True:
                if self.passive:
                    await asyncio.sleep(self.settings["frame_timeout"])
                if not self.present(frame.address):
                    self.journal(self.address, frame.address, frame.encode(refusal=Refusal.NO_RECEIVER), "NO_RECEIVER")
                    raise Nack(Refusal.NO_RECEIVER)
                self.state["sequence"] += 1
                sequence = self.state["sequence"]
                self.save()
                self.place(self.wire / frame.address / f"q-{self.address}-{sequence}.frame", frame.encode())
                reply_path = self.home / f"r-{sequence}.frame"
                deadline = time.time() + self.settings["frame_timeout"]
                while not reply_path.is_file():
                    for hold in (self.wire / frame.address).glob(f"hold-{self.address}-{sequence}-*"):
                        deadline = float(hold.name.rsplit("-", 1)[1]) + self.holds[frame.address]
                    if time.time() > deadline:
                        self.transmit_fault(acknowledgement=True)
                        self.journal(self.address, frame.address, frame.encode(), "timeout")
                        raise TimeoutError(f"No acknowledgement from {frame.address}")
                    await asyncio.sleep(self.settings["poll"])
                reply = reply_path.read_text(encoding="utf-8")
                reply_path.unlink()
                if reply == frame.encode(refusal=Refusal.NOT_READY):
                    if once:
                        return b""
                    await asyncio.sleep(self.settings["frame_timeout"])
                    continue
                if reply == frame.encode(refusal=Refusal.FULL) != frame.encode(refusal=Refusal.UNKNOWN_DATA):
                    raise Nack(Refusal.FULL)
                if reply == frame.encode(refusal=Refusal.UNKNOWN_DATA):
                    if retries == self.settings["data_retries"]:
                        raise Nack(Refusal.UNKNOWN_DATA)
                    retries += 1
                    continue
                try:
                    result = frame.result(reply)
                except ValueError:
                    self.journal(self.address, frame.address, reply, "malformed_reply")
                    self.transmit_fault(acknowledgement=False)
                    raise
                self.state["tx"] = max(0, self.state["tx"] - 1)
                self.save()
                return result

class Configuration(dict):
    def __init__(self):
        self.root = Path(__file__).resolve().parent
        super().__init__(tomllib.loads((self.root / "config.toml").read_text(encoding="utf-8")))

    def path(self, value: str) -> Path:
        return self.root / os.path.expandvars(value)

    def mind(self, name: str) -> dict:
        if name not in self["mind"]:
            raise ValueError("A configured mind part is required")
        return self["mind"][name]

class Server:
    def __init__(self, device, settings: dict):
        self.device, self.settings, self.process, self.url = device, settings, None, settings["url"]

    def request(self, endpoint: str, body: dict) -> bytes:
        request = urllib.request.Request(self.url + endpoint, json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.device.cfg["limits"]["http_timeout"]) as response:
            return response.read()

    async def chat(self, messages: list, **options) -> str:
        body = {"model": self.settings["model"], "messages": messages,
                **self.device.cfg["sampling"], "max_tokens": self.settings["max_tokens"], **options}
        return json.loads(await asyncio.to_thread(self.request, "/v1/chat/completions", body))["choices"][0]["message"]["content"]

    def health(self):
        try:
            with urllib.request.urlopen(self.url + "/health", timeout=self.device.cfg["limits"]["health_timeout"]):
                return True
        except urllib.error.HTTPError as error:
            if error.code != 503:
                raise
        except urllib.error.URLError as error:
            if not isinstance(error.reason, ConnectionRefusedError):
                raise
        return False

    async def start(self):
        cfg = self.device.cfg
        parts = [os.path.expandvars(str(part)).format(**self.settings) for part in self.settings["command"]]
        arguments = [str(cfg.path(part)) if part.startswith("artifacts/") else part for part in parts]
        with self.device.file("server.log").open("ab") as log:
            self.process = await asyncio.create_subprocess_exec(*arguments, cwd=cfg.root, stdout=log, stderr=log)
        self.device.tasks.create_task(self.watch())
        deadline = time.monotonic() + self.settings["start_seconds"]
        while not await asyncio.to_thread(self.health):
            if time.monotonic() >= deadline:
                raise TimeoutError("Server startup expired")
            await asyncio.sleep(cfg["limits"]["health_poll"])

    async def watch(self):
        raise RuntimeError(f"Server exited {await self.process.wait()}; see {self.device.name}-server.log")

    async def close(self):
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()

class Device:
    def __init__(self, name: str):
        self.name, self.cfg, self.run = name, Configuration(), Path(sys.argv[1])
        self.address = self.cfg["address"][name]
        holds = {self.cfg["address"][role]: seconds for role, seconds in self.cfg["holds"].items()}
        self.bus = Bus(self.cfg.root, self.run, self.address, self.cfg["bus"], holds)
        self.server = None

    def file(self, name: str) -> Path:
        return self.run / f"{self.name}-{name}"

    async def send(self, name: str, text: str, register: str, reading: bool = False, once: bool = False, guarded: bool = False):
        with suppress(*(Nack, TimeoutError) * guarded):
            return await self.bus.transfer(Frame(self.cfg["address"][name], bytes.fromhex(register) + text.encode(), reading), once)

    async def run_device(self):
        task = asyncio.current_task()
        signal.signal(signal.SIGBREAK, lambda number, frame: (signal.signal(number, signal.SIG_IGN), task.cancel()))
        async with AsyncExitStack() as cleanup, asyncio.TaskGroup() as self.tasks:
            if self.server is not None:
                cleanup.push_async_callback(self.server.close)
                await self.server.start()
            await self.start()
            cleanup.push_async_callback(self.close)
            alive = self.bus.home / "alive"
            alive.write_text(str(os.getpid()))
            cleanup.callback(alive.unlink)
            self.tasks.create_task(self.bus.serve(self))
            self.tasks.create_task(self.tick())

    async def start(self):
        return None

    async def close(self):
        return None

    async def tick(self):
        await asyncio.Future()

    def launch(self):
        asyncio.run(self.run_device())

class QueuedDevice(Device):
    def __init__(self, name):
        super().__init__(name)
        self.queue = asyncio.Queue()

    async def receive(self, source, frame):
        self.queue.put_nowait((source, frame))
        return b""

    async def tick(self):
        while True:
            await self.work(*await self.queue.get())
