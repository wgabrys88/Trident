import asyncio
import json
import uuid

from audio import wav
from models import execute
from store import CONFIG, ROOT, encode, read, write
from tools import Catalog


class Agent:
    def __init__(self, models, line, record, state):
        self.models, self.line, self.record, self.state = models, line, record, state

    def save(self):
        write(self.record.folder / "session.json", self.state)

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
        if self.state["goal"] is None:
            self.state["start"] = self.record.offset()
        if kind == "audio":
            path = self.record.folder / ("heard-" + uuid.uuid4().hex + ".wav")
            path.write_bytes(wav(value, 16000))
            self.record.append("owner_audio", {"path": str(path)})
            output = await execute([
                CONFIG["ears"]["command"], "transcribe", str(path), "--model", CONFIG["ears"]["model"],
                "--device", "cpu", "--stream", "--format", "json", "--quiet",
            ])
            recognition = json.loads(output)
            observation = {"path": str(path), "recognition": recognition}
            self.record.append("asr_result", observation)
            value = recognition["text"]
            if not value.strip():
                if self.state["goal"] is not None:
                    self.state["history"].append({"audio_observation": observation})
                    self.state["waiting"] = False
                    self.save()
                return
        if self.state["goal"] is None:
            self.state["goal"] = value
            self.state["history"] = []
            self.state["shutdown"] = False
        self.state["waiting"] = False
        self.state["history"].append({kind: value})
        self.record.append("owner_input", {"text": value, "source": kind})
        self.save()

    async def step(self):
        catalog = Catalog()
        mind = read(ROOT / "mind.json")
        request = encode({
            "prompt": mind["actor"], "memory": mind["memory"],
            "goal": self.state["goal"], "suspended": self.state["suspended"],
            "call": self.line.state, "tools": catalog.specs, "history": self.state["history"],
        })
        batch = json.loads(await self.models.luna(request))
        calls = batch["calls"]
        if not isinstance(calls, list) or not calls:
            raise ValueError("Luna must return a nonempty calls array")
        for index, call in enumerate(calls):
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
        for offset in range(0, len(trace), 12000):
            study = await self.models.gemma(
                "Idle learning mode. Study the next contiguous portion of this recorded task. "
                "Keep concise cumulative lessons from all portions: failures, repairs, observations, Luna's batches, "
                "and improvements to your visual reports and cooperation. Separate evidence from speculation. "
                "File paths identify retained evidence; they are not images you have seen. "
                "Return at most 1000 words.\nGoal: " + lesson["goal"] + "\nPrevious study:\n" + study +
                "\nNext trace portion:\n" + trace[offset:offset + 12000], [],
            )
        raw = await self.models.luna(encode({
            "instruction": "Idle learning mode. Teach Gemma from this recorded task and its study. "
            "Return only JSON with actor, student, tools, lesson. actor and student are complete replacement prompts; "
            "tools is the complete current catalog with improved descriptions only. Preserve handlers, boundaries, "
            "parameter types, and required arguments. Preserve the owner's behavior requirements. "
            "lesson states concrete corrections and their trace evidence. No live action or invented success.",
            "mind": mind, "tools": catalog.specs, "recorded_task": lesson, "gemma_study": study,
        }))
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
            if self.state["shutdown"] and not self.state["lessons"]:
                return
            if not self.line.inbox.empty():
                await self.receive(await self.line.inbox.get())
                continue
            if self.state["goal"] is not None and not self.state["waiting"]:
                await self.work(self.step(), learning=False)
            elif self.state["goal"] is None and self.state["lessons"] and self.line.state == "down":
                await self.work(self.learn(), learning=True)
            else:
                await self.receive(await self.line.inbox.get())

    async def work(self, coroutine, learning):
        task = asyncio.create_task(coroutine)
        incoming = asyncio.create_task(self.line.inbox.get())
        pending = []
        try:
            while True:
                done, _ = await asyncio.wait((task, incoming), return_when=asyncio.FIRST_COMPLETED)
                if task in done and not (learning and incoming in done):
                    await task
                    if incoming.done():
                        pending.append(incoming.result())
                    else:
                        incoming.cancel()
                        try:
                            await incoming
                        except asyncio.CancelledError:
                            pass
                    for event in pending:
                        await self.receive(event)
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
                pending.append(event)
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
