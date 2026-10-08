import sys
import time
from pathlib import Path

import i2c


class Timer:
    def __init__(self, bus, seconds, cap, telegram, mind):
        self.bus = bus
        self.seconds = seconds
        self.cap = cap
        self.telegram = telegram
        self.mind = mind
        self.deadline = None
        self.redials = 0
        self.booted = False

    def mind_up(self):
        return self.bus.ready(self.mind)

    async def on_frame(self, _src, line):
        if line.split()[2] == "R":
            return i2c.reply(line, i2c.write_payload(line), bytes([1 if self.deadline else 0]))
        data = i2c.write_payload(line)
        if not data:
            raise i2c.Nack()
        if data[0] == 2:
            if self.deadline is None and self.redials < self.cap and self.mind_up():
                self.deadline = time.monotonic() + self.seconds
            return i2c.pack_write(self.bus.addr, data)
        if data[0] == 3:
            self.deadline = None
            self.redials = 0
            return i2c.pack_write(self.bus.addr, data)
        raise i2c.Nack()

    async def pump(self):
        if not self.booted:
            if not (self.bus.present(self.telegram) and self.mind_up()):
                return
            self.booted = True
            await self.bus.request(self.mind, i2c.pack_call(self.mind, b"\xf0"))
            await self.bus.request(self.telegram, i2c.pack_write(self.telegram, b"\x01"))
            return
        if self.deadline is None or time.monotonic() < self.deadline:
            return
        self.deadline = None
        if self.redials >= self.cap or not self.mind_up():
            return
        self.redials += 1
        await self.bus.request(self.telegram, i2c.pack_write(self.telegram, b"\x01"))


def build(bus, cfg, _root, _run):
    timer = Timer(
        bus, float(cfg["bus"]["retry_seconds"]), int(cfg["bus"]["redial_cap"]),
        i2c.addr(cfg, "telegram"), i2c.addr(cfg, "mind"),
    )
    return timer.on_frame, timer.pump, None


def main():
    i2c.main_for("timer", build)


def test():
    import asyncio
    import tempfile

    async def run():
        root = Path(tempfile.mkdtemp())
        cfg = i2c.test_cfg()
        run_dir = root / "RUN_test"
        timer_bus = i2c.Bus(root, 0x10, run_dir, cfg)
        phone = i2c.Bus(root, 0x11, run_dir, cfg)
        mind = i2c.Bus(root, 0x16, run_dir, cfg)
        phone.up()
        mind.up()
        seen = []

        async def on_phone(_src, line):
            seen.append(i2c.write_payload(line))
            return i2c.pack_write(0x11, i2c.write_payload(line))

        async def on_mind(_src, line):
            if i2c.write_payload(line) == b"\xf0":
                return i2c.with_payload(line, b"cursor;gpt-5.6-luna-none;cli;text")
            return i2c.pack_write(0x16, i2c.write_payload(line))

        timer = Timer(timer_bus, 0.4, 2, 0x11, 0x16)
        stop = [False]
        phones = asyncio.create_task(i2c._peer(phone, on_phone, stop))
        minds = asyncio.create_task(i2c._peer(mind, on_mind, stop))
        clocks = asyncio.create_task(timer_bus.run(timer.on_frame, timer.pump))
        for _ in range(50):
            if seen:
                break
            await asyncio.sleep(0.02)
        assert seen == [b"\x01"]
        assert "63 A 75 A 72 A 73 A 6f A 72" in (run_dir / "bus.log").read_text(encoding="utf-8")
        await phone.request(0x10, i2c.pack_write(0x10, b"\x02"))
        status = await phone.request(0x10, "S 10 R A P")
        assert i2c.read_payload(status) == b"\x01"
        assert not (root / "wire" / "scl" / "10").exists()
        await phone.request(0x10, i2c.pack_write(0x10, b"\x02"))
        await asyncio.sleep(0.2)
        assert seen == [b"\x01"]
        await asyncio.sleep(0.35)
        assert seen == [b"\x01", b"\x01"]
        await phone.request(0x10, i2c.pack_write(0x10, b"\x02"))
        await asyncio.sleep(0.55)
        assert seen == [b"\x01", b"\x01", b"\x01"]
        await phone.request(0x10, i2c.pack_write(0x10, b"\x02"))
        await asyncio.sleep(0.55)
        assert seen == [b"\x01", b"\x01", b"\x01"]
        await phone.request(0x10, i2c.pack_write(0x10, b"\x03"))
        status = await phone.request(0x10, "S 10 R A P")
        assert i2c.read_payload(status) == b"\x00"
        assert timer.redials == 0
        clocks.cancel()
        stop[0] = True
        await phones
        await minds
        import shutil
        shutil.rmtree(root, ignore_errors=True)

    asyncio.run(run())


if __name__ == "__main__":
    test() if "--test" in sys.argv else main()
