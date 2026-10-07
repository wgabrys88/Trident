import asyncio
import hashlib
from pathlib import Path

from jsonschema import Draft202012Validator

import desktop
from audio import call_pcm
from process import execute
from store import ROOT, read, source_paths


class Catalog:
    def __init__(self):
        self.document = read(ROOT / "tools.json")
        self.specs = self.document["tools"]
        for spec in self.specs.values():
            Draft202012Validator.check_schema({"$defs": self.document["$defs"], **spec["parameters"]})

    def validate(self, schema, value):
        Draft202012Validator({"$defs": self.document["$defs"], **schema}).validate(value)

    async def call(self, host, tool, arguments):
        spec = self.specs[tool]
        self.validate(spec["parameters"], arguments)
        module, function = spec["handler"].split(":", 1)
        try:
            if module == "tools":
                result = await globals()[function](host, **arguments)
            elif module == "desktop":
                result = await asyncio.to_thread(getattr(desktop, function), **arguments)
            elif module == "line":
                result = await getattr(host.line, function)(**arguments)
            else:
                raise ValueError(f"Unknown handler module: {module}")
        except Exception as error:
            if spec["observe"]:
                observation = {"failed_action": tool, "error": str(error)}
                try:
                    observation["seen"] = await shot(host, None)
                except Exception as capture_error:
                    observation["observation_error"] = str(capture_error)
                host.record.append("failed_action_observation", observation)
                host.state["history"].append(observation)
            raise
        if spec["observe"]:
            await asyncio.sleep(0.15)
            try:
                result = {"execution": result, "seen": await shot(host, None)}
            except Exception as error:
                result = {"execution": result, "observation_error": str(error)}
                host.record.append("observation_failure", result)
        return result


async def shot(host, region):
    payload, metadata = await asyncio.to_thread(desktop.capture, region)
    return {"image": str(host.record.artifact(payload, "png")), **metadata}


async def screen(host, region):
    return await shot(host, region)


async def images(host, views):
    shots = []
    for view in views:
        payload, metadata = await asyncio.to_thread(desktop.image_view, view)
        shots.append({"image": str(host.record.artifact(payload, "png")), **metadata})
    return {"views": shots}


async def consult(host, task):
    host.state["repair"] = {"request": task, "task": host.state["task"],
                            "record_position": host.record.offset()}
    host.state["attention"] = True
    host.save()
    return {"repair": "queued; the supervisor edits after this body exits", "request": task}


async def python(host, code):
    output = await execute([], code.encode("utf-8"), host.record.folder, host.record,
                           low=True, computation=True)
    return {"stdout": output, "exit": 0}


async def file(host, operation, path, text):
    target = Path(path).expanduser().resolve()
    if operation in ("write", "mkdir") and target.is_relative_to(ROOT):
        raise ValueError("Source and canonical runtime writes require consult or their body handler")
    if operation == "write" and target.exists() and target.stat().st_nlink != 1:
        raise ValueError("Writes through hard links are not supported")
    if operation == "read":
        return {"path": str(target), "text": target.read_text(encoding="utf-8")}
    if operation == "list":
        return {"path": str(target), "entries": [str(item) for item in target.iterdir()]}
    if operation == "mkdir":
        target.mkdir(parents=True, exist_ok=True)
        return {"created": str(target), "exists": target.is_dir()}
    if operation == "write":
        target.write_text(text, encoding="utf-8")
        return {"path": str(target), "bytes": target.stat().st_size,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    raise ValueError(operation)


async def dial(host, text):
    path = await host.models.voice(text)
    pcm = call_pcm(path.read_bytes())
    if len(pcm) < 96000:
        raise RuntimeError("Voice file is empty")
    outcome = await host.line.dial()
    if not outcome["answered"]:
        return {**outcome, "words": text, "audio": str(path)}
    return {**outcome, "channel": "call", **await host.line.speak(pcm), "audio": str(path), "words": text}


async def speak(host, parts):
    receipts = []
    for text in parts:
        path = await host.models.voice(text)
        if host.line.state == "up":
            receipt = {"channel": "call", **await host.line.speak(call_pcm(path.read_bytes())),
                       "audio": str(path), "words": text}
        else:
            receipt = await host.line.send_audio(path, text)
        receipts.append(receipt)
        host.record.append("speech_sent", receipt)
    return {"parts": receipts}


async def goal(host, text, resume_id):
    task = host.state["task"]
    if task is None:
        raise ValueError("Only a new owner input can activate a task")
    if resume_id is not None:
        retained = next(item for item in host.state["open_work"] if item["id"] == resume_id)
        task["id"] = retained["id"]
        task["selected_by"] = task["owner"]
        host.state["open_work"] = [item for item in host.state["open_work"] if item["id"] != resume_id]
        host.state.update(assessment=None, history=[], receipts=[])
    task["text"] = text
    return task


async def files(host):
    return {str(path.relative_to(ROOT)): path.read_text(encoding="utf-8")
            for path in source_paths()}


async def wait(host, memory):
    host.state.update(waiting=True, attention=False)
    return {"waiting": "Owner or transport input", "memory": memory}


async def finish(host, evidence, receipts, outcome, shutdown, owner_receipt):
    task = host.state["task"]
    if task is None:
        raise ValueError("There is no active owner task to finish")
    if not evidence.strip():
        raise ValueError("Closing a task requires nonempty evidence")
    if shutdown and host.line.state != "down":
        raise ValueError("Hang up before shutdown")
    if shutdown and outcome == "paused":
        raise ValueError("Paused work remains alive; use wait after pausing")
    if host.line.hearing.busy or not host.line.hearing.queue.empty() or host.state["audio_pending"]:
        raise ValueError("Receive pending owner speech before closing this task")
    if outcome == "completed":
        if not receipts:
            raise ValueError("Completion requires current-task observation or action receipts")
        for offset in receipts:
            event = host.record.event(offset)
            if offset not in host.state["receipts"] or event["kind"] != "tool_result":
                raise ValueError("Evidence must reference recorded current-task tool results")
            value = event["value"]
            if value["task_id"] != task["id"] or value["call"]["tool"] in ("goal", "wait", "consult", "finish"):
                raise ValueError("Task-management acknowledgements do not prove completion")
            if isinstance(value["result"], dict) and "observation_error" in value["result"]:
                raise ValueError("Verify the failed observation before completing")
    if outcome == "cancelled":
        if owner_receipt != task["owner"]["receipt"] or host.record.event(owner_receipt)["kind"] != "owner_input":
            raise ValueError("Cancellation must reference the current owner's recorded instruction")
    elif owner_receipt is not None:
        raise ValueError("owner_receipt is only for cancellation")
    retained = {item["id"]: item for item in host.state["open_work"] if item["id"] != task["id"]}
    if outcome == "paused":
        retained[task["id"]] = {**task, "evidence": evidence}
    host.state.update(task=None, assessment=None, history=[], receipts=[], open_work=list(retained.values()),
                      attention=not shutdown, waiting=False, shutdown=shutdown)
    return {"task": task, "evidence": evidence, "receipts": receipts, "outcome": outcome}
