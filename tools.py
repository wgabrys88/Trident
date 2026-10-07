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
            if not isinstance(observation, dict):
                raise RuntimeError(tool + " requires observation")
            self.validate({"$ref": "#/$defs/observation"}, observation)
        elif observation is not None:
            raise ValueError("This tool does not take an observation")
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
                result = {"execution": result, "observation": await visual(host, observation, [await asyncio.to_thread(desktop.capture, observation["region"])])}
            return result
        finally:
            host.acting = False
            host.seen = await asyncio.to_thread(desktop.sample)


LOOK = {
    "type": "object", "additionalProperties": False, "required": ["report", "scene", "marks"],
    "properties": {
        "report": {"type": "string"}, "scene": {"type": "string"},
        "marks": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["label", "bbox_2d"],
            "properties": {
                "label": {"type": "string"},
                "bbox_2d": {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "number"}},
                "image_id": {"type": "integer"},
            },
        }},
    },
}


async def visual(host, observation, prepared):
    seen = await host.models.look(read(ROOT / "mind.json")["student"], {
        "keys": "report, scene, marks",
        "expected": observation["expected"], "question": observation["question"],
        "relation": observation["relation"], "scene": host.state["scene"],
        "views": [{"index": index} for index in range(len(prepared))],
    }, [payload for payload, _ in prepared], LOOK)
    parsed = json.loads(seen["text"])
    if not isinstance(parsed, dict) or any(key not in parsed for key in ("report", "scene", "marks")):
        raise RuntimeError("LFM omitted report, scene, or marks")
    report, scene, marks = parsed["report"], parsed["scene"], parsed["marks"]
    if not isinstance(report, str) or not report.strip():
        raise RuntimeError("LFM report is empty")
    if not isinstance(scene, str) or not scene.strip() or len(scene) > CONFIG["lfm"]["scene_chars"]:
        raise RuntimeError("LFM scene is outside the rolling limit")
    if not isinstance(marks, list):
        raise RuntimeError("LFM marks must be a list")
    placed = []
    for mark in marks:
        if not isinstance(mark, dict):
            raise RuntimeError("LFM mark is not an object")
        label, box = mark["label"], norm_box(mark["bbox_2d"])
        if not isinstance(label, str) or not label.strip():
            raise RuntimeError("LFM mark has no label")
        if len(prepared) == 1:
            index = 0
        else:
            index = mark["image_id"]
            if type(index) is not int or not 0 <= index < len(prepared):
                raise RuntimeError("LFM mark names no supplied image")
        placed.append({"label": label, "image_id": index, "bbox_2d": box, "desktop": desktop.place(box, prepared[index][1])})
    host.state["scene"] = scene
    return {
        "views": [{**metadata, "image": path} for (_, metadata), path in zip(prepared, seen["images"])],
        "report": report, "scene": scene, "marks": placed,
    }


def norm_box(box):
    if not isinstance(box, list) or len(box) != 4:
        raise RuntimeError("LFM bbox_2d is not four numbers in 0-1000")
    numbers = []
    for number in box:
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not 0 <= number <= 1000:
            raise RuntimeError("LFM bbox_2d is not four numbers in 0-1000")
        numbers.append(int(round(number)))
    if numbers[0] >= numbers[2] or numbers[1] >= numbers[3]:
        raise RuntimeError("LFM bbox_2d is not four numbers in 0-1000")
    return numbers


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
