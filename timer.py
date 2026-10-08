import sys
import time

import i2c

MISSED = 2
CLEAR = 3
DIAL = 1
IDENT = 0xF0


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
        if data[0] == MISSED:
            if self.deadline is None and self.redials < self.cap and self.mind_up():
                self.deadline = time.monotonic() + self.seconds
            return i2c.pack_write(self.bus.addr, data)
        if data[0] == CLEAR:
            self.deadline = None
            self.redials = 0
            return i2c.pack_write(self.bus.addr, data)
        raise i2c.Nack()

    async def pump(self):
        if not self.booted:
            if not (self.bus.present(self.telegram) and self.mind_up()):
                return
            self.booted = True
            await self.bus.request(self.mind, i2c.pack_call(self.mind, bytes([IDENT])))
            await self.bus.request(self.telegram, i2c.pack_write(self.telegram, bytes([DIAL])))
            return
        if self.deadline is None or time.monotonic() < self.deadline:
            return
        self.deadline = None
        if self.redials >= self.cap or not self.mind_up():
            return
        self.redials += 1
        await self.bus.request(self.telegram, i2c.pack_write(self.telegram, bytes([DIAL])))


def build(bus, cfg, _root, _run):
    timer = Timer(
        bus, float(cfg["bus"]["retry_seconds"]), int(cfg["bus"]["redial_cap"]),
        i2c.addr(cfg, "telegram"), i2c.addr(cfg, "mind"),
    )
    return timer.on_frame, timer.pump, None


def main():
    i2c.main_for("timer", build)


if __name__ == "__main__":
    main()
