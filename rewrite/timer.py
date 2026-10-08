import sys
import time
from pathlib import Path

import i2c


class Timer:
    def __init__(self, bus, seconds, cap, telegram, luna):
        self.bus = bus
        self.seconds = seconds
        self.cap = cap
        self.telegram = telegram
        self.luna = luna
        self.deadline = None
        self.redials = 0
        self.booted = False

    def luna_up(self):
        return self.bus.ready(self.luna)

    async def on_frame(self, _src, line):
        if line.split()[2] == "R":
            return i2c.with_payload(line, bytes([1 if self.deadline else 0]))
        data = i2c.write_payload(line)
        if not data:
            raise i2c.Nack()
        if data[0] == 2:
            if self.deadline is None and self.redials < self.cap and self.luna_up():
                self.deadline = time.monotonic() + self.seconds
            return i2c.pack_write(self.bus.addr, data)
        if data[0] == 3:
            self.deadline = None
            self.redials = 0
            return i2c.pack_write(self.bus.addr, data)
        raise i2c.Nack()

    async def pump(self):
        if not self.booted:
            if not (self.bus.present(self.telegram) and self.luna_up()):
                return
            self.booted = True
            await self.bus.request(self.telegram, i2c.pack_write(self.telegram, b"\x01"))
            return
        if self.deadline is None or time.monotonic() < self.deadline:
            return
        self.deadline = None
        if self.redials >= self.cap or not self.luna_up():
            return
        self.redials += 1
        await self.bus.request(self.telegram, i2c.pack_write(self.telegram, b"\x01"))


def main():
    root, cfg = i2c.load()
    run = Path(sys.argv[1])
    bus = i2c.Bus(root, i2c.addr(cfg, "timer"), run, cfg)
    timer = Timer(
        bus, float(cfg["bus"]["retry_seconds"]), int(cfg["bus"]["redial_cap"]),
        i2c.addr(cfg, "telegram"), i2c.addr(cfg, "luna"),
    )
    i2c.entry(lambda: bus.run(timer.on_frame, timer.pump))


def test():
    import asyncio
    import tempfile

    async def run():
        root = Path(tempfile.mkdtemp())
        cfg = i2c.test_cfg()
        run_dir = root / "RUN_test"
        timer_bus = i2c.Bus(root, 0x10, run_dir, cfg)
        phone = i2c.Bus(root, 0x11, run_dir, cfg)
        luna = root / "wire" / "16"
        luna.mkdir(parents=True)
        (luna / "alive").write_text("1 0\n", encoding="utf-8")
        phone.up()
        seen = []

        async def on_phone(_src, line):
            seen.append(i2c.write_payload(line))
            return i2c.pack_write(0x11, i2c.write_payload(line))

        timer = Timer(timer_bus, 0.4, 2, 0x11, 0x16)
        stop = [False]
        phones = asyncio.create_task(i2c._peer(phone, on_phone, stop))
        clocks = asyncio.create_task(timer_bus.run(timer.on_frame, timer.pump))
        for _ in range(50):
            if seen:
                break
            await asyncio.sleep(0.02)
        assert seen == [b"\x01"]
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
        import shutil
        shutil.rmtree(root, ignore_errors=True)

    asyncio.run(run())


if __name__ == "__main__":
    test() if "--test" in sys.argv else main()
