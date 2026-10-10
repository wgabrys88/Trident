import asyncio, base64, ctypes, json, re, time
import pyperclip
from PIL import Image, ImageGrab, ImageOps, PngImagePlugin
from bus import Device, Refused, Server

def boxes_requested(question):
    folded = question.casefold()
    legacy = question.startswith('Provide the bounding box')
    selected = any(phrase in folded for phrase in ('bounding box', 'bbox', 'bounds'))
    # #region agent log
    try:
        import json, time
        from pathlib import Path
        payload = {"sessionId": "f678ae", "hypothesisId": "A", "runId": "post-fix", "location": "tools.py:boxes_requested", "message": "bbox classifier", "data": {"legacy_prefix": legacy, "selected": selected, "yes_no_ending": question.casefold().rstrip().endswith("answer yes or no")}, "timestamp": int(time.time() * 1000)}
        with Path(__file__).with_name("debug-f678ae.log").open("a", encoding="utf-8") as stream: stream.write(json.dumps(payload) + "\n")
    except Exception: pass
    # #endregion
    return selected

def box_question(question):
    text = re.sub(r'\s*answer yes or no\s*$', '', question, flags=re.IGNORECASE)
    return re.sub(r',?\s*or answer no\b.*$', '', text, flags=re.IGNORECASE).strip()

def parse_boxes(raw):
    text = raw.strip()
    fenced = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', text, re.DOTALL | re.IGNORECASE)
    if fenced: text = fenced.group(1).strip()
    try: value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find('['), text.rfind(']')
        if start < 0 or end <= start: raise
        value = json.loads(text[start:end + 1])
    if not isinstance(value, list) or any(not isinstance(item, dict) or not isinstance(item.get('bbox_2d'), list) or len(item['bbox_2d']) != 4 for item in value):
        raise ValueError('Bounding box array required')
    return value

def newest_argument(filename, latest):
    # #region agent log
    try:
        import json, time
        from pathlib import Path
        payload = {"sessionId": "f678ae", "hypothesisId": "B", "runId": "post-fix", "location": "tools.py:newest_argument", "message": "vision path check", "data": {"matches_latest": filename == latest, "looks_like_png": filename.lower().endswith(".png")}, "timestamp": int(time.time() * 1000)}
        with Path(__file__).with_name("debug-f678ae.log").open("a", encoding="utf-8") as stream: stream.write(json.dumps(payload) + "\n")
    except Exception: pass
    # #endregion
    return filename == latest

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
        handle = self.file(f'command-{time.time_ns()}.log').open('w+b')
        process = await asyncio.create_subprocess_shell(text, cwd=self.run, stdin=asyncio.subprocess.DEVNULL, stdout=handle, stderr=asyncio.subprocess.STDOUT)
        # #region agent log
        started = time.time()
        def _dbg(message, data):
            try:
                from pathlib import Path
                payload = {"sessionId": "f678ae", "hypothesisId": "H1", "runId": "post-fix", "location": "tools.py:command", "message": message, "data": data, "timestamp": int(time.time() * 1000)}
                with Path(__file__).with_name("debug-f678ae.log").open("a", encoding="utf-8") as stream: stream.write(json.dumps(payload) + "\n")
            except Exception: pass
        _dbg('command start', {'pid': process.pid, 'chars': len(text), 'head': text[:80]})
        # #endregion
        try:
            await asyncio.wait_for(process.wait(), self.cfg['tools']['command_seconds'])
            if process.returncode == 0: await asyncio.sleep(self.cfg['tools']['launch_seconds'])
            handle.seek(0)
            output = handle.read()
            # #region agent log
            _dbg('command returned', {'pid': process.pid, 'code': process.returncode, 'elapsed': round(time.time() - started, 1), 'out': len(output)})
            # #endregion
            return f'exit {process.returncode}\n'.encode() + output
        except Exception as error: return f'Command started; outcome uncertain: {error!r}. Observe before repeating.'.encode()
        finally:
            if process.returncode is None: process.kill(); await process.wait()
            handle.close()
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
        if not newest_argument(filename, self.latest): raise Refused('UNKNOWN_DATA')
        with Image.open(filename) as image:
            crop = list(map(int, image.info['crop'].split()))
            boxes = boxes_requested(question)
            _, raw = await self.look(image, (box_question(question) if boxes else question) or self.cfg['vision']['overview'], boxes)
            if not boxes: return raw.encode()
            try: return json.dumps([self.remap(item, crop) for item in parse_boxes(raw)]).encode()
            except (json.JSONDecodeError, ValueError, TypeError, KeyError): raise Refused('UNKNOWN_DATA') from None

if __name__ == '__main__': Tools('tools').launch()
