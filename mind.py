import asyncio
import json
import subprocess
import sys

from jsonschema import Draft202012Validator, ValidationError

from i2c import QueuedDevice, Frame, Nack, Refusal, Server

class CommandPart:
    def __init__(self, device, settings):
        self.device, self.settings = device, settings
        folder = max((path for path in self.device.cfg.path(self.settings["versions"]).iterdir() if path.is_dir()), key=lambda path: path.name)
        settings = self.settings | {"entry": str(folder / self.settings["entry"]), "run": self.device.run}
        self.command = [str(folder / settings["executable"])] + [part.format(**settings) for part in settings["arguments"]]

    async def reply(self, prompt, schema):
        process = await asyncio.create_subprocess_exec(*self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                     stderr=asyncio.subprocess.PIPE, cwd=self.device.run, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        output, error = await process.communicate(prompt.encode())
        self.device.file(f"turn-{self.device.turn_number + 1}.events.jsonl").write_bytes(output)
        if process.returncode: raise RuntimeError(error.decode())
        try:
            events = [event for line in reversed(output.splitlines()) if line.strip() and isinstance(event := json.loads(line), dict)]
            return next(("".join(part["text"] for part in event["message"]["content"] if part.get("type") == "text") for event in events[:next((index for index, event in enumerate(events) if event.get("type") == "tool_call"), None)] if event.get("type") == "assistant"), None)
        except (ValueError, LookupError, TypeError, AttributeError): return None

class ServerPart:
    def __init__(self, device, settings):
        self.device = device
        device.server = Server(device, device.cfg["llama"] | settings)

    async def reply(self, prompt, schema):
        return await self.device.server.chat([{"role": "user", "content": prompt}], response_format={"type": "json_object", "schema": schema})

class Mind(QueuedDevice):
    def __init__(self):
        super().__init__("mind")
        self.part = self.cfg.mind(sys.argv[2])
        self.transport = {"exec": CommandPart, "post": ServerPart}[self.part["transport"]](self, self.part)
        self.schema = json.loads((self.cfg.root / "action.json").read_text())
        branches = []
        for branch in self.schema["oneOf"]:
            if (action := branch["properties"]["action"]["const"]) in ("read", "write"):
                for name, registers in self.cfg["registers"].items():
                    if allowed := [key for key, value in registers.items() if action in value.split(":")[0].split("/")]:
                        branches.append(branch | {"properties": branch["properties"] | {
                            "address": {"const": self.cfg["address"][name]}, "register": {"enum": allowed}}})
            else: branches.append(branch)
        self.schema["oneOf"] = branches
        self.validator = Draft202012Validator(self.schema)
        self.history, self.self_turns, self.turn_number, self.busy, self.owner_due = [], 0, 0, False, 0.0

    async def receive(self, source, frame):
        if frame.reading:
            return (f"{int(self.busy):02d}" if frame.data[0] == 0 else ";".join(self.part["identity"])).encode()
        if self.busy or (source != self.address and not self.queue.empty()):
            self.owner_due = asyncio.get_running_loop().time() + 2 * self.cfg["bus"]["frame_timeout"] if source in (self.cfg["address"]["telegram"], self.cfg["address"]["ears"]) else self.owner_due
            raise Nack(Refusal.NOT_READY)
        if source == self.address:
            if asyncio.get_running_loop().time() < self.owner_due:
                return self.history.append(f"Controller {source}: {frame.data[1:].decode()}\nAction: none yet; a waiting owner message went first") or b""
            if self.self_turns + (cost := -(-self.cfg["bus"]["self_turn_cap"] // self.cfg["bus"]["fault_turns"]) if frame.data[1:].startswith(b"Mind fault:") else 1) > self.cfg["bus"]["self_turn_cap"]:
                raise Nack(Refusal.FULL)
            self.self_turns += cost
        elif source in (self.cfg["address"]["telegram"], self.cfg["address"]["ears"], self.cfg["address"]["timer"]):
            self.self_turns, self.owner_due = 0, 0.0
        self.queue.put_nowait((source, frame))
        return b""

    def prompt(self, source, text):
        brief = (self.cfg.root / "prompt.txt").read_text(encoding="utf-8").format(grid=self.cfg["limits"]["grid"], telegram=self.cfg["address"]["telegram"], ears=self.cfg["address"]["ears"])
        registers = {self.cfg["address"][name]: {"role": name, "registers": values} for name, values in self.cfg["registers"].items()}
        return "\n".join((json.dumps(registers), json.dumps(self.schema), *self.history, f"Controller {source}:\n{text}", brief))

    async def work(self, source, frame):
        self.busy = True
        prompt = self.prompt(source, incoming := frame.data[1:].decode())
        raw = await asyncio.wait_for(self.transport.reply(prompt, self.schema), self.part["turn_seconds"])
        self.turn_number += 1
        self.file(f"turn-{self.turn_number}.txt").write_text(prompt + "\nReply:\n" + json.dumps(raw), encoding="utf-8")
        fault = None
        try:
            if not isinstance(raw, str): raise ValueError("Reply must be one JSON action object")
            action, end = json.JSONDecoder(object_pairs_hook=self.unique_object).raw_decode(raw, start := max(raw.find("{"), 0))
            if raw[:start].strip() or raw[end:].strip():
                print(f"Turn {self.turn_number}: kept the first JSON object {raw[start:end]!r} of {raw!r}", file=sys.stderr)
            self.validator.validate(action)
        except (ValueError, ValidationError) as error:
            reason = "it matched no action" if isinstance(error, ValidationError) else error.args[0].split(":")[0]
            fault = f"your reply was not one action object ({reason}), so nothing was done"
        self.history.append(f"Controller {source}: {incoming}\nAction: {raw if fault else json.dumps(action)}")
        while sum(map(len, self.history)) > self.part["context_chars"]: self.history.pop(0)
        self.busy = False
        while True:
            try:
                return await self.dispatch(action := {"action": "write", "address": self.address, "register": "01", "text": f"Mind fault: {fault}. Continue with one JSON action."} if fault else action)
            except (Nack, TimeoutError, ValueError) as error:
                failure = f"{dict(zip(self.cfg['address'].values(), self.cfg['address']))[action.get('address', self.cfg['address']['telegram'])]} {'refused' if isinstance(error, Nack) else 'failed'} {action.get('register', '10')}: {error}"
                stopped = fault and action.get("address") == self.address and getattr(error, "cause", None) == Refusal.FULL
                await self.send("telegram", "I stopped: I could not form a valid action." if stopped else f"My {action['action']} action was not carried out: {failure}.", "10", guarded=True)
                if action.get("address") == self.address:
                    return
                fault = f"that {action['action']} failed ({failure})"

    @staticmethod
    def unique_object(pairs):
        if len(result := dict(pairs)) != len(pairs): raise ValueError("Duplicate JSON property")
        return result

    async def dispatch(self, action):
        match action["action"]:
            case "say":
                await self.send("telegram", action["text"], "10")
            case "write" | "read":
                target = Frame(action["address"], bytes.fromhex(action["register"]) + action["text"].encode(), action["action"] == "read")
                result = await self.bus.transfer(target)
                if target.reading:
                    await self.send("mind", f"Read {target.address}/{action['register']}:\n{result.decode()}", "01")

if __name__ == "__main__":
    Mind().launch()
