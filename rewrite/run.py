"""Start from the checkout root: artifacts\python\Scripts\python.exe rewrite\run.py"""

import asyncio
import sys
import tomllib
from datetime import datetime
from pathlib import Path

from channel import Channel
from ear import Ear
from mind import Mind, disposition
from mouth import Mouth, pcm48


def load():
    here = Path(__file__).resolve().parent
    return here.parent, tomllib.loads((here / "config.toml").read_text(encoding="utf-8"))


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
    mind = Mind(cfg["luna"], life, run_dir)
    idle = cfg["life"]["idle_seconds"]
    images = cfg["life"]["recent_images"]
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
            answer, tools = await mind.turn(task, "\n\n".join(transcript), channel.send, images)
            transcript.append("Task:\n" + task)
            if tools:
                transcript.append("Tools:\n" + "\n".join(tools))
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
        for stop in (mouth.stop, ear.stop, channel.stop):
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
