import asyncio
import base64
import ctypes
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import i2c


def command(parts, root):
    argv = []
    for part in parts:
        text = os.path.expandvars(str(part))
        if text.startswith("artifacts/"):
            text = str(Path(root) / text)
        argv.append(text)
    return argv


def run_cmd(text):
    done = subprocess.run(text, shell=True, capture_output=True, text=True)
    body = (done.stdout or "") + (done.stderr or "")
    if done.returncode:
        return body + f"\nexit {done.returncode}"
    return body


def shot(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen; "
        "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height; "
        "$g=[System.Drawing.Graphics]::FromImage($bmp); "
        "$g.CopyFromScreen($b.X,$b.Y,0,0,$bmp.Size); "
        f"$bmp.Save('{path}',[System.Drawing.Imaging.ImageFormat]::Png)"
    )
    done = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True)
    if done.returncode or not path.is_file():
        raise RuntimeError((done.stderr or "shot failed").strip())
    return str(path)


def healthy(url, timeout):
    try:
        with urllib.request.urlopen(url + "/health", timeout=timeout) as response:
            return response.status == 200
    except urllib.error.HTTPError as error:
        if error.code == 503:
            return False
        raise
    except urllib.error.URLError:
        return False


def describe(url, path, temperature, tokens, timeout):
    raw = Path(path).read_bytes()
    kind = "png" if str(path).lower().endswith(".png") else "jpeg"
    content = [
        {"type": "text", "text": "Describe this image."},
        {"type": "image_url", "image_url": {"url": f"data:image/{kind};base64,{base64.b64encode(raw).decode()}"}},
    ]
    body = json.dumps({
        "model": "lfm", "temperature": temperature, "max_tokens": tokens,
        "messages": [{"role": "user", "content": content}],
    }).encode()
    request = urllib.request.Request(
        url + "/v1/chat/completions", data=body, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode())
    text = data["choices"][0]["message"]["content"]
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("Vision returned no answer")
    return text


_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _Mouse(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", _PTR),
    ]


class _Key(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong), ("dwExtraInfo", _PTR),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _Mouse), ("ki", _Key)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _InputUnion)]


_KEYS = {
    "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "space": 0x20, "backspace": 0x08,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "delete": 0x2E,
    "home": 0x24, "end": 0x23,
}


def parse_input(text, grid):
    grid = int(grid)
    try:
        if text.startswith("click "):
            parts = text.split()
            if len(parts) != 3:
                raise i2c.Nack()
            y, x = int(parts[1]), int(parts[2])
            if min(y, x) < 0 or max(y, x) > grid:
                raise i2c.Nack()
            return ("click", y, x)
        if text.startswith("type "):
            body = text[5:]
            if not body:
                raise i2c.Nack()
            return ("type", body)
        if text.startswith("key "):
            parts = text.split()
            if len(parts) != 2:
                raise i2c.Nack()
            return ("key", parts[1])
    except ValueError:
        raise i2c.Nack() from None
    raise i2c.Nack()


def grid_point(y, x, bounds, grid):
    left, top, width, height = bounds
    return (
        left + int(round(x * (width - 1) / grid)),
        top + int(round(y * (height - 1) / grid)),
    )


def screen_bounds():
    user = ctypes.windll.user32
    return (
        user.GetSystemMetrics(76), user.GetSystemMetrics(77),
        user.GetSystemMetrics(78), user.GetSystemMetrics(79),
    )


def _send(items):
    user = ctypes.windll.user32
    user.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(_Input), ctypes.c_int)
    user.SendInput.restype = ctypes.c_uint
    sent = user.SendInput(len(items), (_Input * len(items))(*items), ctypes.sizeof(_Input))
    if sent != len(items):
        raise RuntimeError("input was not sent")


def perform(action, bounds, grid):
    if action[0] == "click":
        px, py = grid_point(action[1], action[2], bounds, grid)
        left, top, width, height = bounds
        nx = int(round((px - left) * 65535 / (width - 1)))
        ny = int(round((py - top) * 65535 / (height - 1)))
        flags = 0x8001 | 0x4000
        move = _Input(0, _InputUnion(mi=_Mouse(nx, ny, 0, flags, 0, 0)))
        down = _Input(0, _InputUnion(mi=_Mouse(nx, ny, 0, flags | 0x0002, 0, 0)))
        up = _Input(0, _InputUnion(mi=_Mouse(nx, ny, 0, flags | 0x0004, 0, 0)))
        _send((move, down, up))
        return f"{px} {py}".encode()
    if action[0] == "type":
        items = []
        for char in action[1]:
            items.append(_Input(1, _InputUnion(ki=_Key(0, ord(char), 0x0004, 0, 0))))
            items.append(_Input(1, _InputUnion(ki=_Key(0, ord(char), 0x0006, 0, 0))))
        _send(items)
        return action[1].encode()
    vk = _KEYS.get(action[1])
    if vk is None and len(action[1]) == 1 and action[1].isascii() and action[1].isalnum():
        vk = ord(action[1].upper())
    if vk is None:
        raise i2c.Nack()
    _send((
        _Input(1, _InputUnion(ki=_Key(vk, 0, 0, 0, 0))),
        _Input(1, _InputUnion(ki=_Key(vk, 0, 0x0002, 0, 0))),
    ))
    return action[1].encode()


