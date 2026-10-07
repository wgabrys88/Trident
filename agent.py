import asyncio
import json
import uuid

from models import stop_if_capped
from store import cancel, save as save_state
from tools import Catalog


def wakes(event):
    kind, value = event
    if kind in ("ring", "line"):
        return True
    if kind == "owner":
        return bool(str(value.get("text", "")).strip())
    if kind == "audio":
        text = value.get("text") or (value.get("recognition") or {}).get("text") or ""
        return bool(str(text).strip())
    return False


class Agent:
    def __init__(self, models, line, folder, state):
        self.models, self.line, self.folder, self.state = models, line, folder, state
        self.catalog = Catalog()
        self.deciding = False
        kept = []
        for item in state["history"]:
            if not kept or kept[-1] != item:
                kept.append(item)
        state["history"] = kept[-40:]
        self.save()

    def save(self):
        save_state(self.folder, self.state)

    def remember(self, item):
        history = self.state["history"]
        if not history or history[-1] != item:
            history.append(item)
        del history[:-40]

    async def receive(self, event):
        kind, value = event
        if kind == "error":
            raise value if isinstance(value, BaseException) else RuntimeError(str(value))
        if kind == "audio_pending":
            return
        if kind == "audio":
            text = value["recognition"]["text"]
            if not text.strip():
                self.save()
                return
            value = {"text": text, "id": value["id"], "receipt": value["receipt"],
                     "sequence": value["sequence"], "superseded": value.get("superseded", False)}
        if kind in ("owner", "audio"):
            if not str(value.get("text", "")).strip():
                return
            key = "superseded_owner_input" if value.get("superseded") else "owner_input"
            self.remember({key: value, "receipt": value["receipt"]})
        else:
            self.remember({"observation": value})
            self.state["attention"] = True
            self.state["waiting"] = False
            if kind == "ring":
                self.state["shutdown"] = False
        self.save()

    async def step(self):
        generation = self.line.generation
        self.deciding = True
        try:
            text = await self.models.luna({
                "transport": {"call": self.line.state, "id": self.line.peer.id if self.line.peer else None,
                              "connected": bool(self.line.client and self.line.client.is_connected())},
                "task": self.state["task"], "open_work": self.state["open_work"],
                "assessment": self.state["assessment"], "receipts": self.state["receipts"],
                "hearing": {"busy": self.line.hearing.busy, "queued": self.line.hearing.queue.qsize(),
                            "pending": self.state["audio_pending"]},
                "tools": self.catalog.document, "history": self.state["history"][-40:],
            }, self.state, self.line.photo)
            batch = self.parse_batch(text)
            self.catalog.validate({"$ref": "#/$defs/batch"}, batch)
            calls = batch["calls"]
            for index, call in enumerate(calls):
                spec = self.catalog.specs[call["tool"]]
                self.catalog.validate(spec["parameters"], call["arguments"])
                if spec["boundary"] and index != len(calls) - 1:
                    raise ValueError(f"{call['tool']} must end its batch")
        finally:
            self.deciding = False
        if generation != self.line.generation:
            stop_if_capped()
            return
        self.state["assessment"] = batch["assessment"]
        for index, call in enumerate(calls):
            if generation != self.line.generation:
                break
            task_id = self.state["task"]["id"] if self.state["task"] else None
            try:
                result = await self.catalog.call(self, **call)
            except Exception as error:
                self.remember({"call": call, "error": str(error)})
                self.state["attention"] = True
                self.save()
                stop_if_capped()
                return
            entry = {"task_id": task_id, "call": call, "result": result}
            receipt = uuid.uuid4().hex
            if self.state["task"] and self.state["task"]["id"] == task_id:
                self.state["results"][receipt] = entry
                self.state["receipts"].append(receipt)
            if call["tool"] != "finish":
                self.remember({**entry, "receipt": receipt})
            self.save()
        stop_if_capped()

    @staticmethod
    def parse_batch(text):
        candidate = text.strip()
        if candidate.startswith(chr(96) * 3):
            candidate = "\n".join(candidate.splitlines()[1:-1]).strip()
        return json.loads(candidate)

    async def serve(self):
        while not self.state.get("repair"):
            if not self.line.inbox.empty():
                await self.receive(await self.line.inbox.get())
                continue
            if self.state["shutdown"] and not self.line.hearing.busy and self.line.hearing.queue.empty():
                return
            if (self.state["task"] or self.state["attention"]) and not self.state["waiting"]:
                await self.work(self.step())
                continue
            await self.receive(await self.line.inbox.get())

    async def work(self, coroutine):
        task = asyncio.create_task(coroutine)
        incoming = asyncio.create_task(self.line.inbox.get())
        try:
            while True:
                done, _ = await asyncio.wait((task, incoming), return_when=asyncio.FIRST_COMPLETED)
                if incoming in done:
                    event = incoming.result()
                    await self.receive(event)
                    if self.deciding and wakes(event):
                        await cancel(task)
                        return
                    incoming = asyncio.create_task(self.line.inbox.get())
                if task in done:
                    await task
                    return
        finally:
            await cancel(incoming, task)
