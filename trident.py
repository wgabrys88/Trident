import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

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
    "Do the screen work he asked for. Each look is one small step and you write its prompt. The prompt names the one thing this pass is about. The look returns that box. A pass does not list every element. "
    "Crop that box, then click or drag the x and y from the crop. After the click or the stroke, look. If it is not finished, crop what the screen shows and click or drag that. A whole-desktop point is not the box. If the tool says not clicked, not dragged, or not pressed, call crop next. "
    "When a look is not confident, the next call is look once more before you act.\n"
    "When he asks you to use a program's own controls, call survey first.\n"
    "Open a program with run. After a page opens or a message is sent, run Start-Sleep, then look again before you act.\n"
    "A command is run. A key he asked for is press. press does not click.\n"
    "When he asks you to type, the type_text text is that sentence copied unchanged. "
    "Example: he says type The note says reply with exactly the word maple. The text is The note says reply with exactly the word maple.\n"
    "Ring him or send him a message only when he asks. When he says goodbye or asks you to stop, use hang_up and say nothing.\n"
    "When he asks you not to speak, do the work and say nothing.\n"
    f"You are Gemma, the mind of {OWNER}'s computer. You see the screen and act with tools."
)
PASS = {"type": "object", "properties": {"answer": {"type": "string", "maxLength": 200}, "confident": {"type": "boolean"}, "y": {"type": "integer", "minimum": 0, "maximum": 1000}, "x": {"type": "integer", "minimum": 0, "maximum": 1000}, "y0": {"type": "integer", "minimum": 0, "maximum": 1000}, "x0": {"type": "integer", "minimum": 0, "maximum": 1000}, "y1": {"type": "integer", "minimum": 0, "maximum": 1000}, "x1": {"type": "integer", "minimum": 0, "maximum": 1000}}, "required": ["answer", "confident", "y", "x", "y0", "x0", "y1", "x1"]}


