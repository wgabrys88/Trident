import asyncio
import os
import sys
import time
import tomllib
from pathlib import Path

ADDRS = {"10", "11", "12", "13", "14", "16", "50"}


class Nack(Exception):
    pass


def load():
    root = Path(__file__).resolve().parent
    cfg = tomllib.loads((root / "config.toml").read_text(encoding="utf-8"))
    return root, cfg


def addr(cfg, name):
    return int(cfg["address"][name], 16)


def entry(fn):
    try:
        asyncio.run(fn())
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)


def io(fn):
    delay = 0.05
    for attempt in range(5):
        try:
            return fn()
        except OSError as error:
            if getattr(error, "winerror", None) not in (5, 32) or attempt == 4:
                raise
            time.sleep(delay)
            delay *= 2


def read_file(path):
    return io(lambda: Path(path).read_text(encoding="utf-8"))


def remove(path):
    path = Path(path)
    if path.exists():
        io(path.unlink)


def place(folder, name, text):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    tmp_dir = folder.parent.parent / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{name}.{os.getpid()}.tmp"
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    io(lambda: os.replace(tmp, folder / name))


def move(src, dst):
    io(lambda: os.replace(src, dst))


def pack_write(target, data):
    parts = ["S", f"{target:02x}", "W", "A"]
    for byte in data:
        parts += [f"{byte:02x}", "A"]
    parts.append("P")
    return " ".join(parts)


def pack_call(target, data):
    return pack_write(target, data)[:-2] + f" Sr {target:02x} R A P"


def write_payload(line):
    tok = line.split()
    if len(tok) < 4 or tok[2] != "W":
        return b""
    out = bytearray()
    index = 4
    while index < len(tok) and tok[index] not in ("Sr", "P"):
        out.append(int(tok[index], 16))
        index += 2
    return bytes(out)


def read_payload(line):
    tok = line.split()
    start = None
    for index, token in enumerate(tok):
        if token in ("S", "Sr") and index + 2 < len(tok) and tok[index + 2] == "R":
            start = index
    if start is None:
        return b""
    out = bytearray()
    index = start + 4
    while index < len(tok) and tok[index] != "P":
        if tok[index] not in ("A", "NA"):
            out.append(int(tok[index], 16))
        index += 1
    return bytes(out)


def data_nacked(line):
    tok = line.split()
    if len(tok) < 4 or tok[3] == "NA" or tok[2] != "W":
        return False
    index = 4
    while index < len(tok) and tok[index] not in ("Sr", "P"):
        if tok[index + 1] == "NA":
            return True
        index += 2
    return False


def nack(target):
    return f"S {target:02x} W NA P"


def nack_data(line):
    data = write_payload(line)
    target = line.split()[1]
    if not data:
        return nack(int(target, 16))
    return f"S {target} W A {data[0]:02x} NA P"


def with_payload(line, payload):
    tok = line.split()
    if tok[2] == "R":
        head = tok[:4]
    else:
        head = []
        for token in tok:
            if token in ("Sr", "P"):
                break
            head.append(token)
        head += ["Sr", tok[1], "R", "A"]
    parts = head
    for index, byte in enumerate(payload):
        parts += [f"{byte:02x}", "NA" if index == len(payload) - 1 else "A"]
    parts.append("P")
    return " ".join(parts)


