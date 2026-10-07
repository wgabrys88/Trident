import asyncio
import json
from pathlib import Path

from jsonschema import Draft202012Validator

import desktop
from audio import call_pcm
from store import CONFIG, ROOT, cancel, read, write


class Catalog:
    def __init__(self):
        self.document = read(ROOT / "tools.json")
        self.specs = self.document["tools"]

    def validate(self, schema, value):
        Draft202012Validator({"$defs": self.document["$defs"], **schema}).validate(value)

    async def call(self, host, tool, arguments, observation=None):
        spec = self.specs[tool]
        self.validate(spec["parameters"], arguments)
        if spec["observe"]:
            self.validate({"$ref": "#/$defs/observation"}, observation)
        elif observation is not None:
            raise ValueError("This tool does not take an observation")
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
            result = {"execution": result, "observation": await visual(host, observation, [await asyncio.to_thread(desktop.capture, observation["region"])])}
        return result


async def visual(host, observation, prepared):
    seen = await host.models.look(read(ROOT / "mind.json")["student"], {
        "expected": observation["expected"], "question": observation["question"],
        "relation": observation["relation"], "scene": host.state["scene"],
        "views": [{"index": index, "prepared": metadata["prepared"]} for index, (_, metadata) in enumerate(prepared)],
    }, [payload for payload, _ in prepared])
    parsed = json.loads(seen["text"])
    report, scene, marks = parsed["report"], parsed["scene"], parsed["marks"]
    if not isinstance(report, str) or not report.strip():
        raise RuntimeError("LFM report is empty")
    if not isinstance(scene, str) or not scene.strip() or len(scene) > CONFIG["lfm"]["scene_chars"]:
        raise RuntimeError("LFM scene is outside the rolling limit")
    if not isinstance(marks, list):
        raise RuntimeError("LFM marks must be a list")
    placed = []
    for mark in marks:
        label, index, box = mark["label"], mark["image_id"], mark["bbox_2d"]
        if not isinstance(label, str) or not label.strip():
            raise RuntimeError("LFM mark has no label")
        if type(index) is not int or not 0 <= index < len(prepared):
            raise RuntimeError("LFM mark names no supplied image")
        if [type(number) is int and 0 <= number <= 1000 for number in box] != [True, True, True, True] or box[0] >= box[2] or box[1] >= box[3]:
            raise RuntimeError("LFM mark is outside 0-1000")
        placed.append({"label": label, "image_id": index, "bbox_2d": box, "desktop": desktop.place(box, prepared[index][1])})
    host.state["scene"] = scene
    return {
        "views": [{**metadata, "image": path} for (_, metadata), path in zip(prepared, seen["images"])],
        "report": report, "scene": scene, "marks": placed,
    }


async def screen(host, expected, question, region, relation):
    observation = {"expected": expected, "question": question, "region": region, "relation": relation}
    return await visual(host, observation, [await asyncio.to_thread(desktop.capture, region)])


async def images(host, expected, question, relation, views):
    observation = {"expected": expected, "question": question, "region": None, "relation": relation}
    prepared = [await asyncio.to_thread(desktop.image_view, view) for view in views]
    return await visual(host, observation, prepared)


async def python(host, code):
    namespace = {"host": host, "ROOT": ROOT, "asyncio": asyncio}
    function = "async def action():\n" + "\n".join("    " + line for line in code.splitlines())
    exec(compile(function, "<luna-python>", "exec"), namespace)
    return await namespace["action"]()


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
