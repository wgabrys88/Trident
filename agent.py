import asyncio
import json

from store import cancel, save
from tools import Catalog


class Agent:
    def __init__(self, models, line, record, state):
        self.models, self.line, self.record, self.state = models, line, record, state
        self.catalog = Catalog()
        self.deciding = False

    def save(self):
        save(self.record.folder, self.state)

    async def receive(self, event):
        kind, value = event
        if kind == "audio_pending":
            return
        if kind == "audio":
            text = value["recognition"]["text"]
            if not text.strip():
                self.save()
                return
            value = {"text": text, "audio": value["path"], "id": value["id"], "receipt": value["receipt"],
                     "superseded": value.get("superseded", False)}
        if kind in ("owner", "audio"):
            text = value["text"]
            if not text.strip():
                return
            if self.state["task"] and self.state["task"]["owner"]["receipt"] == value["receipt"]:
                self.state["history"].append({"owner_input": value, "receipt": value["receipt"]})
            elif value.get("superseded"):
                self.state["history"].append({"superseded_owner_input": value, "receipt": value["receipt"]})
        else:
            if kind == "error":
                value = {"type": type(value).__name__, "error": str(value)}
            receipt = self.record.append(kind, value)
            self.state["history"].append({"observation": value, "receipt": receipt})
            self.state.update(attention=True, waiting=False, shutdown=False)
        self.save()

    async def step(self):
        generation = self.line.generation
        text = None
        try:
            self.deciding = True
            text = await self.models.luna({
                "transport": {"call": self.line.state, "id": self.line.peer.id if self.line.peer else None,
                              "connected": bool(self.line.client and self.line.client.is_connected())},
                "task": self.state["task"], "open_work": self.state["open_work"],
                "assessment": self.state["assessment"], "receipts": self.state["receipts"],
                "hearing": {"busy": self.line.hearing.busy, "queued": self.line.hearing.queue.qsize(),
                            "pending": self.state["audio_pending"]},
                "tools": self.catalog.document, "history": self.state["history"],
            })
            self.deciding = False
            batch = self.parse_batch(text)
            self.catalog.validate({"$ref": "#/$defs/batch"}, batch)
            calls = batch["calls"]
            for index, call in enumerate(calls):
                spec = self.catalog.specs[call["tool"]]
                self.catalog.validate(spec["parameters"], call["arguments"])
                if spec["boundary"] and index != len(calls) - 1:
                    raise ValueError(f"{call['tool']} must end its batch")
        except Exception as error:
            self.record.append("rejected", {"error": str(error), "text": text})
            self.state["history"].append({"rejected": str(error)})
            self.state["attention"] = True
            self.save()
            await asyncio.sleep(1)
            return
        finally:
            self.deciding = False
        if generation != self.line.generation:
            self.record.append("batch_superseded", {"batch": batch})
            return
        self.state["assessment"] = batch["assessment"]
        self.record.append("assessment", batch["assessment"])
        for index, call in enumerate(calls):
            if generation != self.line.generation:
                self.record.append("batch_superseded", {"remaining": calls[index:]})
                break
            task_id = self.state["task"]["id"] if self.state["task"] else None
            self.record.append("tool_start", {"task_id": task_id, **call}, "LUNA", "TOOL")
            try:
                result = await self.catalog.call(self, **call)
            except Exception as error:
                self.record.append("tool_failure", {"call": call, "task_id": task_id, "error": str(error)})
                self.state["history"].append({"call": call, "error": str(error)})
                self.state["attention"] = True
                self.save()
                return
            entry = {"task_id": task_id, "call": call, "result": result}
            receipt = self.record.append("tool_result", entry, "TOOL", "LUNA")
            if self.state["task"] and self.state["task"]["id"] == task_id:
                self.state["receipts"].append(receipt)
            if call["tool"] != "finish":
                self.state["history"].append({**entry, "receipt": receipt})
            self.save()

    @staticmethod
    def parse_batch(text):
        candidate = text.strip()
        if candidate.startswith(chr(96) * 3):
            candidate = "\n".join(candidate.splitlines()[1:-1]).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            batches = []
            decoder = json.JSONDecoder()
            for index, character in enumerate(candidate):
                if character == "{":
                    try:
                        value, _ = decoder.raw_decode(candidate[index:])
                        if isinstance(value, dict) and "assessment" in value and "calls" in value:
                            batches.append(value)
                    except json.JSONDecodeError:
                        pass
            if not batches:
                raise
            return batches[-1]

    async def serve(self):
        while not self.state.get("repair"):
            if not self.line.inbox.empty():
                await self.receive(await self.line.inbox.get())
            elif self.state["shutdown"] and not self.line.hearing.busy and self.line.hearing.queue.empty():
                return
            elif (self.state["task"] or self.state["attention"]) and not self.state["waiting"]:
                await self.work(self.step())
            else:
                await self.receive(await self.line.inbox.get())

    async def work(self, coroutine):
        task = asyncio.create_task(coroutine)
        incoming = asyncio.create_task(self.line.inbox.get())
        try:
            while True:
                done, _ = await asyncio.wait((task, incoming), return_when=asyncio.FIRST_COMPLETED)
                if incoming in done:
                    await self.receive(incoming.result())
                    if self.deciding:
                        self.record.append("decision_superseded", {"generation": self.line.generation})
                        await cancel(task)
                        return
                    incoming = asyncio.create_task(self.line.inbox.get())
                if task in done:
                    await task
                    return
        finally:
            await cancel(incoming, task)