def cloud_look(png: bytes, question: str) -> dict:
    folder = state_dir() / "cloud"
    folder.mkdir(exist_ok=True)
    (folder / "look.png").write_bytes(png)
    version = max(p for p in (Path(os.environ["LOCALAPPDATA"]) / "cursor-agent" / "versions").iterdir() if (p / "node.exe").is_file())
    done = subprocess.run(
        [str(version / "node.exe"), str(version / "index.js"), "-p", "--mode", "ask", "--trust", "--model", CONFIG["cloud"]["model"], "--output-format", "text", "--workspace", str(folder), f"Read only look.png. Do not search or edit. The whole reply is one JSON object with keys answer, confident, y, x, y0, x0, y1, and x1. {question}"],
        capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=300, cwd=str(folder), creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if done.returncode != 0:
        raise RuntimeError((done.stderr or "").strip() or f"agent exit {done.returncode}")
    LOG.info("cloud %s", CONFIG["cloud"]["model"])
    return json.loads(done.stdout.strip())


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
        self.request = ""
        self.unsure = False
        self.cropped = False
        self.acted = False
        self.seen = []
        self.line = Line(on_text=lambda t: self.push("chat", t), on_utterance=lambda c: self.push("call", c), on_line=lambda _state: self.touch())

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

    def inbox_loop(self):
        while not self.stopping.wait(0.5):
            if not INBOX.is_file():
                continue
            raw = INBOX.read_bytes()
            lines = [l.strip() for l in raw.decode("utf-8").splitlines() if l.strip()]
            INBOX.unlink()
            self.line.send_file(raw, INBOX.name)
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

    def handle(self, kind: str, payload):
        if kind == "call":
            wav = write_wav(state_dir() / "call.wav", payload)
            self.line.send_file(wav.read_bytes(), wav.name)
            text, language = transcribe(wav)
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
        self.request = text
        self.cropped = False
        self.acted = False
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
        pieces = self.mouth.pieces(text)
        first = next(pieces)
        worker = threading.Thread(target=self.line.speak, args=(pcm48(first, self.mouth.sr),))
        worker.start()
        rest = list(pieces)
        worker.join()
        if rest:
            self.line.speak(pcm48(rest[0], self.mouth.sr))

    def tools(self, kind: str) -> dict[str, Tool]:
        from organs import eyes, hands

        asked = kind != "idle"

        def look(prompt: str, y0: int = -1, x0: int = -1, y1: int = -1, x1: int = -1):
            png = eyes.mark(eyes.screenshot(), self.seen)
            y0, x0, y1, x1 = (int(v) for v in (y0, x0, y1, x1))
            box = [y0, x0, y1, x1] if min(y0, x0, y1, x1) >= 0 else None
            if box:
                png = eyes.crop(png, box)
            cloud = self.unsure
            if cloud:
                png = eyes.shrink(png)
            self.line.send_photo(png, prompt)
            words = f"The answer is only the name. Box only {prompt}, tight around it. y and x are its center."
            LOG.info("look %s", "cloud" if cloud else "local")
            data = cloud_look(png, words) if cloud else self.brain.ask_json(words, PASS, png)
            got = [data["y0"], data["x0"], data["y1"], data["x1"]]
            self.seen.append((str(data["answer"])[:16], eyes.embed(box, got) if box else got))
            sure = data["confident"] is True
            self.unsure = not cloud and not sure
            if box:
                self.cropped = True
            clicking = "click" in self.request.lower() or "drag" in self.request.lower()
            found = {"answer": str(data["answer"]), "confident": sure, "y0": data["y0"], "x0": data["x0"], "y1": data["y1"], "x1": data["x1"]}
            if box:
                found["x"], found["y"] = eyes.point_px(box, (data["y0"] + data["y1"]) / 2, (data["x0"] + data["x1"]) / 2)
            elif not clicking:
                found["x"], found["y"] = eyes.center_px([data["y0"], data["x0"], data["y1"], data["x1"]])
            if not sure or (not box and clicking and not self.acted):
                found["next"] = "look once more" if not sure else "crop"
            return found

        def survey(prompt: str):
            png = eyes.shrink(eyes.mark(eyes.screenshot(), self.seen), CONFIG["vision"]["side"])
            self.line.send_photo(png, prompt)
            return self.brain.survey(png, prompt)

        def click(x: int, y: int, how: str = "left"):
            if not asked:
                return "nobody asked for this"
            if not self.cropped:
                return "not clicked. crop a small square on the edge the prompt names, then click the x and y from that crop"
            hands.click(int(x), int(y), how)
            self.acted, self.cropped = True, False
            return f"{how} click at {int(x)} {int(y)}. look. if it is not finished, crop what the screen shows and click that"

        def drag(x0: int, y0: int, x1: int, y1: int):
            if not asked:
                return "nobody asked for this"
            if not self.cropped:
                return "not dragged. call crop with a tight box around the element, then drag using the x and y from that crop"
            hands.drag(int(x0), int(y0), int(x1), int(y1))
            self.acted = True
            return f"stroke {int(x0)} {int(y0)} {int(x1)} {int(y1)}"

        def type_text(text: str):
            if not asked:
                return "nobody asked for this"
            hands.type_text(text)
            return "typed"

        def press(keys: str):
            if not asked:
                return "nobody asked for this"
            if "click" in self.request.lower() and "press" not in self.request.lower():
                return "not pressed. a key does not click. crop the element and click the x and y from that crop"
            hands.press(keys)
            return f"pressed {keys}"

        def run(command: str):
            if not asked:
                return "nobody asked for this"
            if not self.acted and "click" in self.request.lower() and "run" not in self.request.lower():
                return "not run. a command does not click. crop the element and click the x and y from that crop"
            return hands.run(command)

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
            "look": Tool("look", f"One look at the whole desktop. It returns no click point. Write the prompt for this pass. Name the one thing it is about. The picture is also sent to {OWNER}'s chat.", {"prompt": {"description": "The one thing this pass is about.", "type": "STRING"}}, look),
            "survey": Tool("survey", f"What is on screen and which controls it has. A desktop, a movie, a camera feed, or a game. The picture is also sent to {OWNER}'s chat. Call this before using a program's own controls.", {"prompt": {"description": "What this pass should understand.", "type": "STRING"}}, survey),
            "crop": Tool("crop", f"One look at a crop of one element. The prompt names where it sits and asks for its center. y0, x0, y1, x1 are that box, 0 to 1000, origin at the top left, y vertical. The picture is also sent to {OWNER}'s chat.", {"prompt": {"description": "What this pass should answer.", "type": "STRING"}, "y0": {"description": "Crop top, 0 to 1000.", "type": "INTEGER"}, "x0": {"description": "Crop left, 0 to 1000.", "type": "INTEGER"}, "y1": {"description": "Crop bottom, 0 to 1000.", "type": "INTEGER"}, "x1": {"description": "Crop right, 0 to 1000.", "type": "INTEGER"}}, lambda prompt, y0, x0, y1, x1: look(prompt, y0, x0, y1, x1)),
            "click": Tool("click", "Click the x and y returned by crop.", {"x": {"description": "Pixel x from crop.", "type": "INTEGER"}, "y": {"description": "Pixel y from crop.", "type": "INTEGER"}, "how": {"description": "Kind of click.", "type": "STRING", "enum": ["left", "right", "double"]}}, click, optional=("how",)),
            "drag": Tool("drag", "The next side, one straight stroke. Look after it. If the shape is not finished, drag the next side.", {"x0": {"description": "Start pixel x.", "type": "INTEGER"}, "y0": {"description": "Start pixel y.", "type": "INTEGER"}, "x1": {"description": "End pixel x.", "type": "INTEGER"}, "y1": {"description": "End pixel y.", "type": "INTEGER"}}, drag),
            "type_text": Tool("type_text", "Type the text argument exactly, every word of it, where the cursor is.", {"text": {"description": "The text to type, every word.", "type": "STRING"}}, type_text),
            "press": Tool("press", "Press a key he asked for, for example enter, escape, tab, or ctrl-a. Never a click and never a command.", {"keys": {"description": "The key or chord.", "type": "STRING"}}, press),
            "run": Tool("run", "One PowerShell command. A program is Start-Process and its executable name. Start-Sleep -Seconds N waits.", {"command": {"description": "The PowerShell command.", "type": "STRING"}}, run),
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
