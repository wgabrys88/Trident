import asyncio, time
import soundfile
from bus import Device, Server

class Voice(Device):
    async def start(self):
        self.server = Server(self, self.cfg['voice'])
        await self.server.start()
    async def work(self, source, frame):
        audio = await asyncio.to_thread(self.server.request, '/v1/audio/speech', dict(input=frame.text, response_format='wav'))
        path = self.file(f'{time.time_ns()}.wav')
        path.write_bytes(audio)
        if frame.register == '02': soundfile.write(path.with_suffix('.ogg'), *soundfile.read(path), format='OGG', subtype='OPUS')
        await self.send('telegram', str(path.with_suffix({'01': '.wav', '02': '.ogg'}[frame.register])), {'01': '20', '02': '21'}[frame.register])

Voice('voice').launch()
