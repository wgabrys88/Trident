import sqlite3
import sys
from pathlib import Path

import i2c

APPEND = 1
ROW = 2
LATEST = 3


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
        if reg == APPEND:
            self.db.execute("insert into rec(body) values (?)", (body,))
            self.db.commit()
            return body
        if reg == ROW:
            row = self.db.execute("select body from rec where id=?", (int(body.decode()),)).fetchone()
            if row is None:
                raise i2c.Nack()
            return bytes(row[0])
        if reg == LATEST:
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


if __name__ == "__main__":
    main()
