import asyncio
import sqlite3
import sys
import time

from i2c import Device, Nack, Refusal


class Memory(Device):
    def __init__(self):
        super().__init__("memory")
        path = self.cfg.path(self.cfg["memory"]["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        self.database = sqlite3.connect(path)
        self.database.execute("CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY, text TEXT NOT NULL)")

    async def receive(self, source, frame):
        register, text = frame.data[0], frame.data[1:].decode()
        if register == 1:
            cursor = self.database.execute("INSERT INTO records(text) VALUES (?)", (text,))
            self.database.commit()
            result = str(cursor.lastrowid)
        elif register == 2:
            row = self.database.execute("SELECT text FROM records WHERE id = ?", (text,)).fetchone() if text.isdecimal() else None
            if row is None:
                raise Nack(Refusal.UNKNOWN_DATA)
            result = row[0]
        elif register == 3:
            result = (self.database.execute("SELECT text FROM records ORDER BY id DESC LIMIT 1").fetchone() or ("",))[0]
        return result.encode() if frame.reading else b""

    async def close(self):
        self.database.close()


class Timer(Device):
    def __init__(self):
        super().__init__("timer")
        self.deadline, self.redials, self.dial_task = None, 0, None

    async def receive(self, source, frame):
        match frame.data[0]:
            case 0 if frame.reading:
                return f"{int(self.deadline is not None):02d}".encode()
            case 2:
                if self.redials < self.cfg["timer"]["redial_cap"]:
                    self.deadline = time.monotonic() + self.cfg["timer"]["redial_seconds"]
            case 3:
                self.deadline, self.redials = None, 0
                if self.dial_task is not None:
                    self.dial_task.cancel()
        return b""

    async def tick(self):
        while not all(self.bus.present(self.cfg["address"][name]) for name in ("telegram", "mind")):
            await asyncio.sleep(self.cfg["bus"]["poll"])
        await self.send("mind", "", "f0", reading=True)
        while True:
            if self.deadline is not None and time.monotonic() >= self.deadline:
                self.deadline = None
                self.redials += 1
                self.dial_task = self.tasks.create_task(self.send("telegram", "", "01"))
            if time.time() - (self.run / "bus.log").stat().st_mtime >= self.cfg["timer"]["idle_seconds"] and await self.send("telegram", "", "00", reading=True) == b"00":
                await self.send("mind", self.cfg["timer"]["idle_text"], "01")
            await asyncio.sleep(self.cfg["bus"]["poll"])


if __name__ == "__main__":
    {"timer": Timer, "memory": Memory}[sys.argv[2]]().launch()
