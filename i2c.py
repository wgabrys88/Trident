import asyncio
import os
import shutil
import sys
import tempfile
import time
import tomllib
from pathlib import Path

TEC_STEP = 8
REC_STEP = 1
ERROR_PASSIVE = 128
BUS_OFF_AT = 256
REC_RECOVER = 119
ERROR_ACTIVE = 127


class Nack(Exception):
    pass


class BusOff(BaseException):
    pass


def load():
    root = Path(__file__).resolve().parent
    cfg = tomllib.loads((root / "config.toml").read_text(encoding="utf-8"))
    return root, cfg


def addr(cfg, name):
    return int(cfg["address"][name], 16)


def home_of(wire, address):
    return Path(wire) / f"{address:02x}"


def scl_of(wire, address):
    return Path(wire) / "scl" / f"{address:02x}"


def entry(fn):
    try:
        asyncio.run(fn())
    except BusOff:
        raise SystemExit(1)
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)


def main_for(name, build):
    root, cfg = load()
    run = Path(sys.argv[1])
    bus = Bus(root, addr(cfg, name), run, cfg)
    handler, pump, around = build(bus, cfg, root, run)
    if around:
        entry(lambda: around(bus.run(handler, pump)))
    else:
        entry(lambda: bus.run(handler, pump))


def io(fn):
    bus = load()[1]["bus"]
    delay = float(bus["share_delay"])
    retries = int(bus["share_retries"])
    for attempt in range(retries):
        try:
            return fn()
        except OSError as error:
            if getattr(error, "winerror", None) not in (5, 32) or attempt == retries - 1:
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


def nack_addr(line, address):
    tok = line.split()
    if len(tok) >= 3 and tok[0] == "S" and tok[2] in ("W", "R"):
        return f"S {tok[1]} {tok[2]} NA P"
    return f"S {address:02x} W NA P"


def nack_data(line):
    data = write_payload(line)
    target = line.split()[1]
    if not data:
        return nack_addr(line, int(target, 16))
    return f"S {target} W A {data[0]:02x} NA P"


def reply(line, data, payload=None):
    if payload is not None and (line.split()[2] == "R" or " Sr " in line):
        return with_payload(line, payload)
    return pack_write(int(line.split()[1], 16), data)


def accept(line, register):
    data = write_payload(line)
    if not data or data[0] != register:
        raise Nack()
    return data


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


