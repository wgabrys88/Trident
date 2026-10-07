import asyncio
import os
import sys
import uuid
from contextlib import AsyncExitStack
from pathlib import Path

from agent import Agent
from line import Line
from models import Models
from store import ROOT, Record, read, write
from tools import announce

OPENING = "This is Trident. Startup is finished. I can hear you. Tell me the task."


async def main():
    os.chdir(ROOT)
    folder = ROOT / "runs" / uuid.uuid4().hex if len(sys.argv) == 1 else Path(sys.argv[1])
    record = Record(folder)
    if len(sys.argv) == 1:
        state = {
            "goal": None, "suspended": [], "scene": "", "history": [], "lessons": [], "finished": None,
            "waiting": False, "shutdown": False, "restart": False, "start": 0,
            "attention": False, "recording": False, "open_work": read(ROOT / "mind.json")["open_work"], "assessment": None,
        }
    else:
        state = read(folder / "session.json")
        state["restart"] = False
        if "scene" not in state:
            state["scene"] = ""
    record.append("boot", {"resumed": len(sys.argv) != 1})
    write(folder / "session.json", state)
    line = Line(asyncio.Queue(), record)
    models = Models(record, line.inbox)
    try:
        await models.open()
        await line.hearing.open()
        await line.open()
        if len(sys.argv) == 1:
            await announce(models, line, OPENING)
        await Agent(models, line, record, state).serve()
    except Exception as error:
        record.append("error", {"type": type(error).__name__, "message": str(error)})
        raise
    finally:
        record.append("cleanup_started", {})
        try:
            async with AsyncExitStack() as cleanup:
                for resource in (models, line.hearing, line):
                    cleanup.push_async_callback(resource.close)
            record.append("cleanup_finished", {})
            if sys.exception() is None:
                record.append("restart" if state["restart"] else "shutdown", {})
        finally:
            try:
                await record.close()
            finally:
                if line.client is not None:
                    await line.client.disconnect()
    return folder if state["restart"] else None


if __name__ == "__main__":
    try:
        restart = asyncio.run(main())
        if restart is not None:
            os.execv(sys.executable, [sys.executable, str(ROOT / "trident.py"), str(restart)])
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
