import asyncio
import base64
import ctypes
import re
import subprocess
import time

import pyperclip
from PIL import Image, ImageGrab, ImageOps, PngImagePlugin

from i2c import Device, Nack, Refusal, Server

if not ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
    raise RuntimeError("Windows refused per-monitor DPI awareness")
import pyautogui
MOUSE_MOVE_ABSOLUTE_VIRTUAL_DESK, MOUSE_LEFT_DOWN, MOUSE_LEFT_UP = 0xC001, 0x0002, 0x0004
BOX = re.compile(r'"bbox_2d"\s*:\s*\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]')
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
                ImageOps.contain(image, (min(side := self.cfg["vision"]["image_side"], image.width), min(side, image.height)), Image.LANCZOS).save(sent := f"{path}-vision-{time.time_ns()}.png", "PNG")
                self.mirror(f"{sent}\n{source} -> {self.name} vision: {(question := question or self.cfg['vision']['prompt'].format(grid=grid))}")
                reply = await self.server.chat([{"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64.b64encode(open(sent, 'rb').read()).decode()}"}}, {"type": "text", "text": question}]}])
                self.mirror(f"\nvision {self.name} -> {source}: {reply}")
                return BOX.sub(lambda box: self.unzoom(box, x0, y0, x1, y1, grid), reply).encode()

    def mirror(self, text):
        asyncio.create_task(self.send("telegram", text, "11")).add_done_callback(lambda task: task.cancelled() or task.exception() is None or print(time.strftime("%X"), "Vision mirror failed:", repr(task.exception()), flush=True))

    @staticmethod
    def unzoom(box, x0, y0, x1, y1, grid):
        a, b, c, d = (round(low + int(value) * (high - low) / grid) for value, low, high in zip(box.groups(), (x0, y0) * 2, (x1, y1) * 2))
        return f'"bbox_2d": [{a}, {b}, {c}, {d}], "centre_y_x": [{round((b + d) / 2)}, {round((a + c) / 2)}]'

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