def legal(line, cfg=None):
    tok = line.split()
    if len(tok) < 5 or tok[0] != "S" or tok[-1] != "P":
        return False
    addrs = {value.lower() for value in (cfg or load()[1])["address"].values()}

    def eat(index, marker):
        if index >= len(tok) or tok[index] != marker or index + 3 >= len(tok):
            return None
        aa = tok[index + 1].lower()
        rw = tok[index + 2]
        ack = tok[index + 3]
        if aa not in addrs or rw not in ("W", "R") or ack not in ("A", "NA"):
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
        self.home = home_of(self.root, address)
        self.inbox = self.home / "inbox"
        self.seq = 0
        self.tec = 0
        self.rec = 0
        self.dead = False
        self.handler = None
        self._serving = False
        self.poll = float(cfg["bus"]["poll"])
        self.frame_timeout = float(cfg["bus"]["frame_timeout"])
        self.nack_retries = int(cfg["bus"]["nack_retries"])
        self._busy = {
            int(value, 16): float(cfg["busy"][name])
            for name, value in cfg["address"].items()
            if name in cfg["busy"]
        }

    def error_passive(self):
        return self.tec >= ERROR_PASSIVE or self.rec >= ERROR_PASSIVE

    def error_active(self):
        return self.tec <= ERROR_ACTIVE and self.rec <= ERROR_ACTIVE

    def store(self):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "tec").write_text(f"{self.tec}\n", encoding="utf-8")
        (self.home / "rec").write_text(f"{self.rec}\n", encoding="utf-8")

    def _count(self, name):
        path = self.home / name
        if not path.is_file():
            return 0
        return int(path.read_text(encoding="utf-8").strip())

    def up(self):
        self.home.mkdir(parents=True, exist_ok=True)
        self.inbox.mkdir(parents=True, exist_ok=True)
        (self.home / "alive").write_text(f"{os.getpid()} {time.time()}\n", encoding="utf-8")
        if (self.home / "busoff").is_file():
            self.tec = 0
            self.rec = 0
            remove(self.home / "busoff")
        else:
            self.tec = self._count("tec")
            self.rec = self._count("rec")
        self.dead = False
        self.store()

    def down(self):
        remove(self.home / "alive")
        remove(scl_of(self.root, self.addr))

    def present(self, target):
        return (home_of(self.root, target) / "alive").is_file()

    def off(self, target):
        return (home_of(self.root, target) / "busoff").is_file()

    def down_flag(self, target):
        return (home_of(self.root, target) / "down").is_file()

    def ready(self, target):
        return self.present(target) and not self.off(target) and not self.down_flag(target)

    def tx_success(self):
        if self.tec:
            self.tec -= 1
            self.store()

    def tx_ack_error(self):
        if self.error_passive():
            return
        self.tec += TEC_STEP
        self.store()
        self._bus_off()

    def tx_fault(self):
        self.tec += TEC_STEP
        self.store()
        self._bus_off()

    def rx_fault(self):
        self.rec += REC_STEP
        self.store()

    def rx_success(self):
        if not self.rec:
            return
        self.rec = REC_RECOVER if self.rec > ERROR_ACTIVE else self.rec - 1
        self.store()

    def _bus_off(self):
        if self.tec >= BUS_OFF_AT:
            self.dead = True
            (self.home / "busoff").write_text("1\n", encoding="utf-8")
            raise BusOff()

    def post(self, line):
        self.seq += 1
        place(self.inbox, f"q-{self.addr:02x}-{self.seq}", line)

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
        path = scl_of(self.root, target)
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
        if not self.inbox.is_dir() or self.dead:
            return
        pending = sorted(path for path in self.inbox.iterdir() if path.is_file() and path.name.startswith("q-"))
        if not pending:
            return
        path = pending[0]
        line = read_file(path)
        src = int(path.name.split("-")[1], 16)
        started = time.monotonic()
        try:
            if not legal(line, self.cfg):
                self.rx_fault()
                reply_line = self._form_reply(line)
                note = "form"
            else:
                reply_line = await handler(src, line)
                note = "nack" if reply_line.split()[3] == "NA" or data_nacked(reply_line) else "ack"
                if note == "ack":
                    self.rx_success()
        except Nack:
            reply_line = nack_data(line)
            note = "nack"
        except BusOff:
            raise
        except Exception:
            self.rx_fault()
            reply_line = nack_data(line)
            note = "fault"
        place(self.root / f"{src:02x}" / "inbox", f"r-{path.name.split('-')[2]}", reply_line)
        remove(path)
        self.journal(src, self.addr, reply_line, note, int((time.monotonic() - started) * 1000))

    def _form_reply(self, line):
        tok = line.split()
        if len(tok) >= 6 and tok[0] == "S" and tok[2] == "W" and tok[3] == "A" and len(tok[4]) == 2:
            try:
                byte = int(tok[4], 16)
            except ValueError:
                return nack_addr(line, self.addr)
            return f"S {tok[1]} W A {byte:02x} NA P"
        return nack_addr(line, self.addr)

    async def _suspend(self):
        if not self.error_passive():
            return
        deadline = time.monotonic() + self.frame_timeout
        while time.monotonic() < deadline:
            if self.dead:
                raise BusOff()
            await self.serve()
            await asyncio.sleep(self.poll)

    async def transfer(self, target, line):
        await self._suspend()
        if self.dead:
            raise BusOff()
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
            if self.scl_held(target) is not None and target in self._busy:
                limit = max(limit, self._busy[target])
            if time.monotonic() - started > limit:
                self.tx_ack_error()
                self.journal(self.addr, target, line, "timeout", int((time.monotonic() - started) * 1000))
                raise TimeoutError(f"timeout {target:02x}")
            await asyncio.sleep(self.poll)
        return read_file(reply_path)

    async def request(self, target, line):
        data_tries = 0
        while True:
            if self.dead:
                raise BusOff()
            if self.off(target) or not self.present(target):
                raise RuntimeError(f"NACK {target:02x}")
            reply_line = await self.transfer(target, line)
            remove(self.inbox / f"r-{self.seq}")
            if reply_line.split()[3] == "NA":
                if self.present(target) and not self.off(target):
                    deadline = time.monotonic() + self.frame_timeout
                    while time.monotonic() < deadline:
                        await self.serve()
                        await asyncio.sleep(self.poll)
                    continue
                raise RuntimeError(f"NACK {target:02x}")
            if data_nacked(reply_line):
                data_tries += 1
                if data_tries <= self.nack_retries:
                    continue
                raise RuntimeError(f"NACK {target:02x}")
            self.tx_success()
            return reply_line

    async def run(self, handler, pump=None):
        self.handler = handler
        self.up()
        try:
            while True:
                if self.dead:
                    raise BusOff()
                await self.serve(handler)
                if self.dead:
                    raise BusOff()
                if pump:
                    await pump()
                if self.dead:
                    raise BusOff()
                await asyncio.sleep(self.poll)
        finally:
            self.down()


