import asyncio
import base64
import ctypes
import mimetypes
import subprocess
import time
from pathlib import Path

import pyperclip
from PIL import ImageGrab

from i2c import Device, Nack, Refusal, Server

if not ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
    raise RuntimeError("Windows refused per-monitor DPI awareness")
import pyautogui


class Tools(Device):
    def __init__(self):
        super().__init__("tools")
        self.server = Server(self, self.cfg["llama"] | self.cfg["vision"])
        self.bounds = tuple(ctypes.windll.user32.GetSystemMetrics(index) for index in (76, 77, 78, 79))
        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = self.cfg["tools"]["input_pause"]

    async def receive(self, source, frame):
        register, text = frame.data[0], frame.data[1:].decode()
        match register:
            case 1 | 4:
                path = self.run / text if register == 1 else self.file(f"after-input-{time.time_ns()}.png")
                if register == 4:
                    await asyncio.to_thread(self.act, text)
                path.parent.mkdir(parents=True, exist_ok=True)
                image = await asyncio.to_thread(ImageGrab.grab, all_screens=True)
                await asyncio.to_thread(image.save, path)
                return str(path).encode()
            case 2:
                result = await asyncio.to_thread(subprocess.run, text, shell=True, cwd=self.run,
                                                capture_output=True, text=True,
                                                timeout=self.cfg["tools"]["command_seconds"],
                                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
                return f"exit {result.returncode}\n{result.stdout}{result.stderr}".encode()
            case 3:
                path = Path(text)
                image = base64.b64encode(path.read_bytes()).decode()
                content = [{"type": "text", "text": self.cfg["vision"]["prompt"].format(grid=self.cfg["limits"]["grid"])},
                           {"type": "image_url", "image_url": {"url": f"data:{mimetypes.guess_type(path)[0]};base64,{image}"}}]
                return (await self.server.chat([{"role": "user", "content": content}])).encode()

    def act(self, text):
        operation, separator, argument = text.partition(" ")
        if not separator:
            raise Nack(Refusal.UNKNOWN_DATA)
        match operation:
            case "click":
                try:
                    y, x = map(int, argument.split())
                except ValueError:
                    raise Nack(Refusal.UNKNOWN_DATA) from None
                grid = self.cfg["limits"]["grid"]
                if not (0 <= x <= grid and 0 <= y <= grid):
                    raise Nack(Refusal.UNKNOWN_DATA)
                left, top, width, height = self.bounds
                pyautogui.click(left + round(x * (width - 1) / grid), top + round(y * (height - 1) / grid))
            case "type":
                pyperclip.copy(argument)
                pyautogui.hotkey("ctrl", "v")
            case "key":
                if argument not in pyautogui.KEYBOARD_KEYS:
                    raise Nack(Refusal.UNKNOWN_DATA)
                pyautogui.press(argument)
            case _:
                raise Nack(Refusal.UNKNOWN_DATA)


if __name__ == "__main__":
    Tools().launch()