class Toolbox:
    def __init__(self, root, cfg, limits):
        self.root = Path(root)
        self.cfg = cfg
        self.limits = limits
        self.proc = None
        self.err = bytearray()
        self.reader = None

    async def drain(self):
        while True:
            keep = int(self.limits["stderr_keep"])
            block = await self.proc.stderr.read(keep)
            if not block:
                return
            self.err += block
            del self.err[:-keep]

    async def start(self, limit):
        parts = command(self.cfg["command"], self.root)
        if not Path(parts[0]).is_file():
            raise RuntimeError("Vision server is missing")
        self.proc = await asyncio.create_subprocess_exec(
            *parts, cwd=str(self.root),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        self.reader = asyncio.create_task(self.drain())
        started = asyncio.get_running_loop().time()
        while self.proc.returncode is None:
            if await asyncio.to_thread(healthy, self.cfg["url"], float(self.limits["health_timeout"])):
                return
            if asyncio.get_running_loop().time() - started > limit:
                self.proc.kill()
                await self.proc.wait()
                raise RuntimeError("Vision did not start")
            await asyncio.sleep(float(self.limits["health_poll"]))
        detail = self.err.decode("utf-8", "replace").strip().splitlines()
        tail = detail[-1] if detail else "no stderr"
        raise RuntimeError(f"Vision exited {self.proc.returncode}: {tail}")

    async def stop(self):
        if self.proc is not None and self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()
        if self.reader is not None:
            await self.reader

    def work(self, data):
        reg, body = data[0], data[1:]
        if reg == 1:
            return shot(body.decode()).encode()
        if reg == 2:
            return run_cmd(body.decode()).encode()
        if reg == 3:
            return describe(
                self.cfg["url"], body.decode(), float(self.cfg["temperature"]),
                int(self.cfg["max_tokens"]), float(self.limits["http_timeout"]),
            ).encode()
        if reg == 4:
            action = parse_input(body.decode(), self.limits["grid"])
            return perform(action, screen_bounds(), int(self.limits["grid"]))
        raise i2c.Nack()


def main():
    root, cfg = i2c.load()
    run = Path(sys.argv[1])
    bus = i2c.Bus(root, i2c.addr(cfg, "tools"), run, cfg)
    box = Toolbox(root, cfg["vision"], cfg["limits"])

    async def on_frame(_src, line):
        data = i2c.write_payload(line)
        if not data:
            raise i2c.Nack()
        with bus.stretch():
            result = await asyncio.to_thread(box.work, data)
        if " Sr " in line or line.split()[2] == "R":
            return i2c.with_payload(line, result)
        return i2c.pack_write(bus.addr, data)

    async def live():
        await box.start(float(cfg["busy"]["tools"]))
        try:
            await bus.run(on_frame)
        finally:
            await box.stop()

    i2c.entry(live)


def test():
    import tempfile

    text = run_cmd(f"\"{sys.executable}\" -c \"print(6)\"")
    assert text.strip() == "6"
    assert parse_input("click 10 20", 1000) == ("click", 10, 20)
    assert parse_input("type hi there", 1000) == ("type", "hi there")
    assert grid_point(0, 1000, (0, 0, 1001, 501), 1000) == (1000, 0)
    try:
        parse_input("nope", 1000)
        raise AssertionError("bad input")
    except i2c.Nack:
        pass
    root = Path(tempfile.mkdtemp())
    cfg = i2c.test_cfg()
    cfg["vision"] = {"url": "http://127.0.0.1:9", "command": ["missing"]}

    async def run():
        bus = i2c.Bus(root, 0x14, root / "RUN_test", cfg)
        box = Toolbox(root, cfg["vision"], cfg["limits"])
        master = i2c.Bus(root, 0x16, root / "RUN_test", cfg)
        bus.up()

        async def on_frame(_src, line):
            data = i2c.write_payload(line)
            with bus.stretch():
                result = box.work(data)
            return i2c.with_payload(line, result)

        stop = [False]
        task = asyncio.create_task(i2c._peer(bus, on_frame, stop))
        reply = await master.request(0x14, i2c.pack_call(0x14, bytes([2]) + b"echo tools-ok"))
        assert b"tools-ok" in i2c.read_payload(reply)
        try:
            await master.request(0x14, i2c.pack_call(0x14, bytes([4]) + b"nope"))
            raise AssertionError("bad input")
        except RuntimeError:
            pass
        assert (root / "wire" / "scl" / "14").exists() is False
        stop[0] = True
        await task
        import shutil
        shutil.rmtree(root, ignore_errors=True)

    asyncio.run(run())


if __name__ == "__main__":
    test() if "--test" in sys.argv else main()
