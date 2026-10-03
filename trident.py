"""One process, one queue, one turn at a time.

    python trident.py           run until Ctrl+C. Log to the console and state/trident.log.
    python trident.py say TEXT  give the running process a written line
    python trident.py call      ring him
    python trident.py hang      hang up
    python trident.py stop      shut down

Each turn is a Telegram message, his speech on the Telegram call, a written line, or one idle tick. The idle tick fires after brain.idle_after quiet seconds, and only while the call is down, she has not promised to stay quiet, and the queue is empty.
The brain may use tools. Words it finishes with are spoken on the call when the line is up, and sent to the chat when the turn was a message and the line is down.
"""

import json
import queue
import sys
import threading
import time

from organs import CONFIG, log, state_dir
from organs.brain import Brain, Tool
from organs.ears import transcribe, write_wav
from organs.memory import Memory
from organs.mouth import Mouth, pcm48
from organs.telegram import Line

LOG = log("trident")
OWNER = CONFIG["owner"]["name"]
INBOX = state_dir() / "inbox.txt"

SYSTEM = (
    f"You are Gemma, the mind of {OWNER}'s computer. You see the screen and act with tools.\n"
    "Each turn: think in two or three short sentences, then either call exactly one tool or say one or two short sentences in English. "
    "He hears only what you say, never the thought.\n"
    "Do the screen work he asked for. Use look before you touch the screen, and name screen elements by the text written on them. "
    "Open a program with run. After a page opens or a message is sent, run Start-Sleep, then look again before you act.\n"
    "A command is run. A key is press. When he says to click an element, call click with that name before you type.\n"
    "When he asks you to type, the type_text text is that sentence copied unchanged. "
    "Example: he says type The note says reply with exactly the word maple. The text is The note says reply with exactly the word maple.\n"
    "When you report the screen, say the area name, whether that area and its items are visible, and what the crop shows. A sidebar title is not a message. The year is the year on the clock.\n"
    "Ring him or send him a message only when he asks. When he says goodbye or asks you to stop, use hang_up and say nothing."
)
SOURCE = {"chat": "a Telegram message from him", "call": "his voice on the call", "typed": "a line he typed on the computer", "idle": "nobody; an idle moment"}