def legal(line):
    tok = line.split()
    if len(tok) < 5 or tok[0] != "S" or tok[-1] != "P":
        return False

    def eat(index, marker):
        if index >= len(tok) or tok[index] != marker or index + 3 >= len(tok):
            return None
        aa = tok[index + 1].lower()
        rw = tok[index + 2]
        ack = tok[index + 3]
        if aa not in ADDRS or rw not in ("W", "R") or ack not in ("A", "NA"):
            return None
        cursor = index + 4
        if ack == "NA":
            return cursor if cursor < len(tok) and tok[cursor] == "P" else None
        count = 0
        last_na = False
        while cursor < len(tok) and tok[cursor] not in ("Sr", "P"):
            if cursor + 1 >= len(tok):
                return None
            hh, ak = tok[cursor], tok[cursor + 1]
            if len(hh) != 2 or ak not in ("A", "NA"):
                return None
            try:
                int(hh, 16)
            except ValueError:
                return None
            count += 1
            last_na = ak == "NA"
            cursor += 2
            if ak == "NA":
                break
        if rw == "R" and count and not last_na:
            return None
        if last_na and cursor < len(tok) and tok[cursor] not in ("P",):
            return None
        return cursor

    end = eat(0, "S")
    if end is None:
        return False
    if end < len(tok) and tok[end] == "Sr":
        if tok[end + 1].lower() != tok[1].lower():
            return False
        end = eat(end, "Sr")
        if end is None:
            return False
    return end == len(tok) - 1


class Bus:
    def __init__(self, root, address, run, cfg):
        self.root = Path(root) / "wire"
        self.addr = address
        self.logs = Path(run) if run else None
        self.cfg = cfg
        self.home = self.root / f"{address:02x}"
        self.inbox = self.home / "inbox"
        self.seq = 0
        self.err = 0
        self.handler = None
        self._serving = False
        self.poll = float(cfg["bus"]["poll"])
        self.frame_timeout = float(cfg["bus"]["frame_timeout"])
        self.bus_off_at = int(cfg["bus"]["bus_off"])
        self._busy = {int(value, 16): float(cfg["busy"][name]) for name, value in cfg["address"].items()}

    def up(self):
        self.home.mkdir(parents=True, exist_ok=True)
        self.inbox.mkdir(parents=True, exist_ok=True)
        (self.home / "alive").write_text(f"{os.getpid()} {time.time()}\n", encoding="utf-8")
        self.err = 0
        (self.home / "err").write_text("0", encoding="utf-8")
        remove(self.home / "busoff")

    def down(self):
        remove(self.home / "alive")
        remove(self.root / "scl" / f"{self.addr:02x}")

    def present(self, target):
        return (self.root / f"{target:02x}" / "alive").is_file()

    def off(self, target):
        return (self.root / f"{target:02x}" / "busoff").is_file()

    def down_flag(self, target):
        return (self.root / f"{target:02x}" / "down").is_file()

    def ready(self, target):
        return self.present(target) and not self.off(target) and not self.down_flag(target)

    def bump(self, delta):
        was = self.err
        self.err = max(0, self.err + delta)
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "err").write_text(str(self.err), encoding="utf-8")
        if was < self.bus_off_at <= self.err:
            (self.home / "busoff").write_text("1\n", encoding="utf-8")
            print(f"{self.addr:02x} bus-off", file=sys.stderr)

    def journal(self, src, dst, frame, note, ms):
        if self.logs is None:
            return
        self.logs.mkdir(parents=True, exist_ok=True)
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {src:02x} {dst:02x} {note} {ms} {frame}\n"
        with (self.logs / "bus.log").open("a", encoding="utf-8") as handle:
            handle.write(line)

    def stretch(self):
        return _Stretch(self)

    def scl_held(self, target):
        path = self.root / "scl" / f"{target:02x}"
        if not path.is_file():
            return None
        return time.time() - float(read_file(path).split()[1])

    async def serve(self, handler=None):
        handler = handler or self.handler
        if handler is None or self._serving:
            return
        self._serving = True
        try:
            await self._take(handler)
        finally:
            self._serving = False

    async def _take(self, handler):
        if not self.inbox.is_dir():
            return
        pending = sorted(path for path in self.inbox.iterdir() if path.is_file() and path.name.startswith("q-"))
        if not pending:
            return
        path = pending[0]
        line = read_file(path)
        src = int(path.name.split("-")[1], 16)
        remove(path)
        started = time.monotonic()
        try:
            if self.off(self.addr):
                reply = nack(self.addr)
                note = "bus-off"
            else:
                reply = await handler(src, line)
                note = "nack" if reply.split()[3] == "NA" or data_nacked(reply) else "ack"
                if note == "ack":
                    self.bump(-1)
        except Nack:
            reply = nack_data(line)
            note = "nack"
        except Exception as error:
            self.bump(8)
            reply = nack(self.addr)
            note = type(error).__name__
        seq = path.name.split("-")[2]
        place(self.root / f"{src:02x}" / "inbox", f"r-{seq}", reply)
        self.journal(src, self.addr, reply, note, int((time.monotonic() - started) * 1000))

    async def transfer(self, target, line):
        if self.off(target):
            raise RuntimeError(f"bus-off {target:02x}")
        if not self.present(target):
            raise RuntimeError(f"NACK {target:02x}")
        self.seq += 1
        seq = self.seq
        self.inbox.mkdir(parents=True, exist_ok=True)
        place(self.root / f"{target:02x}" / "inbox", f"q-{self.addr:02x}-{seq}", line)
        reply_path = self.inbox / f"r-{seq}"
        started = time.monotonic()
        limit = self.frame_timeout
        while not reply_path.exists():
            await self.serve()
            if self.scl_held(target) is not None:
                limit = max(limit, self._busy[target])
            if time.monotonic() - started > limit:
                self.bump(8)
                self.journal(self.addr, target, line, "timeout", int((time.monotonic() - started) * 1000))
                raise TimeoutError(f"timeout {target:02x}")
            await asyncio.sleep(self.poll)
        return read_file(reply_path)

    async def request(self, target, line):
        while True:
            if self.off(target) or not self.present(target):
                raise RuntimeError(f"NACK {target:02x}")
            reply = await self.transfer(target, line)
            remove(self.inbox / f"r-{self.seq}")
            if reply.split()[3] == "NA":
                if self.present(target) and not self.off(target):
                    deadline = time.monotonic() + self.frame_timeout
                    while time.monotonic() < deadline:
                        await self.serve()
                        await asyncio.sleep(self.poll)
                    continue
                raise RuntimeError(f"NACK {target:02x}")
            if data_nacked(reply):
                self.bump(8)
                raise RuntimeError(f"NACK {target:02x}")
            self.bump(-1)
            return reply

    async def run(self, handler, pump=None):
        self.handler = handler
        self.up()
        try:
            while True:
                await self.serve(handler)
                if pump:
                    await pump()
                await asyncio.sleep(self.poll)
        finally:
            self.down()


