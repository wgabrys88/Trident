import sys

import i2c


class Injector:
    def __init__(self, bus):
        self.bus = bus
        self.last = b""
        self.pending = []

    async def on_frame(self, _src, line):
        data = i2c.write_payload(line)
        if line.split()[2] == "R" and not data:
            return i2c.reply(line, b"", self.last)
        if not data or data[0] != 1:
            raise i2c.Nack()
        frame = data[1:].decode()
        if not i2c.legal(frame):
            raise i2c.Nack()
        self.pending.append(frame)
        return i2c.pack_write(self.bus.addr, data)

    async def pump(self):
        if not self.pending:
            return
        frame = self.pending.pop(0)
        target = int(frame.split()[1], 16)
        answered = await self.bus.request(target, frame)
        self.last = answered.encode()


def build(bus, _cfg, _root, _run):
    device = Injector(bus)
    return device.on_frame, device.pump, None


def main():
    i2c.main_for("injector", build)


def test():
    import asyncio

    seen = []

    async def on_target(src, line):
        seen.append(src)
        return i2c.pack_write(0x14, i2c.write_payload(line))

    async def run():
        cfg = i2c.test_cfg()

        async def body(stand, _buses):
            bus = stand.bus(0x48)
            device = Injector(bus)
            loop = asyncio.create_task(bus.run(device.on_frame, device.pump))
            try:
                for _ in range(50):
                    if bus.present(0x48):
                        break
                    await asyncio.sleep(0.02)
                master = stand.bus(0x10)
                frame = i2c.pack_write(0x14, b"\x02ok")
                await master.request(0x48, i2c.pack_write(0x48, bytes([1]) + frame.encode()))
                for _ in range(50):
                    if seen == [0x48] and device.last:
                        break
                    await asyncio.sleep(0.02)
                assert seen == [0x48]
                reply = await master.request(0x48, "S 48 R A P")
                assert b"S 14" in i2c.read_payload(reply)
                assert not (stand.root / "wire" / "scl" / "48").exists()
            finally:
                loop.cancel()
                await asyncio.gather(loop, return_exceptions=True)

        await i2c.rehearse(cfg, {0x14: on_target}, body)

    asyncio.run(run())


if __name__ == "__main__":
    test() if "--test" in sys.argv else main()
