import asyncio
import json

import desktop
from store import CONFIG, ROOT, cancel, read, write
from tools import Catalog


class Agent:
    def __init__(self, models, line, record, state):
        self.models, self.line, self.record, self.state = models, line, record, state
        self.acting = False
        self.seen = None

    def save(self):
        write(self.record.folder / "session.json", self.state)
        mind = read(ROOT / "mind.json")
        owner_goal = self.state["goal"]
        retained = list(self.state["open_work"])
        if owner_goal is not None:
            retained.append({"goal": owner_goal, "evidence": "Unfinished owner work", "session": str(self.record.folder / "session.json")})
        stored = {"open_work": retained}
        if mind != stored:
            write(ROOT / "mind.json", stored)

    async def receive(self, event):
        kind, value = event
        if kind == "error":
            raise value
        if kind == "line":
            self.record.append("line_status", value)
            self.state["history"].append({"line": value})
            self.state["attention"] = True
            self.state["waiting"] = False
            self.state["shutdown"] = False
            self.save()
            return
        if not self.state["recording"] and kind != "audio":
            self.state["start"] = value["start"] if kind == "audio_pending" else self.record.offset()
            self.state["history"] = []
            self.state["recording"] = True
        if kind == "audio_pending":
            self.state["history"].append({"audio_observation": {**value, "status": "transcribing"}})
            self.save()
            return
        if kind == "audio":
            entry = next(entry for entry in self.state["history"]
                         if entry.get("audio_observation", {}).get("id") == value["id"])
            entry["audio_observation"] = {**value, "status": "complete"}
            text = value["recognition"]["text"]
            if not text.strip():
                self.save()
                return
            entry["audio"] = text
            value = text
        else:
            self.state["history"].append({kind: value})
        self.state["attention"] = True
        self.state["shutdown"] = False
        self.state["waiting"] = False
        self.record.append("owner_input", {"text": value, "source": kind})
        self.save()

    async def step(self):
        catalog = Catalog()
        generation = self.line.generation
        text = None
        try:
            text = await self.models.luna((ROOT / "AGENTS.md").read_text(encoding="utf-8"), {
                "transport": {"call": self.line.state, "id": self.line.peer.id if self.line.peer is not None else None},
                "goal": self.state["goal"],
                "open_work": self.state["open_work"], "assessment": self.state["assessment"],
                "hearing": {"busy": self.line.hearing.busy, "queued": self.line.hearing.queue.qsize()},
                "tools": catalog.document, "history": self.state["history"],
            })
            batch = json.loads(text)
            catalog.validate({"$ref": "#/$defs/batch"}, batch)
            calls = batch["calls"]
            for call in calls:
                name = call["tool"]
                if name not in catalog.specs:
                    raise KeyError(name)
                if catalog.specs[name]["boundary"] and call is not calls[-1]:
                    raise ValueError(f"{name} must be last in its batch")
        except Exception as error:
            self.state["history"].append({"rejected": str(error)})
            self.record.append("rejected", {"error": str(error), "text": text})
            self.state["attention"] = True
            self.save()
            return
        if generation != self.line.generation:
            self.record.append("batch_superseded", {"reason": "Input changed while Luna was deciding", "batch": batch})
            return
        assessment = batch["assessment"]
        self.state["assessment"] = assessment
        self.record.append("assessment", assessment)
        for index, call in enumerate(calls):
            if generation != self.line.generation:
                self.record.append("batch_superseded", {"reason": "Input changed during execution", "remaining": calls[index:]})
                break
            name = call["tool"]
            self.record.append("tool_start", call, "LUNA", "TOOL (" + name + ")")
            try:
                result = await catalog.call(self, **call)
            except Exception as error:
                self.state["history"].append({"rejected": str(error), "call": call})
                self.record.append("rejected", {"error": str(error), "call": call})
                self.state["attention"] = True
                self.save()
                return
            entry = {"call": call, "result": result}
            self.state["history"].append(entry)
            self.record.append("tool_result", entry, "TOOL (" + name + ")", "LUNA")
            self.save()

    async def watch_screen(self):
        limit = CONFIG["screen"]["difference"]
        pending = None
        motion = 0
        self.seen = await asyncio.to_thread(desktop.sample)
        while True:
            await asyncio.sleep(CONFIG["screen"]["interval"])
            if self.acting or self.state["attention"] or not self.line.inbox.empty() or self.line.hearing.busy:
                continue
            current = await asyncio.to_thread(desktop.sample)
            if desktop.difference(self.seen, current) <= limit:
                pending = None
                motion = 0
                continue
            motion += 1
            if pending is not None and (desktop.difference(pending, current) <= limit or motion >= CONFIG["screen"]["settle"]):
                self.seen = current
                pending = None
                motion = 0
                self.line.emit("screen", "The desktop changed apart from Trident.")
            else:
                pending = current

    async def serve(self):
        watch = asyncio.create_task(self.watch_screen())
        try:
            await self.live()
        finally:
            await cancel(watch)

    async def live(self):
        while not self.state.get("restart"):
            if not self.line.inbox.empty():
                await self.receive(await self.line.inbox.get())
                continue
            if self.state["shutdown"] and not self.line.hearing.busy and self.line.hearing.queue.empty():
                return
            if (self.state["goal"] is not None or self.state["attention"]) and not self.state["waiting"]:
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
                    event = incoming.result()
                    if event[0] == "error":
                        await cancel(task)
                        self.record.append("work_interrupted", {})
                    await self.receive(event)
                    incoming = asyncio.create_task(self.line.inbox.get())
                if task in done:
                    await task
                    return
        finally:
            await cancel(incoming, task)
