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


def build(bus, cfg, root, _run):
    store = Store(root / cfg["memory"]["path"])

    async def on_frame(_src, line):
        reading = line.split()[2] == "R" or " Sr " in line
        payload = store.op(i2c.write_payload(line))
        if reading:
            return i2c.reply(line, i2c.write_payload(line), payload)
        return i2c.reply(line, i2c.write_payload(line))

    return on_frame, None, None


def main():
    i2c.main_for("memory", build)


def test():
    import asyncio

    async def run():
        cfg = i2c.test_cfg()
        holder = {}

        async def on_frame(_src, line):
            reading = line.split()[2] == "R" or " Sr " in line
            payload = holder["store"].op(i2c.write_payload(line))
            if reading:
                return i2c.reply(line, i2c.write_payload(line), payload)
            return i2c.reply(line, i2c.write_payload(line))

        async def body(stand, buses):
            life = stand.root / "life" / "memory.sqlite"
            holder["store"] = Store(life)
            master = stand.bus(0x10)
            call = i2c.pack_call(0x50, bytes([1, 0x68, 0x69]))
            reply_line = await master.request(0x50, call)
            assert i2c.read_payload(reply_line) == b"\x68\x69"
            again = await master.request(0x50, "S 50 R A P")
            assert i2c.read_payload(again) == b"\x68\x69"
            assert life.is_file()
            assert holder["store"].db.execute("select count(*) from rec").fetchone()[0] == 1

        await i2c.rehearse(cfg, {0x50: on_frame}, body)

    asyncio.run(run())


if __name__ == "__main__":
    test() if "--test" in sys.argv else main()
