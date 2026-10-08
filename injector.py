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


if __name__ == "__main__":
    main()