class _Stretch:
    def __init__(self, bus):
        self.path = scl_of(bus.root, bus.addr)

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(f"{os.getpid()} {time.time()}\n", encoding="utf-8")
        return self

    def __exit__(self, *_):
        remove(self.path)


def test_cfg():
    cfg = load()[1]
    stand = cfg["selftest"]
    cfg["bus"]["frame_timeout"] = stand["frame_timeout"]
    cfg["bus"]["poll"] = stand["poll"]
    for name in cfg["busy"]:
        cfg["busy"][name] = stand["busy"]
    return cfg


async def _peer(bus, handler, stop):
    while not stop[0]:
        await bus.serve(handler)
        await asyncio.sleep(bus.poll)


class Stand:
    def __init__(self, cfg):
        self.root = Path(tempfile.mkdtemp())
        self.run = self.root / "RUN_test"
        self.run.mkdir()
        self.cfg = cfg
        self.stop = [False]
        self.tasks = []

    def bus(self, address):
        return Bus(self.root, address, self.run, self.cfg)

    def peer(self, address, handler):
        bus = self.bus(address)
        bus.up()
        self.tasks.append(asyncio.create_task(_peer(bus, handler, self.stop)))
        return bus

    async def close(self):
        self.stop[0] = True
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        shutil.rmtree(self.root, ignore_errors=True)


async def rehearse(cfg, peers, body):
    stand = Stand(cfg)
    buses = {address: stand.peer(address, handler) for address, handler in peers.items()}
    try:
        await body(stand, buses)
    finally:
        await stand.close()


def test():
    asyncio.run(_test())