class _Stretch:
    def __init__(self, bus):
        self.bus = bus
        self.path = bus.root / "scl" / f"{bus.addr:02x}"

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(f"{os.getpid()} {time.time()}\n", encoding="utf-8")
        return self

    def __exit__(self, *_):
        remove(self.path)


def test_cfg():
    return {
        "address": {
            "timer": "10", "telegram": "11", "ears": "12", "voice": "13",
            "tools": "14", "luna": "16", "memory": "50",
        },
        "bus": {
            "frame_timeout": 0.4, "poll": 0.01, "bus_off": 32,
            "retry_seconds": 120, "redial_cap": 3, "self_turn_cap": 4,
            "luna_start_cap": 3, "stable_seconds": 30, "backoff": 1,
        },
        "busy": {
            "timer": 2, "telegram": 2, "ears": 2, "voice": 2,
            "tools": 2, "luna": 2, "memory": 2,
        },
    }


async def _peer(bus, handler, stop):
    while not stop[0]:
        await bus.serve(handler)
        await asyncio.sleep(bus.poll)


def test():
    asyncio.run(_test())


async def _test():
    import tempfile
    assert legal("S 11 W A 01 A P")
    assert legal("S 16 W NA P")
    assert legal("S 50 W A 01 A 68 A 69 A Sr 50 R A 68 A 69 NA P")
    assert legal("S 50 R A P")
    assert not legal("S 11 W A 01 A")
    assert not legal("S 99 W A 01 A P")
    assert not legal("S 50 W A Sr 11 R A P")
    cfg = test_cfg()
    root = Path(tempfile.mkdtemp())
    run = root / "RUN_test"
    target = Bus(root, 0x50, run, cfg)
    master = Bus(root, 0x10, run, cfg)
    target.up()
    seen = []

    async def on(_src, line):
        seen.append(line)
        data = write_payload(line)
        if data[:1] == b"\xff":
            raise Nack()
        if data[:1] == b"\xfe":
            with target.stretch():
                await asyncio.sleep(0.25)
        if " Sr " in line or line.split()[2] == "R":
            return with_payload(line, b"\x68\x69")
        return pack_write(0x50, data)

    stop = [False]
    task = asyncio.create_task(_peer(target, on, stop))
    reply = await master.request(0x50, pack_call(0x50, b"\x01\x68\x69"))
    assert read_payload(reply) == b"\x68\x69"
    assert (target.inbox.parent.parent / "50" / "inbox").is_dir()
    text = (run / "bus.log").read_text(encoding="utf-8")
    assert " ack " in text
    assert "S 50" in text
    held = []

    async def on_hold(_src, line):
        with target.stretch():
            held.append((target.root / "scl" / "50").is_file())
            await asyncio.sleep(0.25)
        return pack_write(0x50, write_payload(line))

    stop[0] = True
    await task
    mode = {"hold": True}

    async def on2(src, line):
        if mode["hold"]:
            return await on_hold(src, line)
        return await on(src, line)

    stop[0] = False
    task = asyncio.create_task(_peer(target, on2, stop))
    cfg_fast = test_cfg()
    cfg_fast["bus"]["frame_timeout"] = 0.15
    master2 = Bus(root, 0x10, run, cfg_fast)
    master2.seq = 100
    await master2.request(0x50, pack_write(0x50, b"\xfe"))
    assert held == [True]
    mode["hold"] = False
    busy = {"on": True}

    async def on3(src, line):
        if busy["on"]:
            return nack(0x50)
        return pack_write(0x50, write_payload(line))

    stop[0] = True
    await task
    stop[0] = False
    task = asyncio.create_task(_peer(target, on3, stop))
    one = await master.transfer(0x50, pack_write(0x50, b"\x02"))
    assert one.split()[3] == "NA"
    busy["on"] = False

    async def release():
        await asyncio.sleep(0.05)
        busy["on"] = False

    busy["on"] = True
    asyncio.create_task(release())
    reply = await master.request(0x50, pack_write(0x50, b"\x03"))
    assert reply.split()[3] == "A"
    stop[0] = True
    await task
    cfg_off = test_cfg()
    cfg_off["bus"]["frame_timeout"] = 0.2
    cfg_off["bus"]["bus_off"] = 8
    lonely = Bus(root, 0x14, run, cfg_off)
    sender = Bus(root, 0x12, run, cfg_off)
    lonely.up()
    try:
        await sender.transfer(0x14, pack_write(0x14, b"\x01"))
        raise AssertionError("timeout missing")
    except TimeoutError:
        pass
    assert sender.off(0x12)
    assert "timeout" in (run / "bus.log").read_text(encoding="utf-8")
    src = root / "wire" / "tmp" / "locked.txt"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("frame", encoding="utf-8")
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CreateFileW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(str(src), 0x80000000, 1, None, 3, 0x80, None)
    assert handle not in (None, ctypes.c_void_p(-1).value)

    def close():
        time.sleep(0.12)
        kernel.CloseHandle(handle)

    import threading
    threading.Thread(target=close).start()
    move(src, root / "wire" / "tmp" / "moved.txt")
    assert (root / "wire" / "tmp" / "moved.txt").read_text(encoding="utf-8") == "frame"
    import shutil
    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    if "--test" in sys.argv:
        test()
    else:
        raise SystemExit("i2c.py is the wire")
