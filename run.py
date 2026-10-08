"""Start from the checkout root: artifacts\python\Scripts\python.exe run.py [mind]"""

import asyncio
import importlib
import sys
import tomllib
from datetime import datetime
from pathlib import Path

import tools
from channel import Channel
from ear import Ear
from mouth import Mouth, pcm48

MARKERS = {"quiet", "write", "call", "hang", "again"}


def load():
    here = Path(__file__).resolve().parent
    return here, tomllib.loads((here / "config.toml").read_text(encoding="utf-8"))


def open_mind():
    if len(sys.argv) > 2:
        raise RuntimeError("Usage: run.py [mind]")
    return importlib.import_module("mind_" + (sys.argv[1] if len(sys.argv) > 1 else "luna"))


def disposition(answer):
    lines = (answer or "").splitlines()
    flags = []
    while lines and lines[-1].strip() in MARKERS:
        flags.append(lines.pop().strip())
    return "\n".join(lines).strip(), flags


def pictures(life, count):
    found = []
    folder = Path(life)
    if int(count) <= 0 or not folder.is_dir():
        return found
    for path in folder.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            found.append(path)
    found.sort(key=lambda item: item.stat().st_mtime)
    return found[-int(count):]


def picture(up, items):
    lines = ["Call is up." if up else "Call is down."]
    for kind, text in items:
        if kind == "wrote":
            lines.append("Wojciech wrote:\n" + text)
        elif kind == "said":
            lines.append("Wojciech said:\n" + text)
        else:
            lines.append(text)
    return "\n".join(lines)


async def take(channel, ear, timeout):
    loop = asyncio.get_running_loop()
    deadline = None if timeout is None else loop.time() + timeout
    found = []
    while True:
        if channel.trouble:
            raise channel.trouble.pop(0)
        while channel.inbox:
            found.append(channel.inbox.pop(0))
        while channel.mic:
            piece = channel.mic.pop(0)
            audio = ear.flush() if piece is None else None
            pieces = ear.cut(piece) if piece is not None else ([audio] if audio else [])
            for utterance in pieces:
                if not utterance:
                    continue
                text = (await ear.transcribe(utterance)).strip()
                if text:
                    found.append(("said", text))
        while channel.inbox:
            found.append(channel.inbox.pop(0))
        if found:
            return found
        if deadline is not None and loop.time() >= deadline:
            return None
        channel.arrived.clear()
        if channel.inbox or channel.mic or channel.trouble:
            continue
        if deadline is None:
            await channel.arrived.wait()
            continue
        try:
            await asyncio.wait_for(channel.arrived.wait(), max(0, deadline - loop.time()))
        except TimeoutError:
            pass


async def live():
    root, cfg = load()
    here = Path(__file__).resolve().parent
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    life = here / "life" / stamp
    run_dir = here / f"RUN_{stamp}"
    life.mkdir(parents=True)
    run_dir.mkdir(parents=True)
    channel = Channel(
        {"telegram_id": cfg["owner"]["telegram_id"], "tdata": cfg["telegram"]["tdata"]},
        here / "session" / stamp / "telegram",
    )
    ear = Ear(root, cfg["ears"])
    mouth = Mouth(root, cfg["voice"])
    mind = open_mind()
    idle = cfg["life"]["idle_seconds"]
    image_count = cfg["life"]["recent_images"]
    transcript = []
    again = False
    try:
        await channel.start()
        await ear.start()
        await mouth.start()
        mind.check()
        while True:
            if channel.trouble:
                raise channel.trouble.pop(0)
            if again:
                items = await take(channel, ear, 0)
                again = False
                idle_line = "The work is unfinished."
            else:
                items = await take(channel, ear, idle)
                idle_line = "Nothing has arrived."
            up = channel.state == "up"
            if items:
                task = picture(up, items)
            else:
                task = ("Call is up.\n" if up else "Call is down.\n") + idle_line
            answer, seen = await mind.turn(
                task, "\n\n".join(transcript), channel.send, tools.act, tools.card(),
                life, run_dir, pictures(life, image_count),
            )
            transcript.append("Task:\n" + task)
            if seen:
                transcript.append("Tools:\n" + "\n".join(seen))
            transcript.append("Answer:\n" + answer)
            body, flags = disposition(answer)
            deliver = bool(body) and "quiet" not in flags
            wav = await mouth.wav(body) if deliver else None
            spoken = False
            if deliver and channel.state == "up":
                spoken = await channel.play(pcm48(wav))
            if "hang" in flags and channel.state == "up":
                await channel.hang()
            if "call" in flags and channel.state != "up":
                answered = await channel.dial()
                transcript.append("He answered." if answered else "He did not answer.")
            if deliver and channel.state == "up" and not spoken:
                spoken = await channel.play(pcm48(wav))
            if deliver and not spoken:
                await channel.send_wav(wav)
            if deliver:
                await channel.send(body)
            again = "again" in flags
    finally:
        stop_error = None
        stops = [mouth.stop, ear.stop, channel.stop]
        extra = getattr(mind, "stop", None)
        if extra:
            stops.append(extra)
        for stop in stops:
            try:
                await stop()
            except Exception as error:
                stop_error = stop_error or error
        if stop_error is not None and sys.exc_info()[1] is None:
            raise stop_error


if __name__ == "__main__":
    try:
        asyncio.run(live())
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
