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
    "Assumptions are not allowed: before you click, drag, type, or press you look or crop, and you copy that result's x and y into the tool with no other numbers.\n"
    "A name is not proof. After you act, look again, and stop only when that look shows the thing you meant.\n"
    "The picture shows an arrow, slim red lines through its tip, and that tip's y and x on the 1000 grid. A zoom that does not show the arrow and those coordinates missed.\n"
    f"You are Gemma, the one mind on {OWNER}'s computer. He calls you, or you call him, and you talk. You are not a task runner. You decide from the meaning of what is said and what is on the screen.\n"
    "The screen may be Paint, a browser, a film, a camera, or a game. Deal with whatever is in front of you.\n"
    "When the line is up, he can see the screen. The computer's microphone and speakers are not your ears or your mouth. Python carries the call, the chat, and the pictures. You do not operate that wire. "
    "Python sends him the request, your thought, the tool, the arguments, and every picture you are asked to read, in that same form.\n"
    "A call can end and you stay at the machine. You keep working by calling the next tool in this turn. A reply means you decided to stop. The line being down does not start a turn.\n"
    "look boxes one thing. The answer is the words in it, or the name when there are none. It returns the box and the center pixel. crop reads that box and returns the pixel. Aim at that pixel, then click or drag. type_text types the text you pass. press is a key. run is one PowerShell command. "
    "survey is the other local model. When your own look is not good enough, send survey the picture, take the answer, and act.\n"
    "When you do not understand, you are replanning, you are stuck, or you do not know a fact that is not on the screen, call consult before you guess. consult spawns a cursor agent on this machine, and that agent can use this machine and the internet. "
    "why is spoken to him if the line is up, so why says that you spawned a cursor agent and the reason. "
    "Python tells that agent who you are, which tools you have, the request you are on, and the picture if you attached one. "
    "The answer comes back as the next request, the same form as his, and you are not told which requests are his. Continue from the meaning.\n"
    "remember stores one short fact. Those facts are appended to every request. If he asks you not to bother him, remember it. You may still call him. call_owner calls him. hang_up ends the call and you stay.\n"
    f"You are Gemma, the one mind on {OWNER}'s computer. Decide, then one tool or a short reply."
)
PASS = {"type": "object", "properties": {"answer": {"type": "string", "maxLength": 200}, "confident": {"type": "boolean"}, "y": {"type": "integer", "minimum": 0, "maximum": 1000}, "x": {"type": "integer", "minimum": 0, "maximum": 1000}, "y0": {"type": "integer", "minimum": 0, "maximum": 1000}, "x0": {"type": "integer", "minimum": 0, "maximum": 1000}, "y1": {"type": "integer", "minimum": 0, "maximum": 1000}, "x1": {"type": "integer", "minimum": 0, "maximum": 1000}}, "required": ["answer", "confident", "y", "x", "y0", "x0", "y1", "x1"]}
SOURCE = {"chat": "a request", "consult": "a request", "call": "his voice on the call", "typed": "a line he typed on the computer"}


def _box(y0, x0, y1, x1):
    y0, x0, y1, x1 = (int(v) for v in (y0, x0, y1, x1))
    return [y0, x0, y1, x1] if min(y0, x0, y1, x1) >= 0 else None


def consult_prompt(question: str, request: str, memory: str, pictured: bool) -> str:
    picture = "Read screen.png. " if pictured else ""
    return (
        f"{picture}The whole reply is one JSON object with the key request. "
        f"You are advising Gemma, the one mind on {OWNER}'s computer. "
        "You are on this machine, not a virtual machine. Run commands here and use the internet. Do not edit files. "
        "Her tools are look, survey, crop, click, drag, type_text, press, run, remember, call_owner, hang_up, and consult. "
        "look's answer is the words in the thing, or its name when there are none. "
        "request is the next thing she should do, with no mention of a model. "
        f"She is on: {request}\n{memory}\n{question.strip()}"
    )


