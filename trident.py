import asyncio
import os
import sys
from pathlib import Path


async def main(folder):
    from agent import Agent
    from line import Line
    from models import Models
    from store import ROOT, cancel, read, save

    folder = Path(folder)
    state = read(folder / "session.json", {
        "life": folder.name, "task": None, "history": [], "receipts": [], "results": {},
        "waiting": False, "shutdown": False, "repair": None, "attention": True,
        "assessment": None, "owner_cursor": read(ROOT / "runs" / "owner.json", {}).get("cursor"),
        "audio_pending": {}, "open_work": read(ROOT / "runs" / "memory.json", []),
        "arrived": 0, "owner_sequence": 0,
    })
    if "arrived" not in state:
        state["arrived"] = 0
        state["owner_sequence"] = 0
    if "results" not in state:
        state["results"] = {}
        state["receipts"] = []
    state["audio_pending"] = {}
    state["attention"] = True
    state["waiting"] = False
    save(folder, state)
    line = Line(asyncio.Queue(), folder, state)
    models = Models(folder, line.emit)
    agent = Agent(models, line, folder, state)
    startup = [asyncio.create_task(line.open()), asyncio.create_task(line.hearing.open()),
               asyncio.create_task(models.open())]
    try:
        await asyncio.gather(*startup)
        await agent.serve()
    finally:
        await cancel(*startup)
        errors = []
        for operation in (line.hearing.close, models.close, line.close):
            try:
                await operation()
            except Exception as error:
                errors.append(error)
        while not line.inbox.empty():
            try:
                await agent.receive(line.inbox.get_nowait())
            except Exception as error:
                errors.append(error)
        agent.save()
        if errors and sys.exc_info()[0] is None:
            raise errors[0]
    if state["repair"]:
        return 75
    return 0 if state["shutdown"] else 1


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
