import sqlite3
from bus import Device, Refused

class Memory(Device):
    async def start(self):
        path = self.run / 'memory.sqlite'
        self.database = sqlite3.connect(path)
        self.database.execute('CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY, text TEXT NOT NULL)')
        # Restart-safe within this run; a new run has an independent database.
        seed = 'done: setup; resume: nothing'
        self.database.execute(
            'INSERT INTO records(text) SELECT ? WHERE NOT EXISTS '
            '(SELECT 1 FROM records WHERE text=?)', (seed, seed)
        )
        self.database.commit()
    async def receive(self, source, frame):
        queries = {'01': ('INSERT INTO records(text) VALUES(?) RETURNING id', (frame.text,)), '02': ('SELECT text FROM records WHERE id=?', (frame.text,)), '03': ('SELECT text FROM records ORDER BY id DESC LIMIT 1', ())}
        row = self.database.execute(*queries[frame.register]).fetchone()
        if row is None: raise Refused('UNKNOWN_DATA')
        self.database.commit()
        return str(row[0]).encode() * frame.read
    async def close(self):
        self.database.close()

Memory('memory').launch()
