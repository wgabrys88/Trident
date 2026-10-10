import asyncio, time
from bus import Device

class Timer(Device):
    async def start(self):
        self.deadline, self.attempts = float('inf'), 0
        self.started, self.next_idle = time.time(), time.monotonic() + self.cfg['timer']['idle_seconds']
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
            if time.monotonic() >= self.next_idle:
                self.next_idle = time.monotonic() + self.cfg['timer']['idle_seconds']
                activity = self.run / 'mind-activity'
                since = activity.stat().st_mtime if activity.exists() else self.started
                if time.time() - since >= self.cfg['timer']['idle_seconds']:
                    if await self.send('telegram', '', '00', True, True) == b'00' and await self.send('mind', '', '00', True, True) == b'00':
                        await self.send('mind', self.cfg['timer']['idle_text'], '01', once=True)
            await asyncio.sleep(self.cfg['bus']['poll'])

Timer('timer').launch()
