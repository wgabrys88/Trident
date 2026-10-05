import queue
import subprocess
import sys
import threading
from organs import CONFIG, eyes, hands, run_dir
from organs.brain import Brain, Stop, Tool, cursor_text
from organs.ears import ear_cmd, ear_text, write_wav
from organs.memory import Memory
from organs.telegram import Line
GRID = "a 0-1000 grid over the whole screen, y down from the top and x right from the left"
SYSTEM = (
    "You are Gemma, the mind at Wojciech's computer. He reaches you only through Telegram. "
    "The computer's microphone and speakers are not yours. You decide. "
    "A plain reply is you speaking. When Call: up it is spoken on the call. When Call: down it is a Telegram message. A plain reply ends this request. "
    "When the text is No request is open, wait. "
    "Call: down means the line is down. Call: up means the line is up. "
    "Memory on a request is facts you can use. You still choose. "
    f"The screen is {GRID}. "
    "look returns a screenshot of the whole screen with the mouse pointer arrow drawn on it. "
    "point moves only the mouse pointer to (y, x). "
    "After point, look where the arrow landed and correct it before you click or type. You may look again after you act. "
    "call_owner places a call. hang_up ends a call that is up. You stay after the call ends. "
    f"consult asks {CONFIG['cloud']['model']} on this machine. It only advises. When the call is up, say aloud why. "
    "Its answer is the consult result, and you decide. "
    "remember stores a fact for later requests. done finishes the task and nothing is spoken. "
    "Consult when you are stuck, unsure, or he does not answer."
)
class Trident:
    def __init__(self):
        self.brain = Brain()
        self.memory = Memory()
        self.events: queue.Queue = queue.Queue()
        self.stopping = threading.Event()
        self.request = ""
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
                continue
            try:
                self.handle(kind, payload)
            except Exception as exc:
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
        if kind != "wake":
            self.memory.set_task(text)
        self.brain.fresh()
        prompt = ""
        user = text
        tools = self.tools()
        for _ in range(CONFIG["brain"]["max_tool_steps"]):
            if prompt and self.near_slot():
                self.brain.fresh()
                prompt = ""
            reply = self.brain.think(SYSTEM, tools, self.request_text(user), prompt)
            if reply.stop or not reply.prompt:
                return reply.text
            prompt = reply.prompt
        return ""
    def deliver(self, text: str):
        if text:
            self.speak(text) if self.line.up else self.line.send_text(text)
    def speak(self, text: str):
        self.line.speak(self.gate([sys.executable, "-m", "organs.mouth"], text.encode("utf-8")))
    def tools(self) -> dict[str, Tool]:
        text, number = "STRING", "INTEGER"
        model = CONFIG["cloud"]["model"]
        return {
            "look": Tool("look", "Return a screenshot of the whole screen with the mouse pointer arrow drawn on it.", {}, self.look),
            "point": Tool("point", "Move the mouse pointer to (y, x). Move nothing else.", {"y": {"description": "y", "type": number}, "x": {"description": "x", "type": number}}, self.point),
            "click": Tool("click", "Click at the mouse pointer's current position.", {"how": {"description": "left, right, or double", "type": text, "enum": ["left", "right", "double"]}}, self.click),
            "stroke": Tool("stroke", "Draw one line through y x pairs separated by semicolons. Take no new screenshot.", {"points": {"description": "Up to 32 pairs of y x, separated by semicolons", "type": text}}, self.stroke),
            "type_text": Tool("type_text", "Type this text into the focused window.", {"text": {"description": "The text to type", "type": text}}, self.type_text),
            "press": Tool("press", "Press these keys.", {"keys": {"description": "Space-separated chords", "type": text}}, self.press),
            "run": Tool("run", "Run this PowerShell command.", {"command": {"description": "The command to run", "type": text}}, self.run),
            "remember": Tool("remember", "Store this fact so later requests include it.", {"fact": {"description": "The fact to store", "type": text}}, self.remember),
            "call_owner": Tool("call_owner", "Place a video call to Wojciech. When the call is up, say opening. Asking an advisor after a miss is your choice.", {"opening": {"description": "What to say when the call is up", "type": text}}, self.call_owner, optional=("opening",)),
            "hang_up": Tool("hang_up", "End the call and stay at the computer. The open task remains.", {}, self.hang_up),
            "done": Tool("done", "Finish the open task. Show summary in the chat and speak nothing.", {"summary": {"description": "What finished", "type": text}}, self.done),
            "consult": Tool(
                "consult",
                f"Ask {model} on this machine for advice only. It does not change files and it does not use the computer. why is said aloud when the call is up. question is what you ask. The answer is this tool's result, and you decide.",
                {
                    "why": {"description": "What to say aloud about asking", "type": text},
                    "question": {"description": "What you ask", "type": text},
                },
                self.consult,
            ),
        }
    def look(self) -> str:
        self.brain.frames.append(eyes.picture())
        return self.brain.media()
    def point(self, y, x) -> str:
        y, x = int(y), int(x)
        hands.aim(*eyes.screen_px(y, x))
        return f"The mouse pointer is now at y {y} x {x}."
    def click(self, how: str) -> str:
        hands.button(str(how))
        return f"The {how} button was clicked at the mouse pointer."
    def stroke(self, points: str) -> str:
        parts = [part for part in str(points).split(";") if part.strip()]
        if not parts or len(parts) > 32:
            raise ValueError(str(points))
        coords = []
        for part in parts:
            y, x = part.split()
            coords.append(eyes.screen_px(int(y), int(x)))
        hands.stroke(coords)
        return "The line was drawn."
    def type_text(self, text: str) -> str:
        hands.type_text(str(text))
        return "The text was typed into the focused window."
    def press(self, keys: str) -> str:
        hands.press(str(keys))
        return f"These keys were pressed: {keys}."
    def run(self, command: str) -> str:
        return hands.run(str(command))
    def remember(self, fact: str) -> str:
        self.memory.remember(str(fact))
        return "The fact is stored."
    def call_owner(self, opening: str = "") -> str:
        words = str(opening).strip()
        was = self.line.up
        if not was:
            self.line.dial()
        if not self.line.up:
            raise RuntimeError("The call was missed.")
        if words:
            self.speak(words)
        return "The call is already up." if was else "The call is answered."
    def hang_up(self) -> str:
        self.line.hang()
        return "The call is down."
    def done(self, summary: str) -> Stop:
        self.mirror(str(summary), [])
        self.memory.clear_task()
        return Stop()
    def consult(self, why: str, question: str) -> str:
        if self.line.up and str(why).strip():
            self.speak(str(why).strip())
        folder = run_dir() / "consult"
        folder.mkdir(exist_ok=True)
        shot = folder / "screen.png"
        png = eyes.picture()
        shot.write_bytes(png)
        prompt = (
            "You advise Gemma. Advice only. Do not edit files.\n"
            f"The screen is {GRID}.\n"
            f"Her open task:\n{self.memory.task}\n"
            f"She asks:\n{question}\n"
            f"Read {shot}. Those bytes are the whole screen with the mouse pointer arrow drawn on it. "
            f"Answer with places on {GRID}.\n"
        )
        self.mirror(prompt, [png])
        return "The advisor says: " + cursor_text(folder, prompt).strip()
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
