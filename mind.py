import asyncio
import os
import sys
from pathlib import Path

import i2c


class Mind:
    def __init__(self, bus, cfg, run, root, command=None):
        self.bus = bus
        self.cfg = cfg
        self.run = Path(run)
        self.root = Path(root)
        self.command = command
        mind = cfg["mind"]
        self.part = mind["part"]
        self.identity = ";".join((mind["manufacturer"], mind["part"], mind["revision"], mind["capabilities"]))
        self.tg = i2c.addr(cfg, "telegram")
        self.owners = {i2c.addr(cfg, "telegram"), i2c.addr(cfg, "ears")}
        self.cap = int(cfg["bus"]["self_turn_cap"])
        self.limit = float(mind["turn"])
        self.context = int(mind["context_chars"])
        self.busy = False
        self.self_turns = 0
        self.transcript = ""
        self.queued = []
        self.count = 0
        self.proc = None

    def argv(self):
        if self.command is not None:
            return list(self.command)
        mind = self.cfg["mind"]
        raw = []
        for part in mind["command"]:
            text = os.path.expandvars(str(part))
            text = text.replace("{model}", mind["model"]).replace("{tools}", mind["tools"]).replace("{run}", str(self.run))
            raw.append(text)
        folder = Path(raw[0])
        versions = [path for path in folder.iterdir() if (path / raw[1]).is_file() and (path / raw[2]).is_file()]
        if not versions:
            raise RuntimeError("mind command is missing")
        latest = max(versions, key=lambda path: path.name)
        return [str(latest / raw[1]), str(latest / raw[2]), *raw[3:]]

    def stdin_text(self, src, incoming):
        prompt = (self.root / "prompt.txt").read_text(encoding="utf-8")
        listing = "\n".join(f"{int(value, 16):02x} {name}" for name, value in self.cfg["address"].items())
        return prompt + "\n" + listing + "\n" + self.transcript + "\n" + f"{src:02x}\n" + incoming

    def keep(self, text, decoded):
        self.transcript += f"In:\n{text}\nOut:\n{decoded}\n"
        while len(self.transcript) > self.context:
            cut = self.transcript.find("\nIn:\n", 1)
            if cut < 0:
                self.transcript = self.transcript[-self.context:]
                return
            self.transcript = self.transcript[cut + 1:]

    async def on_frame(self, src, line):
        data = i2c.write_payload(line)
        if data == b"\xf0" and " Sr " in line:
            return i2c.with_payload(line, self.identity.encode())
        if line.split()[2] == "R" and not data:
            return i2c.reply(line, b"", bytes([1 if self.busy else 0]))
        if self.busy:
            return i2c.nack(self.bus.addr)
        if src == self.bus.addr:
            self.self_turns += 1
            if self.self_turns > self.cap:
                return i2c.pack_write(self.bus.addr, data)
        elif src in self.owners:
            self.self_turns = 0
        self.busy = True
        asyncio.create_task(self.turn(src, data))
        return i2c.pack_write(self.bus.addr, data)

    async def cli(self, src, incoming):
        text = incoming.decode()
        stdin = self.stdin_text(src, text)
        proc = await asyncio.create_subprocess_exec(
            *self.argv(), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(self.run),
        )
        self.proc = proc
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin.encode()), self.limit)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        finally:
            self.proc = None
        if proc.returncode:
            tail = err.decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError(tail[-1] if tail else f"exit {proc.returncode}")
        decoded = out.decode()
        self.count += 1
        (self.run / f"turn-{self.count}.txt").write_text(stdin + "\n---\n" + decoded, encoding="utf-8")
        self.keep(text, decoded)
        return decoded

    async def act(self, out):
        prose = []

        async def flush():
            body = "\n".join(prose).strip()
            prose.clear()
            if not body:
                return
            frame = i2c.pack_write(self.tg, bytes([0x10]) + body.encode())
            try:
                await self.bus.request(self.tg, frame)
            except i2c.BusOff:
                raise
            except Exception as error:
                self.queued.append(i2c.pack_write(self.bus.addr, str(error).encode()))

        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith("S "):
                await flush()
                if not i2c.legal(stripped):
                    self.queued.append(i2c.pack_write(self.bus.addr, f"illegal frame: {stripped}".encode()))
                    continue
                target = int(stripped.split()[1], 16)
                if target == self.bus.addr and stripped.split()[2] == "W":
                    self.queued.append(stripped)
                    continue
                try:
                    answered = await self.bus.request(target, stripped)
                except i2c.BusOff:
                    raise
                except Exception as error:
                    self.queued.append(i2c.pack_write(self.bus.addr, str(error).encode()))
                    continue
                if " Sr " in stripped:
                    payload = i2c.read_payload(answered)
                    if payload:
                        self.queued.append(i2c.pack_write(self.bus.addr, payload))
            else:
                prose.append(line)
        await flush()

    async def turn(self, src, data):
        try:
            try:
                out = await self.cli(src, data)
            except TimeoutError:
                out = f"{self.part} timed out."
            except Exception as error:
                out = f"{self.part} stopped: {error}"
            await self.act(out)
        except i2c.BusOff:
            return
        finally:
            queued = self.queued
            self.queued = []
            self.busy = False
            if not self.bus.dead:
                for line in queued:
                    self.bus.post(line)


