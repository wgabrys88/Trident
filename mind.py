import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path

import i2c

IDENT = 0xF0
CHAT = 0x10


def part_table(cfg):
    name = sys.argv[2] if len(sys.argv) > 2 else cfg["mind"]["default"]
    table = cfg["mind"].get(name)
    if not isinstance(table, dict):
        raise RuntimeError("mind part is missing")
    return table


class Mind:
    def __init__(self, bus, cfg, run, root):
        self.bus = bus
        self.cfg = cfg
        self.run = Path(run)
        self.root = Path(root)
        self.table = part_table(cfg)
        self.part = self.table["part"]
        self.transport = self.table["transport"]
        self.identity = ";".join((
            self.table["manufacturer"], self.table["part"], self.table["revision"], self.table["capabilities"],
        ))
        self.tg = i2c.addr(cfg, "telegram")
        self.owners = {i2c.addr(cfg, "telegram"), i2c.addr(cfg, "ears")}
        self.cap = int(cfg["bus"]["self_turn_cap"])
        self.limit = float(self.table["turn"])
        self.context = int(self.table["context_chars"])
        self.busy = False
        self.self_turns = 0
        self.transcript = ""
        self.queued = []
        self.count = 0
        self.proc = None

    def argv(self):
        raw = []
        for part in self.table["command"]:
            text = os.path.expandvars(str(part))
            text = text.replace("{model}", self.table["model"]).replace("{tools}", self.table["tools"]).replace("{run}", str(self.run))
            raw.append(text)
        folder = Path(raw[0])
        versions = [path for path in folder.iterdir() if (path / raw[1]).is_file() and (path / raw[2]).is_file()]
        if not versions:
            raise RuntimeError("mind command is missing")
        latest = max(versions, key=lambda path: path.name)
        return [str(latest / raw[1]), str(latest / raw[2]), *raw[3:]]

    def stdin_text(self, src, incoming):
        brief = (self.root / "prompt.txt").read_text(encoding="utf-8")
        brief = brief.replace("{grid}", str(self.cfg["limits"]["grid"]))
        listing = "\n".join(f"{int(value, 16):02x} {name}" for name, value in self.cfg["address"].items())
        return brief + "\n" + listing + "\n" + self.transcript + "\n" + f"{src:02x}\n" + incoming

    def keep(self, text, decoded):
        self.transcript += f"In:\n{text}\nOut:\n{decoded}\n"
        while len(self.transcript) > self.context:
            cut = self.transcript.find("\nIn:\n", 1)
            if cut < 0:
                self.transcript = self.transcript[-self.context:]
                return
            self.transcript = self.transcript[cut + 1:]

    def record(self, text, stdin, decoded):
        self.count += 1
        (self.run / f"turn-{self.count}.txt").write_text(stdin + "\n---\n" + decoded, encoding="utf-8")
        self.keep(text, decoded)

    async def on_frame(self, src, line):
        data = i2c.write_payload(line)
        if data == bytes([IDENT]) and " Sr " in line:
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
        self.record(text, stdin, decoded)
        return decoded

    def http(self, src, incoming):
        text = incoming.decode()
        stdin = self.stdin_text(src, text)
        body = json.dumps({
            "model": self.table["model"],
            "messages": [{"role": "user", "content": stdin}],
        }).encode()
        request = urllib.request.Request(
            self.table["url"].rstrip("/") + "/v1/chat/completions",
            data=body, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.limit) as response:
            payload = json.loads(response.read().decode())
        decoded = payload["choices"][0]["message"]["content"] or ""
        self.record(text, stdin, decoded)
        return decoded

    async def act(self, out):
        prose = []

        async def flush():
            body = "\n".join(prose).strip()
            prose.clear()
            if not body:
                return
            frame = i2c.pack_write(self.tg, bytes([CHAT]) + body.encode())
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
                if self.transport == "http":
                    out = await asyncio.to_thread(self.http, src, data)
                else:
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
    if mind.transport == "stdio":
        mind.argv()

    async def around(serve):
        if mind.transport == "http":
            await i2c.wait_healthy(mind.table["url"], cfg["limits"], float(cfg["start"]["tools"]), "mind server is missing")
        await serve

    return mind.on_frame, None, around


def main():
    i2c.main_for("mind", build)


if __name__ == "__main__":
    main()
