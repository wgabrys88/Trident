import asyncio, ctypes, os, time
import numpy as np
from bus import Device

class Backend(ctypes.Structure):
    _fields_ = [('size', ctypes.c_size_t), ('gpu', ctypes.c_int32)]
class Model(ctypes.Structure):
    _fields_ = [('size', ctypes.c_size_t), ('path', ctypes.c_char_p), ('name', ctypes.c_char_p)]
class Asr(ctypes.Structure):
    _fields_ = [('size', ctypes.c_size_t)] + [(key, ctypes.c_void_p) for key in ('backend', 'model', 'streaming', 'decoder', 'vad', 'endpointing', 'postproc', 'diar', 'batching')]

class Ears(Device):
    def bind(self, name, result, arguments):
        function = getattr(self.library, 'nemo_speech_asr_' + name)
        function.restype, function.argtypes = result, arguments
        return function
    def check(self, status):
        if status: raise RuntimeError(self.error().decode())
    async def start(self):
        pointer = ctypes.c_void_p
        path = self.cfg.path(self.cfg['ears']['library'])
        self.directory = os.add_dll_directory(str(path.parent))
        self.cleanup.callback(self.directory.close)
        self.library = ctypes.CDLL(str(path))
        self.error = self.bind('last_error', ctypes.c_char_p, [])
        self.recognize = self.bind('recognize_f32', ctypes.c_int, [pointer, pointer, ctypes.POINTER(ctypes.c_float), ctypes.c_size_t, ctypes.c_int32, ctypes.POINTER(pointer)])
        self.transcript, self.free = self.bind('result_transcript', ctypes.c_char_p, [pointer, ctypes.c_size_t]), self.bind('result_destroy', None, [pointer])
        self.destroy = self.bind('destroy', None, [pointer])
        self.backend = Backend(ctypes.sizeof(Backend), self.cfg['ears']['gpu'])
        self.model = Model(ctypes.sizeof(Model), str(self.cfg.path(self.cfg['models']['path']) / self.cfg['ears']['model']).encode(), None)
        config, self.handle = Asr(), pointer()
        config.size, config.backend, config.model = ctypes.sizeof(Asr), ctypes.addressof(self.backend), ctypes.addressof(self.model)
        create = self.bind('create', ctypes.c_int, [ctypes.POINTER(Asr), ctypes.POINTER(pointer)])
        self.check(await asyncio.wait_for(asyncio.to_thread(create, ctypes.byref(config), ctypes.byref(self.handle)), self.cfg['ears']['start_seconds']))
    def decode(self, filename):
        samples = np.frombuffer(self.cfg.path(filename).read_bytes(), dtype='<i2').astype(np.float32) / 32768
        result = ctypes.c_void_p()
        self.check(self.recognize(self.handle, None, samples.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(samples), self.cfg['audio']['receive_rate'], ctypes.byref(result)))
        try: return self.transcript(result, 0).decode().strip()
        finally: self.free(result)
    async def work(self, source, frame):
        text = await asyncio.to_thread(self.decode, frame.text)
        marker = self.run / 'telegram-bridge'
        bridged = marker.exists() and time.time() - marker.stat().st_mtime < self.cfg['telegram']['bridge_seconds']
        with self.file('heard.txt').open('a', encoding='utf-8') as log: log.write(f'{time.time():.6f} {text}\n')
        if bridged and self.cfg['telegram']['bridge_release'].lower() in text.lower(): marker.unlink(); bridged = False
        if text and not bridged: await self.send('mind', text, '01')
    async def close(self):
        self.destroy(self.handle)

Ears('ears').launch()
