import sys
from pathlib import Path

import i2c


class Injector:
    def __init__(self, bus):
        self.bus = bus
        self.last = b""

    async def on_frame(self, _src, line):
        data = i2c.write_payload(line)
        reading = line.split()[2] == "R" or " Sr " in line
        if line.split()[2] == "R" and not data:
            return i2c.with_payload(line, self.last)
        if not data or data[0] != 1:
            raise i2c.Nack()
        frame = data[1:].decode()
        if not i2c.legal(frame):
            raise i2c.Nack()
        target = int(frame.split()[1], 16)
        with self.bus.stretch():
            reply = await self.bus.request(target, frame)
        self.last = reply.encode()
        if reading:
            return i2c.with_payload(line, self.last)
        return i2c.pack_write(self.bus.addr, data)


def main():
    root, cfg = i2c.load()
    run = Path(sys.argv[1])
    bus = i2c.Bus(root, i2c.addr(cfg, "injector"), run, cfg)
    i2c.entry(lambda: bus.run(Injector(bus).on_frame))


def test():
    import asyncio
    import tempfile

    seen = []

    async def on_target(src, line):
        seen.append(src)
        return i2c.pack_write(0x14, i2c.write_payload(line))

    async def run():
        root = Path(tempfile.mkdtemp())
        cfg = i2c.test_cfg()
        run_dir = root / "RUN_test"
        target = i2c.Bus(root, 0x14, run_dir, cfg)
        target.up()
        stop = [False]
        peer = asyncio.create_task(i2c._peer(target, on_target, stop))
        bus = i2c.Bus(root, 0x48, run_dir, cfg)
        device = Injector(bus)
        loop = asyncio.create_task(bus.run(device.on_frame))
        for _ in range(50):
            if bus.present(0x48):
                break
            await asyncio.sleep(0.02)
        master = i2c.Bus(root, 0x10, run_dir, cfg)
        frame = i2c.pack_write(0x14, b"\x02ok")
        call = i2c.pack_call(0x48, bytes([1]) + frame.encode())
        reply = await master.request(0x48, call)
        assert seen == [0x48]
        assert b"S 14" in i2c.read_payload(reply)
        stop[0] = True
        loop.cancel()
        await asyncio.gather(peer, loop, return_exceptions=True)
        import shutil
        shutil.rmtree(root, ignore_errors=True)

    asyncio.run(run())


if __name__ == "__main__":
    test() if "--test" in sys.argv else main()
