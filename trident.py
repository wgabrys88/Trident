import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from organs import CONFIG, run_dir
from organs.brain import Brain, Stop, Tool, UserTurn
from organs.ears import ear_cmd, ear_text, write_wav
from organs.memory import Memory
from organs.telegram import Line

LOOK = {"type": "object", "properties": {"seen": {"type": "boolean"}, "y": {"type": "integer"}, "x": {"type": "integer"}}, "required": ["seen", "y", "x"]}
SYSTEM = (
    "You are Gemma. You are the mind at Wojciech's computer. From the phone you are one person. "
    "You hear him and you speak on the call. The computer's microphone and speakers are not yours. "
    "You decide from the meaning of what he says and what a look returns. You are not a task runner. "
    "The screen may be anything in front of you. Keep using tools until the thing he asked is done or you are blocked. "
    "A plain reply is you speaking, and it ends this request. Python brings the task again while you are idle, so look and continue from the screen. "
    "You are stateless. Python puts your memory on every request. Memory is how his preferences reach you, including a wish not to be called often. You still choose. "
    "You call a tool. Python takes the picture, draws the pointer on it, and turns a place into a click. You do not work out a pixel. "
    "The same drawing is on the call, so when the call is up he can see the screen and that pointer. call_owner and hang_up are how a call starts and ends. Python places the call. You stay after he hangs up. "
    f"consult spawns a new Cursor agent on this machine. It is {CONFIG['cloud']['model']}, not a virtual machine. "
    "Say so aloud in why, and why you are spawning it. Its reply is his next request, plain text. You will not see that it came from the agent. "
    "Consult when you are stuck, unsure, or he does not answer."
)


def ask_cursor(folder: Path, prompt: str) -> str:
    root = Path(os.environ["LOCALAPPDATA"]) / "cursor-agent" / "versions"
    version = max(p for p in root.iterdir() if p.name[:1].isdigit() and (p / "node.exe").is_file())
    done = subprocess.run(
        [str(version / "node.exe"), str(version / "index.js"), "-p", "--mode", "ask", "--trust", "--model", CONFIG["cloud"]["model"], "--output-format", "text", "--workspace", str(folder), prompt],
        capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=1800, cwd=str(folder), creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr + done.stdout)
    if not done.stdout.strip():
        raise RuntimeError("empty consult")
    return done.stdout


