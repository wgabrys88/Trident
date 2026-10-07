import asyncio
import os
import sys
from pathlib import Path


async def main(folder):
    from agent import Agent
    from line import Line
    from models import Models
    from store import ROOT, Record, activate, cancel, read, save

    record = Record(folder)
    state = read(record.folder / "session.json", {
        "life": record.folder.name, "task": None, "history": [], "receipts": [],
        "waiting": False, "shutdown": False, "repair": None, "attention": True,
        "assessment": None, "owner_applied": -1,
        "owner_cursor": read(ROOT / "runs" / "owner.json", {}).get("cursor"), "audio_pending": {},
        "open_work": read(ROOT / "runs" / "memory.json", []),
    })
    pending = {}
    for offset, event in record.events():
        value = event["value"]
        if event["kind"] == "owner_audio":
            pending[value["id"]] = {**value, "source_receipt": offset}
        elif event["kind"] == "asr_result" and not value["recognition"]["text"].strip():
            pending.pop(value["id"], None)
        elif event["kind"] == "owner_input":
            if "recognition" in value:
                pending.pop(value["id"], None)
            if offset > state.get("owner_applied", -1):
                if value.get("superseded"):
                    state["owner_applied"] = offset
                    continue
                source = "audio" if "recognition" in value else "owner"
                text = value["recognition"]["text"] if source == "audio" else value["text"]
                activate(state, text, source, offset, value.get("source_receipt", offset))
                if source == "owner":
                    state["owner_cursor"] = max(state["owner_cursor"] or 0, value["id"])
    state["audio_pending"] = pending
    state["attention"] = True
    state["waiting"] = False
    state["body_stopped"] = False
    record.append("boot", {"life": state["life"], "task": state["task"]})
    save(record.folder, state)
    line = Line(asyncio.Queue(), record, state)
    models = Models(record, line.emit)
    agent = Agent(models, line, record, state)
    cleanup_failed = False
    async def start(name, operation):
        try:
            await operation()
            line.emit("dependency_ready", {"resource": name})
        except Exception as error:
            line.emit("error", error)
    startup = [asyncio.create_task(start(name, operation)) for name, operation in (
        ("telegram", line.open), ("hearing", lambda: line.hearing.open(state["audio_pending"].values())),
        ("voice", models.open))]
    try:
        await agent.serve()
    finally:
        record.append("cleanup_started", {"life": state["life"]})
        await cancel(*startup)
        for operation in (line.stop_calls, line.hearing.close, models.close):
            try:
                await operation()
            except Exception as error:
                cleanup_failed = True
                record.append("cleanup_failure", {"resource": operation.__qualname__, "error": str(error)})
        while not line.inbox.empty():
            await agent.receive(line.inbox.get_nowait())
        state["body_stopped"] = not cleanup_failed
        record.append("cleanup_finished", {"failed": cleanup_failed})
        try:
            await line.close()
        except Exception as error:
            cleanup_failed = True
            state["body_stopped"] = False
            record.append("cleanup_failure", {"resource": "telegram", "error": str(error)})
        agent.save()
    if cleanup_failed:
        return 1
    if state["repair"]:
        record.append("repair_handoff", {"life": state["life"], "task": state["task"]})
        return 75
    record.append("shutdown" if state["shutdown"] else "resume_pending_input", {"life": state["life"]})
    return 0 if state["shutdown"] else 76


if __name__ == "__main__":
    if len(sys.argv) != 2 or os.environ.get("TRIDENT_LAUNCH") != str(Path(sys.argv[1]).resolve()):
        raise RuntimeError("Start Trident through launch.py")
    if sys.stdin.buffer.read() != b"start":
        raise RuntimeError("Supervisor startup handshake missing")
    try:
        sys.exit(asyncio.run(main(sys.argv[1])))
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