class Trident:
    def __init__(self):
        self.brain = Brain()
        self.mouth = Mouth()
        self.memory = Memory()
        self.events: queue.Queue = queue.Queue()
        self.stopping = threading.Event()
        self.last_activity = time.monotonic()
        self.idle_sent = False
        self.line = Line(on_text=lambda t: self.push("chat", t), on_utterance=lambda c: self.push("call", c), on_line=lambda _state: self.touch())

    # ------------------------------------------------------------ life

    def start(self):
        self.brain.start()
        self.mouth.load()
        self.line.start()
        threading.Thread(target=self.worker, name="trident-worker", daemon=True).start()
        threading.Thread(target=self.inbox_loop, name="trident-inbox", daemon=True).start()
        threading.Thread(target=self.idle_loop, name="trident-idle", daemon=True).start()
        LOG.info("trident up")

    def stop(self):
        self.stopping.set()
        self.line.stop()
        self.brain.stop()
        LOG.info("trident down")

    def touch(self):
        self.last_activity = time.monotonic()
        self.idle_sent = False

    def push(self, kind: str, payload):
        self.touch()
        self.events.put((kind, payload))

    # ------------------------------------------------------------ senses

    def inbox_loop(self):
        while not self.stopping.wait(0.5):
            if not INBOX.is_file():
                continue
            lines = [l.strip() for l in INBOX.read_text(encoding="utf-8").splitlines() if l.strip()]
            INBOX.unlink()
            for line in lines:
                self.push("typed", line)

    def idle_loop(self):
        while not self.stopping.wait(1.0):
            quiet_for = time.monotonic() - self.last_activity
            if quiet_for >= CONFIG["brain"]["idle_after"] and not self.idle_sent and not self.memory.quiet and not self.line.up and self.events.empty():
                self.idle_sent = True
                self.events.put(("idle", "Nothing has happened for a while. Look if you are curious, ring him only for a reason, otherwise answer with the single word idle."))

    def worker(self):
        while not self.stopping.is_set():
            try:
                kind, payload = self.events.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.handle(kind, payload)
            except Exception as exc:
                LOG.exception("turn failed: %s", exc)

    # ------------------------------------------------------------ one event

    def handle(self, kind: str, payload):
        if kind == "call":
            text, language = transcribe(write_wav(state_dir() / "call.wav", payload))
            LOG.info("heard (%s, %s): %s", kind, language, text)
            if not text:
                return
        else:
            text = payload
        if kind == "typed" and text.startswith("/"):
            return self.command(text[1:])
        if kind != "idle":
            self.memory.set_quiet(False)
        reply = self.turn(kind, text)
        self.deliver(kind, reply)

    def command(self, name: str):
        if name == "call":
            self.line.dial()
            self.speak("I am up. Do you want anything?")
        elif name == "hang":
            self.line.hang()
        elif name == "stop":
            self.stopping.set()

    def turn(self, kind: str, text: str) -> str:
        situation = f"[{time.strftime('%H:%M')} | line {self.line.state} | heard from: {SOURCE[kind]}]"
        reply = self.brain.think(
            SYSTEM + self.memory.facts_block(),
            self.tools(kind),
            self.memory.history(),
            f"{situation}\n{text}",
            on_step=lambda step: self.line.send_text(f"{step.thought}\n{step.tool}\n{json.dumps(step.args, ensure_ascii=False)}"),
        )
        if reply.text and reply.text.lower().strip(".") != "idle":
            self.memory.add_turn(text, reply.text)
            return reply.text
        return ""

    def deliver(self, kind: str, text: str):
        if not text:
            return
        if self.line.up:
            self.speak(text)
        elif kind == "chat":
            self.line.send_text(text)

    def speak(self, text: str):
        self.line.speak(pcm48(self.mouth.say(text), self.mouth.sr))

    # ------------------------------------------------------------ what Gemma can do

    def tools(self, kind: str) -> dict[str, Tool]:
        from organs import eyes, hands

        asked = kind != "idle"

        def shoot(png, need, wrong):
            told = f"You may look at the whole desktop and name one area. {need}"
            if wrong:
                told += f" The previous box {int(wrong['y0'])} {int(wrong['x0'])} {int(wrong['y1'])} {int(wrong['x1'])} was a different element. Name a different area."
            told += " Include the whole element and a margin so none of its text is cut off."
            self.line.send_photo(png, need[:200])
            named = self.brain.area(png, told)
            box = [named["y0"], named["x0"], named["y1"], named["x1"]]
            piece = eyes.crop(png, box)
            self.line.send_photo(piece, named["name"][:200])
            return named, box, piece

        def yes(piece, label):
            return self.brain.see(piece, f'Is this crop the "{label}" element, and not a different element? Answer yes or no.', 16).lower().startswith("yes")

        def widen(png, named, box, label):
            wide = [max(0, min(box[0], box[2]) - 100), max(0, min(box[1], box[3]) - 100), min(1000, max(box[0], box[2]) + 100), min(1000, max(box[1], box[3]) + 100)]
            wide_png = eyes.crop(png, wide)
            self.line.send_photo(wide_png, named["name"][:200])
            again = self.brain.area(wide_png, f"You may look at this crop and name one area. The element is {label}. Include the whole element and a margin so none of its text is cut off.")
            boxed = eyes.inside(wide, [again["y0"], again["x0"], again["y1"], again["x1"]])
            piece = eyes.crop(png, boxed)
            self.line.send_photo(piece, again["name"][:200])
            return again, boxed, piece

        def look(question: str = "What is on the screen?"):
            png = eyes.screenshot()
            titles = eyes.window_titles()
            area, visible, work, wrong = "", False, "rejected", None
            for _ in range(3):
                named, box, piece = shoot(png, question, wrong)
                if not yes(piece, named["name"]):
                    named, box, piece = widen(png, named, box, question)
                    if not yes(piece, named["name"]):
                        wrong = named
                        continue
                work = self.brain.see(piece, f"Answer only from this crop of {named['name']}. {question} One or two sentences.", 80)
                self.line.send_photo(piece, work[:200])
                area, visible = named["name"], True
                break
            corner = eyes.corner(png)
            clock = self.brain.see(corner, "Copy the date and four-digit year exactly as printed.")
            self.line.send_photo(corner, clock[:200])
            return {"area": area or (wrong["name"] if wrong else ""), "visible": visible, "crop": work, "clock": clock, "windows": "; ".join(titles)}

        def find(target: str):
            png = eyes.screenshot()
            wrong = None
            for _ in range(3):
                named, box, piece = shoot(png, f"The element is {target}.", wrong)
                if not yes(piece, target):
                    named, box, piece = widen(png, named, box, target)
                    if not yes(piece, target):
                        wrong = named
                        continue
                self.line.send_photo(piece, target[:200])
                spot = self.brain.locate(piece, f'Point at the center of the icon for "{target}", not the word under it.')
                return eyes.point_px(box, spot["y"], spot["x"])
            return None

        def click(target: str, how: str = "left"):
            if not asked:
                return "nobody asked for this"
            point = find(target)
            if point is None:
                return f"rejected {target}"
            hands.click(*point, how)
            return f"{how} click on {target} at {point[0]} {point[1]}"

        def drag(source: str, destination: str):
            if not asked:
                return "nobody asked for this"
            a = find(source)
            if a is None:
                return f"rejected {source}"
            b = find(destination)
            if b is None:
                return f"rejected {destination}"
            hands.drag(*a, *b)
            return f"dragged {source} to {destination}"

        def type_text(text: str):
            if not asked:
                return "nobody asked for this"
            hands.type_text(text)
            return "typed"

        def press(keys: str):
            if not asked:
                return "nobody asked for this"
            hands.press(keys)
            return f"pressed {keys}"

        def run(command: str):
            return hands.run(command) if asked else "nobody asked for this"

        def remember(fact: str):
            self.memory.remember(fact)
            return "remembered"

        def call_owner(opening: str = "I am up. Do you want anything?"):
            if self.line.up:
                return "the line is already up; just speak"
            if self.memory.quiet:
                return "you promised to stay quiet until he speaks"
            try:
                self.line.dial()
            except Exception as exc:
                return f"he did not answer: {exc}"
            self.speak(opening)
            return "he answered and heard the opening; now say what he should hear next, or hang_up"

        def hang_up():
            self.line.hang()
            return "hung up"

        def send_message(text: str):
            self.line.send_text(text)
            return "sent"

        def stay_quiet(reason: str):
            self.memory.set_quiet(True)
            return "quiet until he speaks"

        return {
            "look": Tool("look", f"Look at the screen. Names one area on the desktop, then answers only on that crop. A wrong crop is rejected and the area is named again. Also the clock and the window titles. Each picture is also sent to {OWNER}'s chat.", {"question": {"description": "What to look for.", "type": "STRING"}}, look),
            "click": Tool("click", "Click one element on the screen, named by its visible text or look, e.g. the Start button, the OK button, the File menu.", {"target": {"description": "The element to click.", "type": "STRING"}, "how": {"description": "Kind of click.", "type": "STRING", "enum": ["left", "right", "double"]}}, click, optional=("how",)),
            "drag": Tool("drag", "Drag from one screen element to another.", {"source": {"description": "Where the drag starts.", "type": "STRING"}, "destination": {"description": "Where the drag ends.", "type": "STRING"}}, drag),
            "type_text": Tool("type_text", "Type the text argument exactly, every word of it, where the cursor is.", {"text": {"description": "The text to type, every word.", "type": "STRING"}}, type_text),
            "press": Tool("press", "Press keyboard keys only, for example enter, escape, tab, or ctrl-a. Never a command.", {"keys": {"description": "The key or chord.", "type": "STRING"}}, press),
            "run": Tool("run", "Run one PowerShell command. Start-Process opens a program. Start-Sleep -Seconds N waits.", {"command": {"description": "The PowerShell command.", "type": "STRING"}}, run),
            "remember": Tool("remember", "Keep one short fact for later turns.", {"fact": {"description": "The fact.", "type": "STRING"}}, remember),
            "call_owner": Tool("call_owner", f"Ring {OWNER} on Telegram. When he answers, the opening is spoken to him first.", {"opening": {"description": "The first sentence he hears.", "type": "STRING"}}, call_owner, optional=("opening",)),
            "hang_up": Tool("hang_up", "End the call. Say nothing after it.", {}, hang_up, final=True),
            "send_message": Tool("send_message", f"Send {OWNER} a Telegram text message.", {"text": {"description": "The message.", "type": "STRING"}}, send_message),
            "stay_quiet": Tool("stay_quiet", "Promise not to ring until he speaks to you again.", {"reason": {"description": "Why.", "type": "STRING"}}, stay_quiet),
        }


def main():
    args = sys.argv[1:]
    if args and args[0] in ("say", "call", "hang", "stop"):
        line = " ".join(args[1:]) if args[0] == "say" else "/" + args[0]
        with INBOX.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return
    trident = Trident()
    try:
        trident.start()
        while not trident.stopping.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        trident.stop()


if __name__ == "__main__":
    main()
