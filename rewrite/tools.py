import asyncio
import base64
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


def healthy(url):
    try:
        with urllib.request.urlopen(url + "/health", timeout=2) as response:
            return response.status == 200
    except urllib.error.HTTPError as error:
        if error.code == 503:
            return False
        raise
    except urllib.error.URLError:
        return False


def describe(url, path):
    raw = Path(path).read_bytes()
    kind = "png" if str(path).lower().endswith(".png") else "jpeg"
    content = [
        {"type": "text", "text": "Describe this image."},
        {"type": "image_url", "image_url": {"url": f"data:image/{kind};base64,{base64.b64encode(raw).decode()}"}},
    ]
    body = json.dumps({
        "model": "lfm", "temperature": 0.2, "max_tokens": 400,
        "messages": [{"role": "user", "content": content}],
    }).encode()
    request = urllib.request.Request(
        url + "/v1/chat/completions", data=body, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        data = json.loads(response.read().decode())
    text = data["choices"][0]["message"]["content"]
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("Vision returned no answer")
    return text


class Toolbox:
    def __init__(self, root, cfg):
        self.root = Path(root)
        self.cfg = cfg
        self.proc = None
        self.err = bytearray()
        self.reader = None

    async def drain(self):
        while True:
            block = await self.proc.stderr.read(4096)
            if not block:
                return
            self.err += block
            del self.err[:-4000]

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
            if await asyncio.to_thread(healthy, self.cfg["url"]):
                return
            if asyncio.get_running_loop().time() - started > limit:
                self.proc.kill()
                await self.proc.wait()
                raise RuntimeError("Vision did not start")
            await asyncio.sleep(0.2)
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
            return describe(self.cfg["url"], body.decode()).encode()
        raise i2c.Nack()


def main():
    root, cfg = i2c.load()
    run = Path(sys.argv[1])
    bus = i2c.Bus(root, i2c.addr(cfg, "tools"), run, cfg)
    box = Toolbox(root, cfg["vision"])

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
    root = Path(tempfile.mkdtemp())
    cfg = i2c.test_cfg()
    cfg["vision"] = {"url": "http://127.0.0.1:9", "command": ["missing"]}

    async def run():
        bus = i2c.Bus(root, 0x14, root / "RUN_test", cfg)
        box = Toolbox(root, cfg["vision"])
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
        assert (root / "wire" / "scl" / "14").exists() is False
        stop[0] = True
        await task
        import shutil
        shutil.rmtree(root, ignore_errors=True)

    asyncio.run(run())


if __name__ == "__main__":
    test() if "--test" in sys.argv else main()
