import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from organs import CONFIG, log, run_dir, state_dir
from organs.brain import Brain, Tool
from organs.ears import transcribe, write_wav
from organs.memory import Memory
from organs.telegram import Line

LOG = log("trident")
OWNER = CONFIG["owner"]["name"]
INBOX = state_dir() / "inbox.txt"

SYSTEM = (
    "You must read the user request in full. "
    "Then you must read every tool description in full. "
    "Then you must call the one tool that moves one step closer to the user's goal."
)
PASS = {"type": "object", "properties": {"answer": {"type": "string"}, "confident": {"type": "boolean"}, "y": {"type": "integer"}, "x": {"type": "integer"}}, "required": ["answer", "confident"]}
SOURCE = {"chat": "a request", "consult": "a request", "call": "his voice on the call", "typed": "a line he typed on the computer"}


def consult_prompt(question: str, request: str, memory: str, pictured: bool) -> str:
    picture = "Read screen.png. " if pictured else ""
    return (
        f"{picture}Reply with one JSON object and no other text: {{\"request\": \"...\"}}. "
        f"You advise Gemma, the one mind on {OWNER}'s computer. You are on this machine, not a virtual machine. "
        "You may run commands and use the internet. Do not edit her files. "
        "Her tools are look, survey, click, drag, type_text, press, run, remember, call_owner, hang_up, and consult. "
        "look reads one list line, y then x then the name, and returns that pixel. She may click or drag only a pixel look just returned. "
        "survey asks the other local model to mark one drawn thing, in more than one pass. The mark is a list line on her next look. "
        "The request tells her the next action. It contains no pixel and does not name a model. "
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
        self.memory = Memory()
        self.events: queue.Queue = queue.Queue()
        self.stopping = threading.Event()
        self.request = ""
        self.note = ""
        self.used_tool = False
        self.aim = None
        self.given = []
        self.owed = None
        self.marks = []
        self.line = Line(on_text=lambda t: self.push("chat", t), on_utterance=lambda c: self.push("call", c), on_line=lambda _state: None)

    def start(self):
        self.brain.start()
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
            wav = write_wav(run_dir() / "call.wav", payload)
            self.line.send_file(wav.read_bytes(), wav.name)
            self.brain.stop()
            try:
                text, language = transcribe(wav)
            finally:
                self.brain.start()
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
        if step.thought or step.tool:
            self.line.send_text(f"{step.thought}\n{step.tool}\n{json.dumps(step.args, ensure_ascii=False)}")
        if step.tool:
            return None
        if self.owed == "act":
            pixel = f" The pixel is {self.aim[0]} {self.aim[1]}." if self.aim else ""
            return "The click did not happen." + pixel + " Look, then use that pixel."
        if self.owed == "look":
            return "Look again before you say it is there."
        return None

    def turn(self, kind: str, text: str):
        self.request = text
        self.note = ""
        self.used_tool = False
        self.aim = None
        self.given = []
        self.owed = None
        self.marks = []
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
        self.brain.stop()
        try:
            done = subprocess.run([sys.executable, "-m", "organs.mouth"], input=text.encode("utf-8"), capture_output=True, timeout=600, creationflags=subprocess.CREATE_NO_WINDOW)
            if done.returncode == 0 and done.stdout:
                self.line.speak(done.stdout)
            else:
                LOG.error("mouth %s", (done.stderr or b"").decode("utf-8", "replace")[-400:])
        finally:
            self.brain.start()

    def tools(self) -> dict[str, Tool]:
        from organs import eyes, hands

        def picked(answer: str, prompt: str):
            want, ask = answer.casefold().strip(), prompt.casefold().strip()
            hits = []
            for name, spot in self.marks:
                key = name.casefold().strip()
                if key and (key == want or key == ask or key in want or key in ask or ask in key):
                    hits.append(spot)
            return hits[0] if len(hits) == 1 else None

        def keep(found: dict) -> dict:
            if "x" not in found:
                self.aim = None
                return found
            self.aim = (int(found["x"]), int(found["y"]))
            self.given.append(self.aim)
            if self.owed == "look":
                self.owed = None
            return found

        def look(prompt: str):
            png = eyes.overlay(eyes.screenshot(), None, self.marks)
            self.line.send_photo(png, prompt)
            words = f"Read the list line for {prompt}. Copy its y and x. The answer is that name. If you cannot read it, confident is false and omit y and x."
            data = self.brain.ask_json(words, PASS, png)
            found = {"answer": str(data["answer"]), "confident": data["confident"] is True}
            spot = picked(found["answer"], prompt) if found["confident"] else None
            if spot:
                found["x"], found["y"] = eyes.center_px(spot)
            elif found["confident"] and "y" in data and "x" in data:
                found["x"], found["y"] = eyes.center_px([data["y"], data["x"], data["y"], data["x"]])
            return keep(found)

        def survey(prompt: str):
            raw = eyes.screenshot()
            found = self.brain.survey(raw, prompt)
            self.marks = [(item["name"], [item["y0"], item["x0"], item["y1"], item["x1"]]) for item in found]
            png = eyes.overlay(raw, None, self.marks)
            self.line.send_photo(png, prompt)
            return "marked " + ", ".join(item["name"] for item in found) if found else "marked none"

        def click(x: int, y: int, how: str = "left"):
            x, y = int(x), int(y)
            if self.aim != (x, y):
                self.owed = "act"
                return f"the pixel is {self.aim[0]} {self.aim[1]}" if self.aim else "no pixel was given"
            ax, ay = hands.aim(x, y)
            png = eyes.overlay(eyes.screenshot(), None, self.marks)
            (run_dir() / "aim.png").write_bytes(png)
            if abs(ax - x) > 2 or abs(ay - y) > 2:
                self.line.send_photo(png, "aim")
                self.owed = "act"
                return f"aimed {ax} {ay}"
            LOG.info("aim cursor %s %s before %s press %s %s", ax, ay, how, x, y)
            hands.strike(x, y, how)
            self.line.send_photo(png, "aim")
            self.marks = []
            self.owed = "look"
            return f"{how} click at {x} {y}"

        def drag(x0: int, y0: int, x1: int, y1: int):
            x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
            if self.aim != (x0, y0):
                self.owed = "act"
                return f"the pixel is {self.aim[0]} {self.aim[1]}" if self.aim else "no pixel was given"
            if (x1, y1) not in self.given or (x1, y1) == (x0, y0):
                self.owed = "act"
                ends = [p for p in self.given if p != (x0, y0)]
                return f"the end is {ends[-1][0]} {ends[-1][1]}" if ends else "no end was given"
            hands.drag(x0, y0, x1, y1)
            self.marks = []
            self.owed = "look"
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

        def consult(why: str, question: str, image: bool = False):
            if self.line.up and str(why).strip():
                self.speak(str(why))
            folder = run_dir() / "consult"
            folder.mkdir(exist_ok=True)
            png_path = folder / "screen.png"
            if png_path.exists():
                png_path.unlink()
            if image is True or image == "true":
                png = eyes.overlay(eyes.screenshot(), None, self.marks)
                png_path.write_bytes(png)
                self.line.send_photo(png, str(why))
            try:
                answer = ask_cursor(folder, question, self.request, self.memory.facts_block(), png_path.is_file())
            except Exception as exc:
                answer = f"The consult came back empty. Decide the next step from what you can see. {exc}"
            self.push("consult", answer)
            self.line.send_text(answer)

        return {
            "look": Tool("look", "Read the screen.", {"prompt": {"description": "What.", "type": "STRING"}}, look),
            "survey": Tool("survey", "Mark one drawn thing.", {"prompt": {"description": "What.", "type": "STRING"}}, survey),
            "click": Tool("click", "Press x y from look.", {"x": {"description": "X.", "type": "INTEGER"}, "y": {"description": "Y.", "type": "INTEGER"}}, click),
            "drag": Tool("drag", "Stroke x0 y0 to x1 y1.", {"x0": {"description": "X0.", "type": "INTEGER"}, "y0": {"description": "Y0.", "type": "INTEGER"}, "x1": {"description": "X1.", "type": "INTEGER"}, "y1": {"description": "Y1.", "type": "INTEGER"}}, drag),
            "type_text": Tool("type_text", "Type the text.", {"text": {"description": "Text.", "type": "STRING"}}, type_text),
            "press": Tool("press", "Press the key.", {"keys": {"description": "Key.", "type": "STRING"}}, press),
            "run": Tool("run", "Run Start-Process and the address.", {"command": {"description": "Command.", "type": "STRING"}}, run),
            "remember": Tool("remember", "Store one fact.", {"fact": {"description": "Fact.", "type": "STRING"}}, remember),
            "call_owner": Tool("call_owner", f"Call {OWNER}.", {"opening": {"description": "Opening.", "type": "STRING"}}, call_owner, optional=("opening",)),
            "hang_up": Tool("hang_up", "End the call.", {}, hang_up, final=True),
            "consult": Tool("consult", "Ask for the next step.", {"why": {"description": "Why.", "type": "STRING"}, "question": {"description": "Question.", "type": "STRING"}, "image": {"description": "Screen.", "type": "BOOLEAN"}}, consult, optional=("image",), final=True),
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
