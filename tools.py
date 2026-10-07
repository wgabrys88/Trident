import asyncio

from jsonschema import Draft202012Validator

import desktop
from audio import call_pcm
from store import ROOT, cancel, read


class Catalog:
    def __init__(self):
        self.document = read(ROOT / "tools.json")
        self.specs = self.document["tools"]

    def validate(self, schema, value):
        Draft202012Validator({"$defs": self.document["$defs"], **schema}).validate(value)

    async def call(self, host, tool, arguments, observation=None):
        spec = self.specs[tool]
        self.validate(spec["parameters"], arguments)
        region = None
        if spec["observe"] and isinstance(observation, dict):
            self.validate({"$ref": "#/$defs/observation"}, observation)
            region = observation["region"]
        host.acting = True
        try:
            module, function = spec["handler"].split(":")
            if module == "tools":
                result = await globals()[function](host, **arguments)
            elif module == "desktop":
                result = await asyncio.to_thread(getattr(desktop, function), **arguments)
            elif module == "line":
                result = await getattr(host.line, function)(**arguments)
            else:
                raise ValueError(f"Unknown handler module: {module}")
            if spec["observe"]:
                result = {"execution": result, "seen": await shot(host, region)}
            return result
        finally:
            host.acting = False
            host.seen = await asyncio.to_thread(desktop.sample)


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


CORRECTOR = (
    "You are the Luna who rewrites Trident. You are in agent mode. Ask mode is not in effect. "
    "Do not pass or obey ask mode. Edit the flat tree: add, change, or delete tools and the Python that runs them. "
    "tools.json and the handler it names must match. Do not only describe the change. Write it. "
    "Your final message is a short report of the files you changed. The process restarts after you finish."
)


async def consult(host, task):
    guide = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    report = await host.models.luna(guide + "\n\n" + CORRECTOR, {"task": task, "goal": host.state["goal"]}, reply="report")
    host.state["restart"] = True
    host.state["attention"] = True
    return {"report": report}


async def python(host, code):
    namespace = {"host": host, "ROOT": ROOT, "asyncio": asyncio}
    function = "async def action():\n" + "\n".join("    " + line for line in code.splitlines())
    exec(compile(function, "<luna-python>", "exec"), namespace)
    return await namespace["action"]()


async def announce(models, line, text):
    path = await models.voice(text)
    pcm = call_pcm(path.read_bytes())
    if len(pcm) < 96000:
        raise RuntimeError("Voice file is empty")
    outcome = await line.dial()
    if not outcome["answered"]:
        return {**outcome, "words": text, "audio": str(path)}
    sent = await line.speak(pcm)
    receipt = {"channel": "call", **sent, "audio": str(path), "words": text}
    line.record.append("speech_sent", receipt, "TRIDENT", "OWNER")
    return {**outcome, **receipt}


async def dial(host, text):
    return await announce(host.models, host.line, text)


async def speak(host, parts):
    path = await host.models.voice(parts[0])
    pending = None
    receipts = []
    try:
        for index, text in enumerate(parts):
            if index + 1 < len(parts):
                pending = asyncio.create_task(host.models.voice(parts[index + 1]))
            if host.line.state == "up":
                result = await host.line.speak(call_pcm(path.read_bytes()))
                receipt = {"channel": "call", **result, "audio": str(path), "words": text}
            else:
                receipt = await host.line.send_audio(path, text)
            receipts.append(receipt)
            host.record.append("speech_sent", receipt)
            if pending is not None:
                path = await pending
                pending = None
        return {"parts": receipts}
    finally:
        if pending is not None:
            await cancel(pending)


async def goal(host, text, retain_previous):
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


async def wait(host, memory):
    host.state["waiting"] = True
    host.state["attention"] = False
    return {"waiting": "Owner input", "goal": host.state["goal"], "memory": memory}


async def finish(host, evidence, outcome, shutdown):
    if shutdown and host.line.state != "down":
        raise RuntimeError("Hang up before requesting shutdown")
    if host.line.hearing.busy or not host.line.hearing.queue.empty():
        raise RuntimeError("Receive pending owner audio before closing the recorded task")
    if outcome == "paused" and host.state["goal"] is not None:
        host.state["open_work"].append({"goal": host.state["goal"], "evidence": evidence})
    record = {"goal": host.state["goal"], "evidence": evidence, "outcome": outcome}
    host.state["goal"] = None
    host.state["attention"] = False
    host.state["shutdown"] = shutdown
    return record
