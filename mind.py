import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path

import i2c

IDENT = 0xF0
CHAT = 0x10
STATUS = 0x00


def fits(value, schema):
    if "anyOf" in schema:
        return any(fits(value, item) for item in schema["anyOf"])
    if "const" in schema:
        return value == schema["const"]
    if "enum" in schema and schema.get("type") != "string":
        return value in schema["enum"]
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            return False
        props = schema["properties"]
        if schema.get("additionalProperties") is False and any(key not in props for key in value):
            return False
        if any(key not in value for key in schema.get("required", ())):
            return False
        return all(fits(value[key], props[key]) for key in value if key in props)
    if kind == "string":
        if not isinstance(value, str):
            return False
        if "enum" in schema and value not in schema["enum"]:
            return False
        if "minLength" in schema and len(value) < schema["minLength"]:
            return False
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return False
        return True
    return False


def bind_schema(raw, cfg):
    text_max = int(cfg["limits"]["text_max"])
    bound = []
    for branch in raw["anyOf"]:
        action = branch["properties"]["action"]["const"]
        if action in ("write", "read"):
            for name, regs in cfg["map"].items():
                item = json.loads(json.dumps(branch))
                item["properties"]["address"] = {"const": f"{int(cfg['address'][name], 16):02x}"}
                item["properties"]["register"] = {"enum": list(regs)}
                item["properties"]["text"]["maxLength"] = text_max
                bound.append(item)
            continue
        item = json.loads(json.dumps(branch))
        text = item["properties"].get("text")
        if text is not None:
            text["maxLength"] = text_max
        bound.append(item)
    return {"anyOf": bound}


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
        self.schema = bind_schema(json.loads((self.root / "action.json").read_text(encoding="utf-8")), cfg)
        self.part = self.table["part"]
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
        self.outbox = []
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

    def listing(self):
        lines = []
        for name, value in self.cfg["address"].items():
            regs = ", ".join(f"{reg} {meaning}" for reg, meaning in self.cfg["map"][name].items())
            lines.append(f"{int(value, 16):02x} {name}. {regs}")
        return "\n".join(lines)

    def stdin_text(self, src, incoming):
        brief = (self.root / "prompt.txt").read_text(encoding="utf-8")
        brief = brief.replace("{grid}", str(self.cfg["limits"]["grid"]))
        return brief + "\n" + self.listing() + "\n" + self.transcript + "\n" + f"{src:02x}\n" + incoming

    def keep(self, text, decoded):
        self.transcript += f"In:\n{text}\nOut:\n{decoded}\n"
        while len(self.transcript) > self.context:
            cut = self.transcript.find("\nIn:\n", 1)
            if cut < 0:
                self.transcript = self.transcript[-self.context:]
                return
            self.transcript = self.transcript[cut + 1:]

    def parse(self, raw):
        try:
            obj = json.loads(raw.strip())
        except json.JSONDecodeError:
            raise RuntimeError("reply was not one action") from None
        if not fits(obj, self.schema):
            raise RuntimeError("reply was not one action")
        return obj

    def say(self, text):
        return i2c.pack_write(self.tg, bytes([CHAT]) + text.encode())

    def frame_for(self, obj):
        action = obj["action"]
        if action == "nothing":
            return None
        if action == "say":
            return self.say(obj["text"])
        target = int(obj["address"], 16)
        data = bytes([int(obj["register"], 16)]) + obj["text"].encode()
        if action == "read":
            return i2c.pack_call(target, data)
        return i2c.pack_write(target, data)

    async def on_frame(self, src, line):
        data = i2c.write_payload(line)
        reading = line.split()[2] == "R" or " Sr " in line
        if data == bytes([IDENT]):
            if " Sr " in line:
                return i2c.with_payload(line, self.identity.encode())
            raise i2c.Nack()
        if reading and (not data or data[0] == STATUS):
            return i2c.reply(line, data, f"{1 if self.busy else 0:02d}".encode())
        if self.busy:
            return i2c.nack(self.bus.addr)
        if src == self.bus.addr:
            self.self_turns += 1
            if self.self_turns > self.cap:
                return i2c.nack_data(line)
        elif src in self.owners:
            self.self_turns = 0
        self.busy = True
        asyncio.create_task(self.turn(src, data))
        return i2c.pack_write(self.bus.addr, data)

    async def exec(self, prompt):
        proc = await asyncio.create_subprocess_exec(
            *self.argv(), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(self.run),
        )
        self.proc = proc
        try:
            out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), self.limit)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        finally:
            self.proc = None
        if proc.returncode:
            tail = err.decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError(tail[-1] if tail else f"exit {proc.returncode}")
        return out.decode()

    def _post(self, prompt):
        body = {
            "model": self.table["model"],
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": self.cfg["llama"]["response_type"], "schema": self.schema},
        }
        body.update(i2c.llama_sample(self.cfg, self.table["max_tokens"]))
        request = urllib.request.Request(
            i2c.llama_url(self.cfg, self.table) + "/v1/chat/completions",
            data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.limit) as response:
            payload = json.loads(response.read().decode())
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise RuntimeError("reply was not one action")
        return content

    async def post(self, prompt):
        return await asyncio.to_thread(self._post, prompt)

    async def exchange(self, prompt):
        return await getattr(self, self.table["call"])(prompt)

    async def boot_exec(self):
        self.argv()
        return None

    async def boot_serve(self):
        return await i2c.serve_until(
            self.root, i2c.llama_argv(self.cfg, self.table), i2c.llama_url(self.cfg, self.table),
            self.cfg["limits"], float(self.cfg["start"]["mind"]), "mind server is missing",
        )

    async def start(self):
        return await getattr(self, self.table["boot"])()

    async def stop(self, started):
        if started is None:
            return
        await i2c.serve_stop(*started)

    async def pump(self):
        if self.busy or not self.outbox:
            return
        frame = self.outbox[0]
        target = int(frame.split()[1], 16)
        try:
            answered = await self.bus.request(target, frame)
        except i2c.BusOff:
            raise
        except Exception as error:
            self.outbox.pop(0)
            if not (target == self.bus.addr and self.self_turns > self.cap):
                self.outbox.append(i2c.pack_write(self.bus.addr, str(error).encode()))
            return
        self.outbox.pop(0)
        if " Sr " in frame or frame.split()[2] == "R":
            payload = i2c.read_payload(answered)
            if payload:
                self.outbox.append(i2c.pack_write(self.bus.addr, payload))

    async def turn(self, src, data):
        fault = None
        frame = None
        text = ""
        try:
            try:
                text = data.decode()
                stdin = self.stdin_text(src, text)
                raw = await self.exchange(stdin)
                self.count += 1
                (self.run / f"turn-{self.count}.txt").write_text(stdin + "\n---\n" + raw, encoding="utf-8")
                frame = self.frame_for(self.parse(raw))
                self.keep(text, raw.strip())
            except TimeoutError:
                fault = f"{self.part} timed out."
            except Exception as error:
                fault = f"{self.part} stopped: {error}"
            if fault:
                self.keep(text, fault)
                frame = self.say(fault)
            if frame:
                self.outbox.append(frame)
        finally:
            self.busy = False


def build(bus, cfg, root, run):
    mind = Mind(bus, cfg, run, root)

    async def around(serve):
        started = await mind.start()
        try:
            await serve
        finally:
            await mind.stop(started)

    return mind.on_frame, mind.pump, around


def main():
    i2c.main_for("mind", build)


if __name__ == "__main__":
    main()
