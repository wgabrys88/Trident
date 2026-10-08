import sqlite3
import sys
from pathlib import Path

import i2c


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute("create table if not exists rec(id integer primary key, body blob)")

    def latest(self):
        row = self.db.execute("select body from rec order by id desc limit 1").fetchone()
        return bytes(row[0]) if row else b""

    def op(self, data):
        if not data:
            return self.latest()
        reg, body = data[0], data[1:]
        if reg == 1:
            self.db.execute("insert into rec(body) values (?)", (body,))
            self.db.commit()
            return body
        if reg == 2:
            row = self.db.execute("select body from rec where id=?", (int(body.decode()),)).fetchone()
            if row is None:
                raise i2c.Nack()
            return bytes(row[0])
        if reg == 3:
            return self.latest()
        raise i2c.Nack()


def reply(address, line, payload, reading):
    if reading:
        return i2c.with_payload(line, payload)
    return i2c.pack_write(address, i2c.write_payload(line))


def main():
    root, cfg = i2c.load()
    run = Path(sys.argv[1])
    bus = i2c.Bus(root, i2c.addr(cfg, "memory"), run, cfg)
    store = Store(root / cfg["memory"]["path"])

    async def on_frame(_src, line):
        reading = line.split()[2] == "R" or " Sr " in line
        return reply(bus.addr, line, store.op(i2c.write_payload(line)), reading)

    i2c.entry(lambda: bus.run(on_frame))


def test():
    import asyncio
    import tempfile

    async def run():
        root = Path(tempfile.mkdtemp())
        cfg = i2c.test_cfg()
        life = root / "life" / "memory.sqlite"
        store = Store(life)
        bus = i2c.Bus(root, 0x50, root / "RUN_test", cfg)
        master = i2c.Bus(root, 0x10, root / "RUN_test", cfg)
        bus.up()

        async def on_frame(_src, line):
            reading = line.split()[2] == "R" or " Sr " in line
            return reply(0x50, line, store.op(i2c.write_payload(line)), reading)

        stop = [False]
        task = asyncio.create_task(i2c._peer(bus, on_frame, stop))
        call = i2c.pack_call(0x50, bytes([1, 0x68, 0x69]))
        reply_line = await master.request(0x50, call)
        assert i2c.read_payload(reply_line) == b"\x68\x69"
        again = await master.request(0x50, "S 50 R A P")
        assert i2c.read_payload(again) == b"\x68\x69"
        assert life.is_file()
        assert store.db.execute("select count(*) from rec").fetchone()[0] == 1
        stop[0] = True
        await task
        import shutil
        shutil.rmtree(root, ignore_errors=True)

    asyncio.run(run())


if __name__ == "__main__":
    test() if "--test" in sys.argv else main()
