import asyncio
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator

import desktop
from audio import call_pcm
from log import write as write_png
from models import run_process
from store import ROOT, read


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
                host.remember(observation)
            raise
        if spec["observe"]:
            await asyncio.sleep(0.15)
            try:
                result = {"execution": result, "seen": await shot(host, None)}
            except Exception as error:
                result = {"execution": result, "observation_error": str(error)}
        return result


async def shot(host, region):
    payload, metadata = await asyncio.to_thread(desktop.capture, region)
    path = write_png(payload, host.folder / "images")
    return {"image": str(path), "file": path.name, **metadata}


async def screen(host, region):
    return await shot(host, region)


async def images(host, views):
    shots = []
    for view in views:
        payload, metadata = await asyncio.to_thread(desktop.image_view, view)
        path = write_png(payload, host.folder / "images")
        shots.append({"image": str(path), "file": path.name, **metadata})
    return {"views": shots}


async def consult(host, task):
    host.state["repair"] = {"request": task, "task": host.state["task"]}
    host.state["attention"] = True
    host.save()
    return {"repair": "queued; the supervisor edits after this body exits", "request": task}


async def python(host, code):
    scratch = Path(tempfile.mkdtemp(prefix="trident-py-"))
    script = scratch / "run.py"
    body = "\n".join("    " + line for line in code.splitlines())
    script.write_text(
        "import asyncio, json\nfrom pathlib import Path\n"
        f"ROOT = Path({json.dumps(str(ROOT))})\n"
        "async def action():\n" + (body or "    return None") + "\n"
        "print(json.dumps({'result': asyncio.run(action())}, ensure_ascii=False))\n",
        encoding="utf-8")
    try:
        output = await run_process([sys.executable, "-B", str(script)], b"", scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
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
    payload = await host.models.voice(text)
    pcm = call_pcm(payload)
    if len(pcm) < 96000:
        raise RuntimeError("Voice file is empty")
    outcome = await host.line.dial()
    if not outcome["answered"]:
        return {**outcome, "words": text}
    return {**outcome, "channel": "call", **await host.line.speak(pcm), "words": text}


async def speak(host, parts):
    receipts = []
    for text in parts:
        payload = await host.models.voice(text)
        if host.line.state == "up":
            receipt = {"channel": "call", **await host.line.speak(call_pcm(payload)), "words": text}
        else:
            receipt = await host.line.send_audio(payload, text)
        receipts.append(receipt)
    return {"parts": receipts}


async def goal(host, text, resume_id):
    task = host.state["task"]
    if task is None:
        raise ValueError("Only a new owner input can activate a task")
    if resume_id is not None:
        retained = next((item for item in host.state["open_work"] if item["id"] == resume_id), None)
        if retained is None:
            raise ValueError("No retained task with that id")
        task["id"] = retained["id"]
        task["selected_by"] = task["owner"]
        host.state["open_work"] = [item for item in host.state["open_work"] if item["id"] != resume_id]
        host.state.update(assessment=None, history=[], receipts=[], results={})
    task["text"] = text
    return task


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
    hearing = host.line.hearing
    if hearing.busy or not hearing.queue.empty() or host.state["audio_pending"]:
        raise ValueError("Receive pending owner speech before closing this task")
    if outcome == "completed":
        if not receipts:
            raise ValueError("Completion requires current-task observation or action receipts")
        for receipt in receipts:
            value = host.state["results"].get(receipt)
            if receipt not in host.state["receipts"] or value is None:
                raise ValueError("Evidence must reference current-task tool results")
            if value["task_id"] != task["id"] or value["call"]["tool"] in ("goal", "wait", "consult", "finish"):
                raise ValueError("Task-management acknowledgements do not prove completion")
            if isinstance(value["result"], dict) and "observation_error" in value["result"]:
                raise ValueError("Verify the failed observation before completing")
    elif outcome == "cancelled":
        if owner_receipt != task["owner"]["receipt"]:
            raise ValueError("Cancellation must reference the current owner's instruction")
    elif owner_receipt is not None:
        raise ValueError("owner_receipt is only for cancellation")
    retained = {item["id"]: item for item in host.state["open_work"] if item["id"] != task["id"]}
    if outcome == "paused":
        retained[task["id"]] = {**task, "evidence": evidence}
    host.state.update(task=None, assessment=None, history=[], receipts=[], results={},
                      open_work=list(retained.values()), attention=not shutdown, waiting=False, shutdown=shutdown)
    return {"task": task, "evidence": evidence, "receipts": receipts, "outcome": outcome}
