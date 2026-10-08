import asyncio
import os
import sys
import time
import tomllib
import urllib.error
import urllib.request
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


def program_argv(parts, root):
    argv = []
    for part in parts:
        text = os.path.expandvars(str(part))
        if text.startswith('artifacts/'):
            text = str(Path(root) / text)
        argv.append(text)
    return argv


def healthy(url, timeout):
    try:
        with urllib.request.urlopen(url + '/health', timeout=timeout) as response:
            return response.status == 200
    except urllib.error.HTTPError as error:
        if error.code == 503:
            return False
        raise
    except urllib.error.URLError:
        return False


async def serve_until(root, parts, url, limits, limit, missing):
    argv = program_argv(parts, root)
    if not Path(argv[0]).is_file():
        raise RuntimeError(missing)
    for part in argv[1:]:
        if part.endswith('.gguf') and not Path(part).is_file():
            raise RuntimeError(missing)
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(root),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    err = bytearray()
    keep = int(limits['stderr_keep'])

    async def drain():
        while True:
            block = await proc.stderr.read(keep)
            if not block:
                return
            err.extend(block)
            del err[:-keep]

    reader = asyncio.create_task(drain())
    started = asyncio.get_running_loop().time()
    while proc.returncode is None:
        if await asyncio.to_thread(healthy, url, float(limits['health_timeout'])):
            return proc, reader
        if asyncio.get_running_loop().time() - started > limit:
            proc.kill()
            await proc.wait()
            raise RuntimeError(missing)
        await asyncio.sleep(float(limits['health_poll']))
    detail = err.decode('utf-8', 'replace').strip().splitlines()
    tail = detail[-1] if detail else 'no stderr'
    raise RuntimeError(f'{missing} {proc.returncode} {tail}')


async def serve_stop(proc, reader):
    if proc is not None and proc.returncode is None:
        proc.kill()
        await proc.wait()
    if reader is not None:
        await reader


async def wait_healthy(url, limits, limit, missing):
    started = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - started <= limit:
        if await asyncio.to_thread(healthy, url, float(limits['health_timeout'])):
            return
        await asyncio.sleep(float(limits['health_poll']))
    raise RuntimeError(missing)


if __name__ == '__main__':
    raise SystemExit('i2c.py is the wire')