async def _test():
    assert legal("S 11 W A 01 A P")
    assert legal("S 16 W NA P")
    assert legal("S 50 W A 01 A 68 A 69 A Sr 50 R A 68 A 69 NA P")
    assert legal("S 50 R A P")
    assert legal("S 48 W A 01 A P")
    assert not legal("S 11 W A 01 A")
    assert not legal("S 99 W A 01 A P")
    assert not legal("S 08 W A 01 A P")
    assert not legal("S 50 W A Sr 11 R A P")
    cfg = test_cfg()
    cfg["busy"]["memory"] = cfg["selftest"]["busy"]
    root = Path(tempfile.mkdtemp())
    run = root / "RUN_test"
    target = Bus(root, 0x50, run, cfg)
    master = Bus(root, 0x10, run, cfg)
    target.up()

    async def on(_src, line):
        assert any(target.inbox.glob("q-*"))
        data = write_payload(line)
        if data[:1] == b"\xff":
            raise Nack()
        if data[:1] == b"\xfe":
            with target.stretch():
                await asyncio.sleep(0.25)
        if data[:1] == b"\xfd":
            raise RuntimeError("handler")
        if " Sr " in line or line.split()[2] == "R":
            return with_payload(line, b"\x68\x69")
        return pack_write(0x50, data)

    stop = [False]
    task = asyncio.create_task(_peer(target, on, stop))
    answered = await master.request(0x50, pack_call(0x50, b"\x01\x68\x69"))
    assert read_payload(answered) == b"\x68\x69"
    assert list(target.inbox.glob("q-*")) == []
    text = (run / "bus.log").read_text(encoding="utf-8")
    assert " ack " in text
    assert "S 50" in text
    held = []

    async def on_hold(_src, line):
        with target.stretch():
            held.append(scl_of(target.root, 0x50).is_file())
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
    cfg_fast["busy"]["memory"] = cfg_fast["selftest"]["busy"]
    master2 = Bus(root, 0x10, run, cfg_fast)
    master2.seq = 100
    await master2.request(0x50, pack_write(0x50, b"\xfe"))
    assert held == [True]
    mode["hold"] = False
    busy = {"on": True}

    async def on3(src, line):
        if busy["on"]:
            return nack_addr(line, 0x50)
        return pack_write(0x50, write_payload(line))

    stop[0] = True
    await task
    stop[0] = False
    task = asyncio.create_task(_peer(target, on3, stop))
    one = await master.transfer(0x50, "S 50 R A P")
    assert one == "S 50 R NA P"
    busy["on"] = False

    async def release():
        await asyncio.sleep(0.05)
        busy["on"] = False

    busy["on"] = True
    asyncio.create_task(release())
    again = await master.request(0x50, pack_write(0x50, b"\x03"))
    assert again.split()[3] == "A"
    stop[0] = True
    await task
    stop[0] = False
    task = asyncio.create_task(_peer(target, on, stop))
    before = target.rec
    tec_before = master.tec
    try:
        await master.request(0x50, pack_write(0x50, b"\xff"))
        raise AssertionError("deliberate nack")
    except RuntimeError:
        pass
    assert target.rec == before
    assert master.tec == tec_before
    try:
        await master.request(0x50, pack_write(0x50, b"\xfd"))
        raise AssertionError("handler fault")
    except RuntimeError:
        pass
    assert target.rec == before + 1 + cfg["bus"]["nack_retries"]
    assert master.tec == tec_before
    stop[0] = True
    await task
    place(target.inbox, "q-10-77", "NOT A FRAME\n")
    await target.serve(on)
    assert target.rec == before + 1 + cfg["bus"]["nack_retries"] + 1
    form = read_file(master.inbox / "r-77")
    assert form == "S 50 W NA P"
    target.tec = 40
    target.rec = 9
    target.store()
    target.down()
    target.up()
    assert target.tec == 40 and target.rec == 9
    (target.home / "busoff").write_text("1\n", encoding="utf-8")
    target.up()
    assert target.tec == 0 and target.rec == 0
    assert not (target.home / "busoff").is_file()
    node = Bus(root, 0x14, run, cfg)
    node.up()
    node.rec = 130
    node.rx_success()
    assert node.rec == REC_RECOVER
    node.rec = 4
    node.rx_success()
    assert node.rec == 3
    node.rec = 0
    node.rx_success()
    assert node.rec == 0
    node.tec = 127
    node.rec = 119
    assert node.error_active()
    node.tec = ERROR_PASSIVE
    assert node.error_passive() and not node.error_active()
    cfg_off = test_cfg()
    cfg_off["bus"]["frame_timeout"] = 0.05
    cfg_off["bus"]["poll"] = 0.01
    lonely = Bus(root, 0x13, run, cfg_off)
    sender = Bus(root, 0x12, run, cfg_off)
    lonely.up()
    for _ in range(16):
        try:
            await sender.transfer(0x13, pack_write(0x13, b"\x01"))
        except TimeoutError:
            pass
    assert sender.tec == ERROR_PASSIVE
    assert sender.error_passive()
    try:
        await sender.transfer(0x13, pack_write(0x13, b"\x01"))
    except TimeoutError:
        pass
    assert sender.tec == ERROR_PASSIVE
    assert "timeout" in (run / "bus.log").read_text(encoding="utf-8")
    sender.tec = BUS_OFF_AT - TEC_STEP
    sender.store()
    try:
        sender.tx_fault()
        raise AssertionError("bus-off missing")
    except BusOff:
        pass
    assert sender.tec == BUS_OFF_AT
    assert sender.off(0x12)
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
    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    if "--test" in sys.argv:
        test()
    else:
        raise SystemExit("i2c.py is the wire")
