import asyncio
import types
from pathlib import Path

from jsonschema import Draft202012Validator

import desktop
from audio import call_pcm
from store import ROOT, cancel, read, write


class Catalog:
    def __init__(self):
        self.document = read(ROOT / "tools.json")
        self.specs = self.document["tools"]
        self.modules = {"desktop": desktop}
        for name, spec in self.specs.items():
            module_name, _ = spec["handler"].split(":")
            if module_name != "line" and module_name not in self.modules:
                path = ROOT / (module_name + ".py")
                module = types.ModuleType(module_name)
                module.__file__ = str(path)
                exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
                self.modules[module_name] = module

    def validate(self, schema, value):
        Draft202012Validator({"$defs": self.document["$defs"], **schema}).validate(value)

    async def call(self, host, tool, arguments, observation=None):
        spec = self.specs[tool]
        self.validate(spec["parameters"], arguments)
        if spec["observe"]:
            self.validate({"$ref": "#/$defs/observation"}, observation)
        elif observation is not None:
            raise ValueError("Observation tools already return visual evidence; do not add a second inspection")
        module, function = spec["handler"].split(":")
        handler = getattr(host.line if module == "line" else self.modules[module], function)
        if module == "desktop":
            result = await asyncio.to_thread(handler, **arguments)
        else:
            result = await handler(**arguments) if module == "line" else await handler(host, **arguments)
        if spec["observe"]:
            question = "Expected result to check, not a fact: " + observation["expected"] + "\nNext-plan question: " + observation["question"]
            result = {"execution": result, "observation": await screen(host, question, observation["region"], observation["max_edge"])}
        return result


async def screen(host, question, region, max_edge):
    return await visual(host, question, [await asyncio.to_thread(desktop.capture, region, max_edge)])


async def images(host, question, views):
    return await visual(host, question, [await asyncio.to_thread(desktop.image_view, view) for view in views])


async def visual(host, question, prepared):
    result = await host.models.gemma({"question": question, "views": [metadata for _, metadata in prepared]},
                                    [payload for payload, _ in prepared])
    return {"views": [{**metadata, "image": path} for (_, metadata), path in zip(prepared, result["images"])],
            "report": result["report"]}


async def python(host, code):
    namespace = {"host": host, "ROOT": ROOT, "asyncio": asyncio}
    function = "async def action():\n" + "\n".join("    " + line for line in code.splitlines())
    exec(compile(function, "<luna-python>", "exec"), namespace)
    return await namespace["action"]()


async def speak(host, parts):
    if host.line.state != "up":
        raise RuntimeError("Speech requires a connected Telegram call")
    path = await host.models.voice(parts[0])
    pending = None
    receipts = []
    try:
        for index, text in enumerate(parts):
            if index + 1 < len(parts):
                pending = asyncio.create_task(host.models.voice(parts[index + 1]))
            result = await host.line.speak(call_pcm(path.read_bytes()))
            receipt = {**result, "audio": str(path), "words": text}
            receipts.append(receipt)
            host.record.append("speech_transmitted", receipt)
            if pending is not None:
                path = await pending
                pending = None
        return {"parts": receipts}
    finally:
        if pending is not None:
            await cancel(pending)


async def remember(host, text):
    mind = read(ROOT / "mind.json")
    mind["memory"] = text
    write(ROOT / "mind.json", mind)
    return {"memory": text}


async def goal(host, text, retain_previous):
    if host.state["suspended"]:
        raise RuntimeError("Resume self-healing before selecting an owner goal")
    previous = host.state["goal"]
    if retain_previous and previous is not None and previous != text:
        host.state["open_work"].append({"goal": previous, "evidence": "Retained by Luna while selecting another goal"})
    host.state["open_work"] = [item for item in host.state["open_work"] if item["goal"] != text]
    host.state["goal"] = text
    host.state["attention"] = False
    host.state["waiting"] = False
    if previous != text:
        host.state["assessment"] = None
    return {"goal": text, "open_work": host.state["open_work"]}


async def files(host):
    return {path.name: path.read_text(encoding="utf-8") for path in ROOT.iterdir() if path.is_file()}


async def heal(host, goal):
    if host.state["suspended"]:
        raise RuntimeError("A self-healing goal is already active")
    host.state["suspended"] = [host.state["goal"]]
    host.state["goal"] = "Self-heal Trident: " + goal
    return {"goal": host.state["goal"], "suspended": host.state["suspended"]}


async def rewrite(host, files, delete):
    if not host.state["suspended"]:
        raise RuntimeError("Rewriting requires an explicit self-healing goal")
    for name in [*files, *delete]:
        if Path(name).name != name:
            raise ValueError("Source files must be directly inside the Trident workspace root")
    for name in delete:
        (ROOT / name).unlink()
    for name, content in files.items():
        (ROOT / name).write_text(content, encoding="utf-8")
    return {"written": list(files), "deleted": delete}


async def resume(host, evidence):
    [goal] = host.state["suspended"]
    host.state["goal"] = goal
    host.state["suspended"] = []
    return {"goal": goal, "repair_evidence": evidence}


async def activate(host):
    if not host.state["suspended"] or host.line.state != "down":
        raise RuntimeError("Activation requires a self-healing goal and a closed Telegram call")
    host.state["restart"] = True
    return {"activation": "Process restart requested; suspended goal is preserved"}


async def wait(host, memory):
    await remember(host, memory)
    host.state["waiting"] = True
    host.state["attention"] = False
    return {"waiting": "Owner input", "goal": host.state["goal"]}


async def finish(host, evidence, outcome, shutdown):
    if host.state["suspended"]:
        raise RuntimeError("Resume the owner's goal before finishing it")
    if shutdown and host.line.state != "down":
        raise RuntimeError("Hang up before requesting shutdown")
    if host.line.hearing.busy or not host.line.hearing.queue.empty():
        raise RuntimeError("Receive pending owner audio before closing the recorded task")
    if outcome == "paused" and host.state["goal"] is not None:
        host.state["open_work"].append({"goal": host.state["goal"], "evidence": evidence})
    host.state["finished"] = {"goal": host.state["goal"], "evidence": evidence, "outcome": outcome}
    host.state["goal"] = None
    host.state["attention"] = False
    host.state["shutdown"] = shutdown
    return host.state["finished"]
