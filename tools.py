import asyncio
import inspect
import types
import uuid
from pathlib import Path

from jsonschema import Draft202012Validator

import desktop
from audio import call_pcm
from store import ROOT, encode, read, write


class Catalog:
    def __init__(self):
        self.specs = read(ROOT / "tools.json")
        self.handlers = {}
        modules = {}
        for name, spec in self.specs.items():
            Draft202012Validator.check_schema(spec["parameters"])
            module_name, function_name = spec["handler"].split(":")
            if module_name not in modules:
                path = ROOT / (module_name + ".py")
                module = types.ModuleType(module_name)
                module.__file__ = str(path)
                exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
                modules[module_name] = module
            self.handlers[name] = getattr(modules[module_name], function_name)

    async def call(self, host, name, arguments):
        Draft202012Validator(self.specs[name]["parameters"]).validate(arguments)
        handler = self.handlers[name]
        inspect.signature(handler).bind(host, **arguments)
        return await handler(host, **arguments)


async def screen(host, question, region, magnify):
    path = host.record.folder / ("screen-" + uuid.uuid4().hex + ".png")
    metadata = await asyncio.to_thread(desktop.capture, path, region, magnify)
    detail = await host.models.gemma(
        question + "\nSupplied image mapping: " + encode(metadata) +
        "\nReport only the requested visible facts concisely. Use image pixels, then the supplied mapping for desktop coordinates. "
        "Separate observed, inferred, and unknown. Do not assume hidden content or conventional positions.", [path],
    )
    return {**metadata, "gemma": detail}


async def images(host, question, views):
    metadata = []
    for view in views:
        path = host.record.folder / ("view-" + uuid.uuid4().hex + ".png")
        metadata.append(await asyncio.to_thread(desktop.image_view, view, path))
    detail = await host.models.gemma(question + "\nActual supplied images and coordinate mappings:\n" + encode(metadata) +
                                    "\nSeparate visible evidence, inference, and unknown. Answer only the requested detail concisely.",
                                    [Path(item["image"]) for item in metadata])
    return {"views": metadata, "gemma": detail}


async def click(host, x, y, button, count):
    return desktop.click(x, y, button, count)


async def drag(host, points, seconds):
    return await asyncio.to_thread(desktop.stroke, points, seconds)


async def type_text(host, text):
    return desktop.type_text(text)


async def keys(host, chord):
    return desktop.keys(chord)


async def scroll(host, amount):
    return desktop.scroll(amount)


async def python(host, code):
    namespace = {"host": host, "ROOT": ROOT, "asyncio": asyncio}
    function = "async def action():\n" + "\n".join("    " + line for line in code.splitlines())
    exec(compile(function, "<luna-python>", "exec"), namespace)
    return await namespace["action"]()


async def chat(host, text):
    return await host.line.chat(text)


async def dial(host):
    return await host.line.dial()


async def answer(host):
    return await host.line.answer()


async def hang(host):
    return await host.line.hang()


async def speak(host, parts):
    if host.line.state != "up":
        raise RuntimeError("Speech requires a connected Telegram call")
    payload = await host.models.voice(parts[0])
    pending = None
    receipts = []
    try:
        for index, text in enumerate(parts):
            path = host.record.folder / ("voice-" + uuid.uuid4().hex + ".wav")
            path.write_bytes(payload)
            host.record.append("voice_ready", {"audio": str(path), "words": text})
            if index + 1 < len(parts):
                pending = asyncio.create_task(host.models.voice(parts[index + 1]))
            result = await host.line.speak(call_pcm(payload))
            receipt = {**result, "audio": str(path), "words": text}
            receipts.append(receipt)
            host.record.append("speech_transmitted", receipt)
            if pending is not None:
                payload = await pending
                pending = None
        return {"parts": receipts}
    finally:
        if pending is not None:
            pending.cancel()
            try:
                await pending
            except asyncio.CancelledError:
                pass


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
    host.state["recording"] = False
    host.state["shutdown"] = shutdown
    return host.state["finished"]
