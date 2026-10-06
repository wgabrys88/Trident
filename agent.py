import asyncio
import json

from store import ROOT, encode, read, write
from tools import Catalog


class Agent:
    def __init__(self, models, line, record, state):
        self.models, self.line, self.record, self.state = models, line, record, state

    def save(self):
        write(self.record.folder / "session.json", self.state)
        mind = read(ROOT / "mind.json")
        owner_goal = self.state["suspended"][0] if self.state["suspended"] else self.state["goal"]
        retained = list(self.state["open_work"])
        if owner_goal is not None:
            retained.append({"goal": owner_goal, "evidence": "Unfinished owner work", "session": str(self.record.folder / "session.json")})
        if mind["open_work"] != retained:
            mind["open_work"] = retained
            write(ROOT / "mind.json", mind)

    async def receive(self, event):
        kind, value = event
        if kind == "error":
            raise value
        if kind == "line":
            self.record.append("line_status", value)
            if self.state["goal"] is not None:
                self.state["history"].append({"line": value})
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
        mind = read(ROOT / "mind.json")
        generation = self.line.generation
        batch = json.loads(await self.models.luna(mind["actor"] +
            '\nReturn {"assessment":{"approach":"current method","failures":["observed failed attempt and evidence"],'
            '"change":"chosen material change, or why no change is needed"},'
            '"calls":[{"tool":"name","arguments":{}}]}. '
            'Assess the evidence before selecting actions. Python stores your assessment; you choose the response to failures.', {
            "persistent_notes": {"authority": "Historical notes only; never current transport state", "text": mind["memory"]},
            "transport": {"authority": "Current transport state", "call": self.line.state,
                          "id": self.line.peer.id if self.line.peer is not None else None},
            "goal": self.state["goal"], "suspended": self.state["suspended"],
            "open_work": self.state["open_work"], "assessment": self.state["assessment"],
            "hearing": {"busy": self.line.hearing.busy, "queued": self.line.hearing.queue.qsize()},
            "tools": catalog.specs, "history": self.state["history"],
        }))
        if generation != self.line.generation:
            self.record.append("batch_superseded", {"reason": "Input changed while Luna was deciding", "batch": batch})
            return
        assessment = batch["assessment"]
        if (not isinstance(assessment["approach"], str) or not assessment["approach"].strip()
                or not isinstance(assessment["failures"], list)
                or any(not isinstance(item, str) for item in assessment["failures"])
                or not isinstance(assessment["change"], str) or not assessment["change"].strip()):
            raise ValueError("Luna must provide an approach, failure evidence, and chosen change")
        self.state["assessment"] = assessment
        self.record.append("assessment", assessment)
        calls = batch["calls"]
        if not isinstance(calls, list) or not calls:
            raise ValueError("Luna must return a nonempty calls array")
        for index, call in enumerate(calls):
            if generation != self.line.generation:
                self.record.append("batch_superseded", {"reason": "Input changed during execution", "remaining": calls[index:]})
                break
            name, arguments = call["tool"], call["arguments"]
            if catalog.specs[name]["boundary"] and index != len(calls) - 1:
                raise ValueError(f"{name} must be last in its batch")
            self.record.append("tool_start", call)
            result = await catalog.call(self, name, arguments)
            entry = {"call": call, "result": result}
            self.state["history"].append(entry)
            self.record.append("tool_result", entry)
            self.save()
        if self.state["finished"] is not None:
            self.state["lessons"].append({
                **self.state["finished"], "trace": self.record.trace(self.state["start"]),
            })
            self.state["finished"] = None
            self.save()

    async def learn(self):
        generation = self.line.generation
        lesson = self.state["lessons"][0]
        self.record.append("learning_started", {"goal": lesson["goal"]})
        mind = read(ROOT / "mind.json")
        catalog = Catalog()
        study = ""
        trace = lesson["trace"]
        for events in self.portions(trace):
            study = await self.models.gemma(
                "Idle learning mode. Study these complete JSON trace events. Each event is intact; "
                "a portion boundary is not a runtime failure. A lone request at a portion boundary may finish in the next portion. "
                "Keep concise cumulative lessons from all portions: failures, repairs, observations, Luna's batches, "
                "and improvements to your visual reports and cooperation. Separate evidence from speculation. "
                "File paths identify retained evidence; they are not images you have seen. "
                "Return at most 1000 words.\nRecorded outcome: " + encode({key: value for key, value in lesson.items() if key != "trace"}) +
                "\nPrevious study:\n" + study +
                "\nNext complete events:\n" + events, [],
            )
        raw = await self.models.luna(
            "Idle learning mode. Teach Gemma from this recorded task and its study. "
            "Return only JSON with actor, student, tools, lesson. actor and student are complete replacement prompts; "
            "tools is the complete current catalog with improved descriptions only. Preserve handlers, boundaries, "
            "parameter types, and required arguments. Preserve the owner's behavior requirements. "
            "lesson states concrete corrections and their trace evidence. No live action or invented success.",
            {"mind": mind, "tools": catalog.specs, "recorded_task": lesson, "gemma_study": study},
        )
        teaching = json.loads(raw)
        for name, spec in catalog.specs.items():
            updated = teaching["tools"][name]
            if updated["handler"] != spec["handler"] or updated["boundary"] != spec["boundary"]:
                raise ValueError("An idle lesson cannot change a tool implementation")
            before = json.loads(encode(spec["parameters"]))
            after = json.loads(encode(updated["parameters"]))
            self.remove_descriptions(before)
            self.remove_descriptions(after)
            if before != after:
                raise ValueError("An idle lesson cannot change tool arguments")
        if set(teaching["tools"]) != set(catalog.specs):
            raise ValueError("An idle lesson cannot add or delete tools")
        for key in ("actor", "student", "lesson"):
            if not isinstance(teaching[key], str) or not teaching[key].strip():
                raise ValueError(f"Empty lesson field: {key}")
        if generation != self.line.generation:
            raise asyncio.CancelledError
        mind["actor"], mind["student"] = teaching["actor"], teaching["student"]
        write(ROOT / "mind.json", mind)
        write(ROOT / "tools.json", teaching["tools"])
        self.record.append("lesson_applied", {"gemma_study": study, **teaching})
        self.state["lessons"].pop(0)
        self.save()

    @staticmethod
    def portions(trace):
        portion = ""
        for line in trace.splitlines():
            json.loads(line)
            if portion and len(portion) + len(line) + 1 > 12000:
                yield portion
                portion = ""
            portion += line + "\n"
        if portion:
            yield portion

    @staticmethod
    def remove_descriptions(schema):
        if isinstance(schema, dict):
            schema.pop("description", None)
            for value in schema.values():
                Agent.remove_descriptions(value)
        elif isinstance(schema, list):
            for value in schema:
                Agent.remove_descriptions(value)

    async def serve(self):
        while not self.state["restart"]:
            if not self.line.inbox.empty():
                await self.receive(await self.line.inbox.get())
                continue
            hearing_idle = not self.line.hearing.busy and self.line.hearing.queue.empty()
            if self.state["shutdown"] and not self.state["lessons"] and hearing_idle:
                return
            if (self.state["goal"] is not None or self.state["attention"]) and not self.state["waiting"]:
                await self.work(self.step(), learning=False)
            elif (self.state["goal"] is None and not self.state["attention"] and self.state["lessons"]
                  and self.line.state == "down" and hearing_idle):
                await self.work(self.learn(), learning=True)
            else:
                await self.receive(await self.line.inbox.get())

    async def work(self, coroutine, learning):
        task = asyncio.create_task(coroutine)
        incoming = asyncio.create_task(self.line.inbox.get())
        try:
            while True:
                done, _ = await asyncio.wait((task, incoming), return_when=asyncio.FIRST_COMPLETED)
                if task in done and not (learning and incoming in done):
                    await task
                    if incoming.done():
                        await self.receive(incoming.result())
                    else:
                        incoming.cancel()
                        try:
                            await incoming
                        except asyncio.CancelledError:
                            pass
                    return
                event = incoming.result()
                if learning or event[0] == "error":
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        self.record.append("learning_interrupted" if learning else "work_interrupted", {})
                    await self.receive(event)
                    return
                await self.receive(event)
                incoming = asyncio.create_task(self.line.inbox.get())
        finally:
            incoming.cancel()
            try:
                await incoming
            except asyncio.CancelledError:
                pass
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