class Trident:
    def __init__(self):
        self.brain = Brain()
        self.memory = Memory()
        self.events: queue.Queue = queue.Queue()
        self.stopping = threading.Event()
        self.request = ""
        self.aim = None
        self.points = []
        self.going = False
        self.line = Line(on_text=lambda t: self.push("chat", t), on_utterance=lambda c: self.push("call", c))
        self.brain.sink = self.mirror

    def mirror(self, text: str, images: list[bytes]) -> None:
        if text:
            self.line.send_text(text)
        for png in images:
            self.line.send_photo(png)

    def start(self):
        self.brain.start()
        self.push("wake", "No request is open.")
        self.line.start()
        threading.Thread(target=self.worker, name="trident-worker", daemon=True).start()

    def stop(self):
        self.stopping.set()
        self.line.stop()
        self.brain.stop()

    def push(self, kind: str, payload):
        self.events.put((kind, payload))

    def worker(self):
        while not self.stopping.is_set():
            try:
                kind, payload = self.events.get(timeout=0.5)
            except queue.Empty:
                if self.stopping.is_set() or not self.going or not self.memory.task:
                    continue
                kind, payload = "idle", self.memory.task
            try:
                self.handle(kind, payload)
            except Exception as exc:
                self.going = False
                self.line.send_text(str(exc))

    def handle(self, kind: str, payload):
        if kind == "call":
            text = ear_text(self.gate(ear_cmd(write_wav(run_dir() / "call.wav", payload))))
            if not text:
                return
        else:
            text = payload
        reply = self.turn(kind, text)
        self.deliver(reply)
        self.going = bool(self.memory.task)

    def request_text(self, text: str) -> str:
        parts = [self.memory.block(), f"Call: {'up' if self.line.up else 'down'}", text]
        return "\n".join(part for part in parts if part)

    def gate(self, args: list[str], data: bytes | None = None) -> bytes:
        self.brain.stop()
        try:
            done = subprocess.run(args, input=data, capture_output=True, timeout=600, creationflags=subprocess.CREATE_NO_WINDOW)
        finally:
            self.brain.start()
        if done.returncode != 0:
            raise RuntimeError(done.stderr.decode("utf-8", "replace").strip() or f"exit {done.returncode}")
        return done.stdout

    def near_slot(self) -> bool:
        brain = CONFIG["brain"]
        return self.brain.prompt_tokens + brain["max_tokens"] >= brain["context"] // brain["slots"]

    def turn(self, kind: str, text: str) -> str:
        self.request = text
        if kind not in ("idle", "wake"):
            self.memory.set_task(text)
        if kind != "idle" or not self.brain.sent or self.near_slot():
            self.brain.sent = ""
            self.brain.rest = ""
            self.brain.prompt_tokens = 0
            self.aim = None
            self.points = []
            prompt = ""
        else:
            prompt = self.brain.rest or self.brain.carry(self.request_text(text))
            self.brain.rest = ""
        user = text
        tools = self.tools()
        for _ in range(CONFIG["brain"]["max_tool_steps"]):
            if prompt and self.near_slot():
                self.brain.sent = ""
                self.brain.prompt_tokens = 0
                self.aim = None
                self.points = []
                prompt = ""
            reply = self.brain.think(SYSTEM, tools, self.request_text(user), prompt)
            if reply.follow:
                user = reply.follow
                prompt = ""
                continue
            if reply.stop or not reply.prompt:
                return reply.text
            prompt = reply.prompt
        self.brain.rest = prompt
        return ""

    def deliver(self, text: str):
        if text:
            self.speak(text) if self.line.up else self.line.send_text(text)

    def speak(self, text: str):
        self.line.speak(self.gate([sys.executable, "-m", "organs.mouth"], text.encode("utf-8")))

    def tools(self) -> dict[str, Tool]:
        string = "STRING"
        return {
            "look": Tool("look", "Name one thing in what. Python takes the picture, draws the pointer, asks where that thing is, and keeps the place for click and drag. You do not calculate the place.", {"what": {"description": "The one thing to find", "type": string}}, self.look),
            "click": Tool("click", "Press the place the last look returned. how is left, right, or double.", {"how": {"description": "left, right, or double", "type": string, "enum": ["left", "right", "double"]}}, self.click),
            "drag": Tool("drag", "Stroke from the previous look to the last look.", {}, self.drag),
            "stroke": Tool("stroke", "One line through points on the 1000 grid of the last picture. points is at most 32 pairs, each y x, separated by semicolons. No new picture.", {"points": {"description": "y x;y x", "type": string}}, self.stroke),
            "type_text": Tool("type_text", "Type this text into the focused window.", {"text": {"description": "The text", "type": string}}, self.type_text),
            "press": Tool("press", "Press these keys.", {"keys": {"description": "Space-separated chords", "type": string}}, self.press),
            "run": Tool("run", "Run this PowerShell command. The start alias is not there. Start-Process opens a program.", {"command": {"description": "The command", "type": string}}, self.run),
            "remember": Tool("remember", "Store a fact. Python puts facts on every later request, including a preference such as him asking you not to call so often.", {"fact": {"description": "The fact", "type": string}}, self.remember),
            "call_owner": Tool("call_owner", "Place the call. answered means it is up and he can see the screen. already up is a different result. A miss is the error string. Consulting an agent after a miss is your choice. opening is what you say when the call is up.", {"opening": {"description": "What you say when the call is up", "type": string}}, self.call_owner, optional=("opening",)),
            "hang_up": Tool("hang_up", "End the call and stay. The task stays until done or he replaces it.", {}, self.hang_up),
            "done": Tool("done", "Finish the task. summary is mirrored, the task is cleared, and this request ends. Nothing is spoken. Call him first if you want him on the line.", {"summary": {"description": "What finished", "type": string}}, self.done),
            "consult": Tool(
                "consult",
                f"Spawn a new Cursor agent on this machine. It is {CONFIG['cloud']['model']}, and not a virtual machine. It only advises, and its reply is plain text. why is what you say aloud: that you are asking an agent, and why. question is what you need. attach is screen, part, or no. screen sends the whole screen. part sends the area around the last look. The reply comes back as his next request. After a missed call, consulting is your choice.",
                {
                    "why": {"description": "What you say aloud about spawning the agent", "type": string},
                    "question": {"description": "What you ask the agent", "type": string},
                    "attach": {"description": "screen, part, or no", "type": string, "enum": ["screen", "part", "no"]},
                },
                self.consult,
            ),
        }

    def look(self, what: str) -> str:
        what = str(what).strip()
        if not what:
            raise ValueError("what")
        from organs import eyes

        data = self.brain.ask_json(
            f"{what}. y and x are where it is on the 1000 grid, y down from the top and x to the right. seen is true only if it is there.",
            LOOK,
            eyes.picture(),
        )
        if data.get("seen") is True:
            self.aim = eyes.screen_px(int(data["y"]), int(data["x"]))
            self.points.append(self.aim)
            return "the place is ready"
        self.aim = None
        return "it is not on screen"

    def click(self, how: str) -> str:
        from organs import hands

        if self.aim is None:
            return "look first"
        x, y = self.aim
        ax, ay = hands.aim(x, y)
        if abs(ax - x) > 2 or abs(ay - y) > 2:
            return f"the cursor is at {ax} {ay}"
        hands.strike(x, y, how)
        return "clicked"

    def drag(self) -> str:
        from organs import hands

        if len(self.points) < 2:
            return "look at both ends first"
        (x0, y0), (x1, y1) = self.points[-2], self.points[-1]
        hands.drag(x0, y0, x1, y1)
        return "stroked"

    def stroke(self, points: str) -> str:
        from organs import eyes, hands

        parts = [part for part in str(points).split(";") if part.strip()]
        if not parts or len(parts) > 32:
            raise ValueError(str(points))
        coords = []
        for part in parts:
            y, x = part.split()
            coords.append(eyes.screen_px(int(y), int(x)))
        hands.stroke(coords)
        return "stroked"

    def type_text(self, text: str) -> str:
        from organs import hands

        hands.type_text(str(text))
        return "typed"

    def press(self, keys: str) -> str:
        from organs import hands

        hands.press(str(keys))
        return f"pressed {keys}"

    def run(self, command: str) -> str:
        from organs import hands

        return hands.run(str(command))

    def remember(self, fact: str) -> str:
        self.memory.remember(str(fact))
        return "remembered"

    def call_owner(self, opening: str = "") -> str:
        words = str(opening).strip()
        was = self.line.up
        if not was:
            self.line.dial()
        if not self.line.up:
            raise RuntimeError("call missed")
        if words:
            self.speak(words)
        return "already up" if was else "answered"

    def hang_up(self) -> str:
        self.line.hang()
        return "hung up"

    def done(self, summary: str) -> Stop:
        self.mirror(str(summary), [])
        self.memory.clear_task()
        self.going = False
        return Stop()

    def consult_prompt(self, question: str, image: bool) -> str:
        tools = "\n".join(f"- {name}: {tool.description}" for name, tool in self.tools().items())
        return (
            "You are advising Gemma. Reply with the next request she should act on, in the words of a person guiding her. "
            "She will read your reply as her user and she will not be told it came from you.\n"
            f"{SYSTEM}\n"
            f"Her tools:\n{tools}\n"
            f"Memory:\n{self.memory.block()}\n"
            f"His request:\n{self.request}\n"
            f"She asks:\n{question}\n"
            + ("The screen is screen.png in this folder.\n" if image else "")
        )

    def consult(self, why: str, question: str, attach: str) -> str | UserTurn:
        if attach == "part" and self.aim is None:
            return "look first"
        if self.line.up and str(why).strip():
            self.speak(str(why).strip())
        folder = run_dir() / "consult"
        folder.mkdir(exist_ok=True)
        shot = folder / "screen.png"
        if shot.exists():
            shot.unlink()
        png = None
        if attach in ("screen", "part"):
            from organs import eyes

            full = eyes.picture()
            png = eyes.around(full, *self.aim) if attach == "part" else full
            shot.write_bytes(png)
        prompt = self.consult_prompt(str(question), png is not None)
        self.mirror(prompt, [png] if png else [])
        reply = ask_cursor(folder, prompt)
        self.mirror(reply, [])
        return UserTurn(reply)


def main():
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
