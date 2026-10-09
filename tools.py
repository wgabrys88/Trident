import asyncio, base64, ctypes, re, subprocess, time

import pyperclip
from PIL import Image, ImageGrab, ImageOps, PngImagePlugin

from i2c import Device, Nack, Refusal, Server

if not ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
    raise RuntimeError("Windows refused per-monitor DPI awareness")
import pyautogui
MOUSE_MOVE_ABSOLUTE_VIRTUAL_DESK, MOUSE_LEFT_DOWN, MOUSE_LEFT_UP = 0xC001, 0x0002, 0x0004
BOX = re.compile(r'("bbox_2d"\s*:\s*|image_index=\d+ [^\[\n]*)\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]')
class Tools(Device):
    def __init__(self):
        super().__init__("tools")
        self.server = Server(self, self.cfg["llama"] | self.cfg["vision"])
        pyautogui.FAILSAFE, pyautogui.PAUSE = False, self.cfg["tools"]["input_pause"]

    async def receive(self, source, frame):
        register, text, grid = frame.data[0], frame.data[1:].decode(), self.cfg["limits"]["grid"]
        match register:
            case 1 | 4:
                name, *region = text.split() if register == 1 else (f"{self.name}-after-input-{time.time_ns()}.png",)
                perform = self.act(text, grid) if register == 4 else lambda: None
                try:
                    await asyncio.to_thread(perform)
                    await asyncio.sleep(self.cfg["tools"]["settle_ms"] / 1000 * (register == 4))
                    x0, y0, x1, y1 = map(int, region or (0, 0, grid, grid))
                    if not 0 <= x0 < x1 <= grid >= y1 > y0 >= 0:
                        raise Nack(Refusal.UNKNOWN_DATA)
                    (path := self.run / name).parent.mkdir(parents=True, exist_ok=True)
                    image = await asyncio.to_thread(ImageGrab.grab, all_screens=True)
                    (info := PngImagePlugin.PngInfo()).add_text("crop", f"{x0} {y0} {x1} {y1}")
                    left, top, right, bottom = (round(value * (size - 1) / grid) for value, size in zip((x0, y0, x1, y1), image.size * 2))
                    await asyncio.to_thread(image.crop((left, top, right + 1, bottom + 1)).save, path, pnginfo=info)
                    return str(path).encode()
                except (Exception if register == 4 else ()) as error:
                    return f"Input was sent but then failed ({error!r}); look at the screen before repeating it.".encode()
            case 2:
                try:
                    result = await asyncio.to_thread(subprocess.run, text, shell=True, cwd=self.run, capture_output=True, text=True, timeout=self.cfg["tools"]["command_seconds"], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
                    reply = f"exit {result.returncode}\n{result.stdout}{result.stderr}"
                except Exception as error:
                    reply = f"failed to run: {error}\n{getattr(error, 'stdout', None) or ''}{getattr(error, 'stderr', None) or ''}"
                await asyncio.sleep(self.cfg["tools"]["start_settle_ms"] / 1000 * reply.startswith("exit 0\n"))
                return reply.encode()
            case 3:
                path, _, question = text.partition("\n")
                x0, y0, x1, y1 = map(int, (image := Image.open(path)).info.get("crop", f"0 0 {grid} {grid}").split())
                sent, reply = await self.look(image, path, question or (vision := self.cfg["vision"])["areas"], question and source)
                if question:
                    return BOX.sub(lambda box: self.unzoom(box, x0, y0, x1, y1, grid), reply).encode()
                areas = [(re.sub(r'image_index=\d+ |"bbox_2d"\s*:\s*', "", box[1]).strip() or (re.findall(r'"label"\s*:\s*"([^"]*)"', reply[reply.rfind("{", 0, box.start()) + 1:box.end() + (reply[box.end():] + "}").find("}")]) or ["area"])[0], [int(value) for value in box.groups()[1:]], [int(value) for value in re.findall(r"\d+", self.unzoom(box, x0, y0, x1, y1, grid)[len(box[1]):])]) for box in BOX.finditer(reply) if int(box[2]) < int(box[4]) and int(box[3]) < int(box[5])][:vision["max_areas"]]
                rendered = []
                for label, (a, b, c, d), (e, f, g, h, cy, cx) in areas:
                    crop, raw = await self.look(image.crop((round(a * (image.width - 1) / grid), round(b * (image.height - 1) / grid), round(c * (image.width - 1) / grid) + 1, round(d * (image.height - 1) / grid) + 1)), path, vision["read"], None)
                    covers = next((f", covers {other}" for other, _, (p, q, r, s, *_) in areas if (r - p) * (s - q) > (g - e) * (h - f) and max(0, min(g, r) - max(e, p)) * max(0, min(h, s) - max(f, q)) >= 0.8 * (g - e) * (h - f)), "")
                    rendered.append((f"{label}, {('top', 'middle', 'bottom')[min(2, cy * 3 // grid)]} {('left', 'centre', 'right')[min(2, cx * 3 // grid)]}{covers}, shows: {' '.join(raw.split())[:80].rstrip()} bbox_2d [{e}, {f}, {g}, {h}] centre_y_x [{cy}, {cx}]", f"{crop}\n{vision['read']} / {raw}"))
                self.mirror("\x1e".join(([f"{sent}\n{(text := chr(10).join(line for line, _ in rendered) or 'no areas found')}"] + [item for _, item in rendered])[:10]))
                return text.encode()

    async def look(self, image, path, question, source):
        ImageOps.contain(image, (min(side := self.cfg["vision"]["image_side"], image.width), min(side, image.height)), Image.LANCZOS).save(sent := f"{path}-vision-{time.time_ns()}.png", "PNG")
        source and self.mirror(f"{sent}\n{source} -> {self.name} vision: {question}")
        reply = await self.server.chat([{"role": "system", "content": self.cfg["vision"]["system"]}, {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64.b64encode(open(sent, 'rb').read()).decode()}"}}, {"type": "text", "text": question}]}], temperature=self.cfg["vision"]["temperature"])
        source and self.mirror(f"\nvision {self.name} -> {source}: {reply}")
        return sent, reply

    def mirror(self, text):
        asyncio.create_task(self.send("telegram", text, "11")).add_done_callback(lambda task: task.cancelled() or task.exception() is None or print(time.strftime("%X"), "Vision mirror failed:", repr(task.exception()), flush=True))

    @staticmethod
    def unzoom(box, x0, y0, x1, y1, grid):
        a, b, c, d = (round(low + int(value) * (high - low) / grid) for value, low, high in zip(box.groups()[1:], (x0, y0) * 2, (x1, y1) * 2))
        return f'{box[1]}[{a}, {b}, {c}, {d}], "centre_y_x": [{round((b + d) / 2)}, {round((a + c) / 2)}]'

    def act(self, text, grid):
        operation, separator, argument = text.partition(" ")
        match operation if separator else None:
            case "click" | "draw":
                coordinates = tuple(map(int, argument.split()))
                points = tuple(zip(coordinates[::2], coordinates[1::2], strict=True))
                if (len(points) != 1 if operation == "click" else len(points) < 2) or not all(0 <= value <= grid for value in coordinates):
                    raise Nack(Refusal.UNKNOWN_DATA)
                events = [(MOUSE_MOVE_ABSOLUTE_VIRTUAL_DESK, round(x * 65535 / grid), round(y * 65535 / grid)) for y, x in points]
                events.insert(1, (MOUSE_LEFT_DOWN, 0, 0))
                return lambda: self.stroke(events)
            case "type":
                return lambda: (pyperclip.copy(argument), pyautogui.hotkey("ctrl", "v"))
            case "key":
                if argument not in pyautogui.KEYBOARD_KEYS:
                    raise Nack(Refusal.UNKNOWN_DATA)
                return lambda: pyautogui.press(argument)
            case _:
                raise Nack(Refusal.UNKNOWN_DATA)

    def stroke(self, events):
        try:
            for flags, x, y in events:
                ctypes.windll.user32.mouse_event(flags, x, y, 0, 0)
                time.sleep(self.cfg["tools"]["input_pause"])
        finally:
            ctypes.windll.user32.mouse_event(MOUSE_LEFT_UP, 0, 0, 0, 0)

if __name__ == "__main__":
    Tools().launch()