def build(bus, cfg, root, run):
    mind = Mind(bus, cfg, run, root)
    mind.argv()
    return mind.on_frame, None, None


def main():
    i2c.main_for("mind", build)


def test():
    import tempfile

    standin = r"""
import sys, time
from pathlib import Path
log, wire, kind = sys.argv[1:]
text = sys.stdin.read()
path = Path(log)
prev = path.read_text(encoding="utf-8") if path.exists() else ""
n = prev.count("BEGIN") + 1
scl = Path(wire) / "scl" / "16"
flag = "SCL" if scl.exists() else "FREE"
path.write_text(prev + f"BEGIN {n} {flag}\n" + text + "\nEND\n", encoding="utf-8")
if kind == "slow":
    time.sleep(0.6)
    sys.stdout.write("hello\n")
elif kind == "self":
    sys.stdout.write("ping\nS 16 W A 68 A 69 A P\n")
elif kind == "bad":
    sys.stdout.write("S 10 W A 03 A P\nkept\n" if n == 1 else "done\n")
elif kind == "ill":
    sys.stdout.write("S 11 W A 01\nkept\n" if n == 1 else "done\n")
else:
    time.sleep(2)
    sys.stdout.write("late\n")
"""
    cfg = i2c.load()[1]
    grid = str(cfg["limits"]["grid"])
    assert f"0 to {grid}" in (Path(__file__).resolve().parent / "prompt.txt").read_text(encoding="utf-8")
    probe = Mind(None, cfg, Path("."), Path("."), ["stand-in"])
    probe.context = 30
    probe.keep("one", "abcdefghij" * 8)
    assert len(probe.transcript) <= 30

    seen = []

    async def phone(_src, line):
        seen.append(i2c.write_payload(line))
        return i2c.pack_write(0x11, i2c.write_payload(line))

    async def run():
        root = Path(tempfile.mkdtemp())
        run_dir = root / "RUN_test"
        run_dir.mkdir()
        script = root / "standin.py"
        script.write_text(standin, encoding="utf-8")
        log = root / "log.txt"
        wire = root / "wire"
        cfg = i2c.test_cfg()
        phone_bus = i2c.Bus(root, 0x11, run_dir, cfg)
        phone_bus.up()
        stop = [False]
        phones = asyncio.create_task(i2c._peer(phone_bus, phone, stop))
        here = Path(__file__).resolve().parent
        part = cfg["mind"]["part"].encode()

        async def boot(command, this_cfg):
            bus = i2c.Bus(root, 0x16, run_dir, this_cfg)
            mind = Mind(bus, this_cfg, run_dir, here, command)
            task = asyncio.create_task(bus.run(mind.on_frame))
            for _ in range(50):
                if bus.present(0x16):
                    break
                await asyncio.sleep(0.02)
            return bus, mind, task

        slow = [sys.executable, str(script), str(log), str(wire), "slow"]
        bus, mind, task = await boot(slow, cfg)
        master = i2c.Bus(root, 0x11, run_dir, cfg)
        master.seq = 400
        assert mind.owners == {0x11, 0x12}
        ident = await master.request(0x16, i2c.pack_call(0x16, b"\xf0"))
        assert i2c.read_payload(ident).decode() == mind.identity
        await master.request(0x16, i2c.pack_write(0x16, b"ping"))
        for _ in range(50):
            if log.exists() and "BEGIN" in log.read_text(encoding="utf-8"):
                break
            await asyncio.sleep(0.02)
        nack = await master.transfer(0x16, i2c.pack_write(0x16, b"more"))
        assert nack.split()[3] == "NA"
        assert "FREE" in log.read_text(encoding="utf-8")
        assert not (wire / "scl" / "16").exists()
        for _ in range(80):
            if any(item.startswith(b"\x10hello") for item in seen):
                break
            await asyncio.sleep(0.02)
        assert any(item.startswith(b"\x10hello") for item in seen)
        turn = (run_dir / "turn-1.txt").read_text(encoding="utf-8")
        assert "\n11\nping\n" in turn
        assert "10 timer" in turn
        assert f"0 to {grid}" in turn
        assert " ack " in (run_dir / "bus.log").read_text(encoding="utf-8")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        cfg["bus"]["self_turn_cap"] = 2
        seen.clear()
        log.write_text("", encoding="utf-8")
        chain = [sys.executable, str(script), str(log), str(wire), "self"]
        bus, mind, task = await boot(chain, cfg)
        master = i2c.Bus(root, 0x11, run_dir, cfg)
        master.seq = 800
        await master.request(0x16, i2c.pack_write(0x16, b"go"))
        for _ in range(100):
            if log.exists() and log.read_text(encoding="utf-8").count("BEGIN") >= 3:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.3)
        assert log.read_text(encoding="utf-8").count("BEGIN") == 3
        assert sum(1 for item in seen if item.startswith(b"\x10ping")) == 3
        other = i2c.Bus(root, 0x13, run_dir, cfg)
        other.seq = 900
        await other.request(0x16, i2c.pack_write(0x16, b"side"))
        await asyncio.sleep(0.3)
        assert log.read_text(encoding="utf-8").count("BEGIN") == 4
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        cfg["bus"]["self_turn_cap"] = 4
        seen.clear()
        log.write_text("", encoding="utf-8")
        bad = [sys.executable, str(script), str(log), str(wire), "bad"]
        bus, mind, task = await boot(bad, cfg)
        master = i2c.Bus(root, 0x11, run_dir, cfg)
        master.seq = 1000
        await master.request(0x16, i2c.pack_write(0x16, b"go"))
        for _ in range(80):
            if any(item.startswith(b"\x10done") for item in seen):
                break
            await asyncio.sleep(0.02)
        assert any(item.startswith(b"\x10kept") for item in seen)
        assert any(item.startswith(b"\x10done") for item in seen)
        assert not any(item.startswith(b"\x10" + part + b" stopped") for item in seen)
        assert "NACK 10" in (run_dir / "turn-2.txt").read_text(encoding="utf-8")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        seen.clear()
        log.write_text("", encoding="utf-8")
        ill = [sys.executable, str(script), str(log), str(wire), "ill"]
        bus, mind, task = await boot(ill, cfg)
        master = i2c.Bus(root, 0x11, run_dir, cfg)
        master.seq = 1100
        await master.request(0x16, i2c.pack_write(0x16, b"go"))
        for _ in range(80):
            if any(item.startswith(b"\x10done") for item in seen):
                break
            await asyncio.sleep(0.02)
        assert any(item.startswith(b"\x10kept") for item in seen)
        assert not any(b"S 11 W A 01" in item for item in seen)
        assert "illegal frame:" in log.read_text(encoding="utf-8")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        cfg["mind"]["turn"] = 0.3
        seen.clear()
        hung = [sys.executable, str(script), str(log), str(wire), "hang"]
        bus, mind, task = await boot(hung, cfg)
        master = i2c.Bus(root, 0x11, run_dir, cfg)
        master.seq = 1200
        await master.request(0x16, i2c.pack_write(0x16, b"wait"))
        expect = b"\x10" + part + b" timed out."
        for _ in range(80):
            if any(item.startswith(expect) for item in seen):
                break
            await asyncio.sleep(0.05)
        assert any(item.startswith(expect) for item in seen)
        assert bus.tec == 0 and bus.rec == 0
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        stop[0] = True
        await phones
        import shutil
        shutil.rmtree(root, ignore_errors=True)

    asyncio.run(run())


if __name__ == "__main__":
    test() if "--test" in sys.argv else main()