def ask_cursor(folder: Path, question: str, request: str, memory: str, pictured: bool) -> str:
    root = Path(os.environ["LOCALAPPDATA"]) / "cursor-agent" / "versions"
    version = max(p for p in root.iterdir() if p.name[:1].isdigit() and (p / "node.exe").is_file())
    LOG.info("consult %s on this machine", CONFIG["cloud"]["model"])
    done = subprocess.run(
        [str(version / "node.exe"), str(version / "index.js"), "-p", "--force", "--sandbox", "disabled", "--trust", "--model", CONFIG["cloud"]["model"], "--output-format", "text", "--workspace", str(folder), consult_prompt(question, request, memory, pictured)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=900, cwd=str(folder), creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout or "").strip() or f"agent exit {done.returncode}")
    text = (done.stdout or "").strip()
    answer = str(json.loads(text)["request"]).strip()
    if not answer:
        raise RuntimeError("empty consult")
    return answer


class Trident:
    def __init__(self):
        self.brain = Brain()
        self.mouth = Mouth()
        self.memory = Memory()
        self.events: queue.Queue = queue.Queue()
        self.stopping = threading.Event()
        self.request = ""
        self.note = ""
        self.used_tool = False
        self.aim = None
        self.seen = []
        self.line = Line(on_text=lambda t: self.push("chat", t), on_utterance=lambda c: self.push("call", c), on_line=lambda _state: None)

    def start(self):
        self.brain.start()
        self.mouth.load()
        self.line.start()
        threading.Thread(target=self.worker, name="trident-worker", daemon=True).start()
        threading.Thread(target=self.inbox_loop, name="trident-inbox", daemon=True).start()
        LOG.info("trident up")

    def stop(self):
        self.stopping.set()
        self.line.stop()
        self.brain.stop()
        LOG.info("trident down")

    def push(self, kind: str, payload):
        self.events.put((kind, payload))

    def inbox_loop(self):
        while not self.stopping.wait(0.5):
            if not INBOX.is_file():
                continue
            raw = INBOX.read_bytes()
            lines = [line.strip() for line in raw.decode("utf-8").splitlines() if line.strip()]
            INBOX.unlink()
            self.line.send_file(raw, INBOX.name)
            for line in lines:
                self.push("typed", line)

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
        self.deliver(self.turn(kind, text))

    def command(self, name: str):
        if name == "call":
            self.line.dial()
            self.speak("I am up. Do you want anything?")
        elif name == "hang":
            self.line.hang()
        elif name == "stop":
            self.stopping.set()

    def on_step(self, step):
        if step.thought:
            self.note = step.thought
        if step.tool:
            self.used_tool = True
        self.line.send_text(f"{step.thought}\n{step.tool}\n{json.dumps(step.args, ensure_ascii=False)}")

    def turn(self, kind: str, text: str):
        self.request = text
        self.note = ""
        self.used_tool = False
        self.aim = None
        self.seen = []
        situation = f"[{time.strftime('%H:%M')} | line {'up' if self.line.up else 'down'} | {SOURCE[kind]}]"
        reply = self.brain.think(SYSTEM, self.tools(), self.memory.history(), f"{situation}\n{text}{self.memory.facts_block()}", on_step=self.on_step)
        spoken = reply.text.strip()
        if spoken or self.note or self.used_tool:
            self.memory.add_turn(text, spoken or self.note)
        if kind in ("chat", "call", "typed"):
            self.memory.set_task(text)
        return spoken

    def deliver(self, text: str):
        if not text:
            return
        if self.line.up:
            self.speak(text)
        else:
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

    def tools(self) -> dict[str, Tool]:
        from organs import eyes, hands

        def look(prompt: str, y0: int = -1, x0: int = -1, y1: int = -1, x1: int = -1):
            box = _box(y0, x0, y1, x1)
            png = eyes.overlay(eyes.mark(eyes.screenshot(), self.seen), box)
            self.line.send_photo(png, prompt)
            words = f"Box only {prompt}, tight around it. y and x are its center. The answer is the words in it, or the name when there are none."
            data = self.brain.ask_json(words, PASS, png)
            got = [data["y0"], data["x0"], data["y1"], data["x1"]]
            self.seen.append((str(data["answer"])[:16], eyes.embed(box, got) if box else got))
            found = {"answer": str(data["answer"]), "confident": data["confident"] is True, "y0": data["y0"], "x0": data["x0"], "y1": data["y1"], "x1": data["x1"]}
            if box:
                found["x"], found["y"] = eyes.point_px(box, (data["y0"] + data["y1"]) / 2, (data["x0"] + data["x1"]) / 2)
            else:
                found["x"], found["y"] = eyes.center_px([data["y0"], data["x0"], data["y1"], data["x1"]])
            self.aim = (found["x"], found["y"])
            if box:
                return {"answer": found["answer"], "confident": found["confident"], "x": found["x"], "y": found["y"]}
            return found

        def survey(prompt: str):
            png = eyes.overlay(eyes.shrink(eyes.mark(eyes.screenshot(), self.seen), CONFIG["vision"]["side"]))
            self.line.send_photo(png, prompt)
            return self.brain.survey(png, prompt)

        def click(x: int, y: int, how: str = "left"):
            x, y = int(x), int(y)
            if self.aim and (x, y) != self.aim:
                return f"the pixel is {self.aim[0]} {self.aim[1]}"
            ax, ay = hands.aim(x, y)
            png = eyes.overlay(eyes.mark(eyes.screenshot(), self.seen))
            (state_dir() / "aim.png").write_bytes(png)
            if abs(ax - x) > 2 or abs(ay - y) > 2:
                self.line.send_photo(png, "aim")
                return f"aimed {ax} {ay}"
            LOG.info("aim cursor %s %s crop %s before %s press %s %s", ax, ay, self.aim, how, x, y)
            hands.strike(x, y, how)
            self.line.send_photo(png, "aim")
            return f"{how} click at {x} {y}"

        def drag(x0: int, y0: int, x1: int, y1: int):
            x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
            if self.aim and (x0, y0) != self.aim:
                return f"the pixel is {self.aim[0]} {self.aim[1]}"
            hands.drag(x0, y0, x1, y1)
            return f"stroke {x0} {y0} {x1} {y1}"

        def type_text(text: str):
            hands.type_text(text)
            return "typed"

        def press(keys: str):
            hands.press(keys)
            return f"pressed {keys}"

        def run(command: str):
            return hands.run(command)

        def remember(fact: str):
            self.memory.remember(fact)
            return "remembered"

        def call_owner(opening: str = "I am up. Do you want anything?"):
            if self.line.up:
                return "the line is already up; just speak"
            try:
                self.line.dial()
            except Exception as exc:
                return f"he did not answer: {exc}"
            self.speak(opening)
            return "he answered and can see the screen; say what he should hear next, or hang_up"

        def hang_up():
            self.line.hang()
            return "hung up"

        def consult(why: str, question: str, image: bool = False, y0: int = -1, x0: int = -1, y1: int = -1, x1: int = -1):
            if self.line.up and str(why).strip():
                self.speak(str(why))
            folder = state_dir() / "consult"
            folder.mkdir(exist_ok=True)
            png_path = folder / "screen.png"
            if png_path.exists():
                png_path.unlink()
            box = _box(y0, x0, y1, x1)
            if image is True or image == "true" or box:
                png = eyes.overlay(eyes.mark(eyes.screenshot(), self.seen), box)
                png_path.write_bytes(png)
                self.line.send_photo(png, str(why))
            try:
                answer = ask_cursor(folder, question, self.request, self.memory.facts_block(), png_path.is_file())
            except Exception as exc:
                answer = f"The consult came back empty. Decide the next step from what you can see. {exc}"
            self.push("consult", answer)
            self.line.send_text(answer)

        return {
            "look": Tool("look", "Your eyes on the whole desktop. Box the one thing. The answer is the words in it, or the name when there are none. Returns the box and its center pixel.", {"prompt": {"description": "The one thing this pass is about.", "type": "STRING"}}, look),
            "survey": Tool("survey", "The other local model looks. Use it when your own look is not good enough. Take the answer and act.", {"prompt": {"description": "What this pass should understand.", "type": "STRING"}}, survey),
            "crop": Tool("crop", "Your eyes on one box. y0, x0, y1, x1 are 0 to 1000, origin top left, y vertical. Returns the pixel to click.", {"prompt": {"description": "What this pass should answer.", "type": "STRING"}, "y0": {"description": "Crop top, 0 to 1000.", "type": "INTEGER"}, "x0": {"description": "Crop left, 0 to 1000.", "type": "INTEGER"}, "y1": {"description": "Crop bottom, 0 to 1000.", "type": "INTEGER"}, "x1": {"description": "Crop right, 0 to 1000.", "type": "INTEGER"}}, lambda prompt, y0, x0, y1, x1: look(prompt, y0, x0, y1, x1)),
            "click": Tool("click", "Aim at the x and y crop returned, then press.", {"x": {"description": "Pixel x from crop.", "type": "INTEGER"}, "y": {"description": "Pixel y from crop.", "type": "INTEGER"}, "how": {"description": "Kind of click.", "type": "STRING", "enum": ["left", "right", "double"]}}, click, optional=("how",)),
            "drag": Tool("drag", "One straight stroke. Start at the x and y look or crop returned.", {"x0": {"description": "Start pixel x.", "type": "INTEGER"}, "y0": {"description": "Start pixel y.", "type": "INTEGER"}, "x1": {"description": "End pixel x.", "type": "INTEGER"}, "y1": {"description": "End pixel y.", "type": "INTEGER"}}, drag),
            "type_text": Tool("type_text", "Type the text argument exactly, every word of it, where the cursor is.", {"text": {"description": "The text to type, every word.", "type": "STRING"}}, type_text),
            "press": Tool("press", "Press a key, for example enter, escape, tab, or ctrl-a.", {"keys": {"description": "The key or chord.", "type": "STRING"}}, press),
            "run": Tool("run", "One PowerShell command. A program is Start-Process and its executable name. Start-Sleep -Seconds N waits.", {"command": {"description": "The PowerShell command.", "type": "STRING"}}, run),
            "remember": Tool("remember", "Store one short fact. It is appended to every later request. Use it when he says how to reach him, including when not to bother him. You still decide.", {"fact": {"description": "The fact.", "type": "STRING"}}, remember),
            "call_owner": Tool("call_owner", f"Call {OWNER}. When he answers he can see the screen. The opening is the first thing he hears.", {"opening": {"description": "The first sentence he hears.", "type": "STRING"}}, call_owner, optional=("opening",)),
            "hang_up": Tool("hang_up", "End the call and stay at the machine. Say nothing after it.", {}, hang_up, final=True),
            "consult": Tool("consult", "Spawn a cursor agent on this machine when you do not understand, you are replanning, you are stuck, or you do not know a fact that is not on the screen. Call it before you guess. The agent can use this machine and the internet. why is spoken on the call: say that you spawned a cursor agent and the reason. The answer comes back as the next request. Python sends who you are, your tools, the request you are on, and the picture. image true attaches the screen. A box attaches only that part.", {"why": {"description": "The sentence he hears: you spawned a cursor agent, and why.", "type": "STRING"}, "question": {"description": "What the cursor agent should decide.", "type": "STRING"}, "image": {"description": "Attach the screen.", "type": "BOOLEAN"}, "y0": {"description": "Part top, 0 to 1000. Omit for the whole screen.", "type": "INTEGER"}, "x0": {"description": "Part left, 0 to 1000.", "type": "INTEGER"}, "y1": {"description": "Part bottom, 0 to 1000.", "type": "INTEGER"}, "x1": {"description": "Part right, 0 to 1000.", "type": "INTEGER"}}, consult, optional=("image", "y0", "x0", "y1", "x1"), final=True),
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
