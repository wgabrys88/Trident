"""One process, one queue, one turn at a time.

    python trident.py           run until Ctrl+C. Log to the console and state/trident.log.
    python trident.py say TEXT  give the running process a written line
    python trident.py call      ring him
    python trident.py hang      hang up
    python trident.py stop      shut down

Each turn is a Telegram message, his speech on the Telegram call, a written line, or one idle tick. The idle tick fires after brain.idle_after quiet seconds, and only while the call is down, she has not promised to stay quiet, and the queue is empty.
The brain may use tools. Words it finishes with are spoken on the call when the line is up, and sent to the chat when the turn was a message and the line is down.
"""

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
    "When you report a chat, say each message in full and who wrote it. A home page is a greeting, a menu, and chips, not messages. A sidebar title is not a message. The year is the year on the clock.\n"
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
        reply = self.brain.think(SYSTEM + self.memory.facts_block(), self.tools(kind), self.memory.history(), f"{situation}\n{text}")
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

        def look(question: str = "What is on the screen?"):
            png = eyes.screenshot()
            titles = eyes.window_titles()
            seen = self.brain.bubbles(png)
            clock = self.brain.see(eyes.corner(png), "Copy the date and four-digit year exactly as printed.")
            self.line.send_photo(png, seen[:200])
            return {"screen": seen, "clock": clock, "windows": "; ".join(titles)}

        def find(target: str):
            boxes = self.brain.locate(eyes.screenshot(), target)
            return eyes.center_px(boxes[0]["box_2d"]) if boxes else None

        def click(target: str, how: str = "left"):
            if not asked:
                return "nobody asked for this"
            point = find(target)
            if point is None:
                return f"{target} is not on the screen"
            hands.click(*point, how)
            return f"{how} click on {target} at {point[0]} {point[1]}"

        def drag(source: str, destination: str):
            if not asked:
                return "nobody asked for this"
            a, b = find(source), find(destination)
            if a is None or b is None:
                return f"{source if a is None else destination} is not on the screen"
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
            "look": Tool("look", f"Look at the screen. A chat returns each line and who wrote it. A home page returns the greeting, the menu, and the chips. Also the clock and the window titles. The picture is also sent to {OWNER}'s chat.", {"question": {"description": "What to look for, e.g. Is Paint open?", "type": "STRING"}}, look),
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
