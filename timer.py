import asyncio, time
from bus import Device

class Timer(Device):
    async def start(self):
        self.deadline, self.attempts = float('inf'), 0
    async def receive(self, source, frame):
        if frame.register == '00': return f'{int(self.deadline < float("inf")):02d}'.encode()
        if frame.register == '03': self.deadline, self.attempts = float('inf'), 0
        if frame.register == '02' and self.attempts < self.cfg['timer']['redial_cap']:
            self.deadline = time.monotonic() + self.cfg['timer']['redial_seconds']
        return b''
    async def worker(self):
        while True:
            if time.monotonic() >= self.deadline:
                self.deadline, self.attempts = float('inf'), self.attempts + 1
                self.tasks.create_task(self.send('telegram', '', '01', once=True))
            if time.time() - (self.run / 'events.jsonl').stat().st_mtime >= self.cfg['timer']['idle_seconds']:
                if await self.send('telegram', '', '00', True, True) == b'00':
                    await self.send('mind', self.cfg['timer']['idle_text'], '01', once=True)
            await asyncio.sleep(self.cfg['bus']['poll'])

Timer('timer').launch()
