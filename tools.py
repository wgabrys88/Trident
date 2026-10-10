import asyncio, base64, ctypes, json, re, time
import pyperclip
from PIL import Image, ImageGrab, ImageOps, PngImagePlugin
from bus import Device, Refused, Server

class Tools(Device):
    async def start(self):
        if not ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)): raise RuntimeError('DPI initialization failed')
        import pyautogui
        self.input, self.latest = pyautogui, None
        self.input.FAILSAFE, self.input.PAUSE = False, self.cfg['tools']['input_pause']
        self.server = Server(self, self.cfg['llama'] | self.cfg['vision'])
        await self.server.start()
    async def receive(self, source, frame):
        return await {'01': self.shot, '02': self.command, '03': self.vision, '04': self.act, '05': self.share}[frame.register](frame.text)
    async def shot(self, text):
        name, *values = text.split()
        if re.fullmatch(r'[A-Za-z0-9_-]+\.png', name) is None: raise Refused('UNKNOWN_DATA')
        grid = self.cfg['limits']['grid']
        box = list(map(int, values)) if values else [0, 0, grid, grid]
        a, b, c, d = box
        if not 0 <= a < c <= grid or not 0 <= b < d <= grid: raise Refused('UNKNOWN_DATA')
        image = await asyncio.to_thread(ImageGrab.grab, all_screens=True)
        bounds = tuple(round(value * (size - 1) / grid) for value, size in zip(box, image.size * 2))
        info = PngImagePlugin.PngInfo()
        info.add_text('crop', ' '.join(map(str, box)))
        path = self.run / f'{name[:-4]}-{time.time_ns()}.png'
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(image.crop((bounds[0], bounds[1], bounds[2] + 1, bounds[3] + 1)).save, path, pnginfo=info)
        self.latest = str(path)
        return self.latest.encode()
    async def command(self, text):
        process = await asyncio.create_subprocess_shell(text, cwd=self.run, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), self.cfg['tools']['command_seconds'])
            if process.returncode == 0: await asyncio.sleep(self.cfg['tools']['launch_seconds'])
            return f'exit {process.returncode}\n'.encode() + output
        except Exception as error: return f'Command started; outcome uncertain: {error!r}. Observe before repeating.'.encode()
        finally:
            if process.returncode is None: process.kill(); await process.wait()
    async def act(self, text):
        operation, _, argument = text.partition(' ')
        try:
            if operation in ('click', 'draw'):
                coordinates = list(map(int, argument.split()))
                points = list(zip(coordinates[::2], coordinates[1::2], strict=True))
                if (operation == 'click' and len(points) != 1) or (operation == 'draw' and len(points) < 2) or not all(0 <= value <= self.cfg['limits']['grid'] for value in coordinates): raise ValueError('Invalid stroke')
                perform = lambda: self.stroke(points)
            elif operation == 'type': perform = lambda: (pyperclip.copy(argument), self.input.hotkey('ctrl', 'v'))
            elif operation == 'key' and argument in self.input.KEYBOARD_KEYS: perform = lambda: self.input.press(argument)
            else: raise ValueError('Unknown input')
        except ValueError: raise Refused('UNKNOWN_DATA') from None
        try:
            await asyncio.to_thread(perform)
            await asyncio.sleep(self.cfg['tools']['settle_seconds'])
            return await self.shot(f'after-{time.time_ns()}.png')
        except Exception as error: return f'Input started; outcome uncertain: {error!r}. Take a new screenshot before repeating.'.encode()
    def stroke(self, points):
        grid, mouse = self.cfg['limits']['grid'], ctypes.windll.user32.mouse_event
        try:
            for index, (y, x) in enumerate(points):
                mouse(0xC001, round(x * 65535 / grid), round(y * 65535 / grid), 0, 0)
                if index == 0: mouse(2, 0, 0, 0, 0)
                time.sleep(self.cfg['tools']['input_pause'])
        finally: mouse(4, 0, 0, 0, 0)
    def bounds(self, image, box):
        values = [round(value * (size - 1) / self.cfg['limits']['grid']) for value, size in zip(box, image.size * 2)]
        return values[0], values[1], values[2] + 1, values[3] + 1
    def remap(self, item, crop):
        a, b, c, d = [round(low + value * (high - low) / self.cfg['limits']['grid']) for value, low, high in zip(item['bbox_2d'], crop[:2] * 2, crop[2:] * 2)]
        return item | dict(bbox_2d=[a, b, c, d], centre_y_x=[round((b + d) / 2), round((a + c) / 2)])
    async def look(self, image, question, boxes):
        vision = self.cfg['vision']
        path = self.file(f'vision-{time.time_ns()}.png')
        ImageOps.contain(image, (vision['image_side'], vision['image_side']), Image.Resampling.LANCZOS).save(path)
        content = [dict(type='image_url', image_url=dict(url='data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode())), dict(type='text', text=question)]
        reply = await self.server.chat([dict(role='system', content=vision[('text_system', 'boxes_system')[boxes]]), dict(role='user', content=content)], temperature=vision['temperature'])
        return path, reply
    async def share(self, text):
        if text != self.latest: raise Refused('UNKNOWN_DATA')
        await self.send('telegram', f'{text}\nRequested screenshot', '11', once=True)
        return b'Image submitted for delivery; external receipt unverified'
    async def vision(self, text):
        filename, _, question = text.partition('\n')
        if filename != self.latest: raise Refused('UNKNOWN_DATA')
        with Image.open(filename) as image:
            crop = list(map(int, image.info['crop'].split()))
            boxes = question.startswith('Provide the bounding box')
            _, raw = await self.look(image, question or self.cfg['vision']['overview'], boxes)
            if boxes: return json.dumps([self.remap(item, crop) for item in json.loads(raw)]).encode()
            return raw.encode()

Tools('tools').launch()
