import queue
import subprocess
import sys
import threading
from organs import eyes, hands, run_dir
from organs.brain import Brain, Stop, Tool, cursor_text
from organs.ears import ear_cmd, ear_text, write_wav
from organs.memory import Memory
from organs.telegram import Line
GRID = "The screen is a grid from 0 to 1000: y goes down from the top, x goes right from the left."
SYSTEM = (
    "You are Gemma. You live on this Windows computer. Your owner reaches you only through Telegram messages and calls. "
    "The computer's microphone and speakers are not yours. You act only by calling tools. "
    "You do every task yourself on this screen with these tools; no tool is made for any one app, so look and use the screen as a person would. "
    "Text outside a tool call is only your log; to say something, call speak. "
    "Each request shows your note, then 'Call: up' (you are on a call with your owner) or 'Call: down' (no call is on), then what is new; the Call label is not your owner speaking. "
    "'Wake' means you just started: continue from your note, or if nothing needs you, end your turn without calling any tool. "
    f"{GRID} "
    "You see the screen only when you call look. "
    "Call look before you name a place. Then point, look where the arrow landed, point again if it is off, then click. After acting, look again. "
    "If nothing changed as you intended, try another way or consult. "
    "A turn with no tool call ends this request, and only your note carries over. "
    "Before you ask your owner something or end a turn with work left, rewrite your note with the task, what you asked, and what is done."
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
        self.push("wake", "Wake")
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
        self.turn(kind, text)
    def request_text(self, text: str) -> str:
        parts = [self.memory.note, f"Call: {'up' if self.line.up else 'down'}", text]
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
    def turn(self, kind: str, text: str) -> None:
        self.request = text
        self.brain.fresh()
        prompt = ""
        user = text
        tools = self.tools()
        while True:
            reply = self.brain.think(SYSTEM, tools, self.request_text(user), prompt)
            if reply.stop or not reply.prompt:
                return
            prompt = reply.prompt
    def speak(self, text: str):
        self.line.speak(self.gate([sys.executable, "-m", "organs.mouth"], text.encode("utf-8")))
    def tools(self) -> dict[str, Tool]:
        text, number = "STRING", "INTEGER"
        return {
            "speak": Tool("speak", "Say this to your owner: aloud when a call is up, as a Telegram message when it is down.", {"text": {"description": "text", "type": text}}, self.say),
            "look": Tool("look", "See the whole screen now, with the pointer arrow drawn on it.", {}, self.look),
            "point": Tool("point", "Move only the pointer.", {"y": {"description": "0 top to 1000 bottom", "type": number}, "x": {"description": "0 left to 1000 right", "type": number}}, self.point),
            "click": Tool("click", "Click at the pointer.", {"how": {"description": "left | right | double", "type": text, "enum": ["left", "right", "double"]}}, self.click),
            "stroke": Tool("stroke", "Hold the left button and draw one line through these places.", {"points": {"description": "y x pairs separated by semicolons, up to 32", "type": text}}, self.stroke),
            "type_text": Tool("type_text", "Type into the focused window.", {"text": {"description": "text", "type": text}}, self.type_text),
            "press": Tool("press", "Press keys, for example ctrl+s or alt+tab.", {"keys": {"description": "keys", "type": text}}, self.press),
            "run": Tool("run", "Run a PowerShell command and get its output.", {"command": {"description": "command", "type": text}}, self.run),
            "note": Tool("note", "Replace your whole note with this text; every later request shows it. Write everything you still need.", {"text": {"description": "text", "type": text}}, self.note),
            "call_owner": Tool("call_owner", "Call your owner by video. opening is said when he answers.", {"opening": {"description": "opening", "type": text}}, self.call_owner),
            "hang_up": Tool("hang_up", "End the call. You stay at the computer.", {}, self.hang_up),
            "consult": Tool("consult", "Ask an advisor who sees the screen as it is now, with the arrow, on the same grid. Its answer comes back here, and you decide.", {"question": {"description": "question", "type": text}}, self.consult),
            "done": Tool("done", "Say a task is finished. The summary goes to the chat; nothing is spoken.", {"summary": {"description": "summary", "type": text}}, self.done),
        }
    def look(self) -> str:
        self.brain.frames.append(eyes.picture())
        return self.brain.media()
    def point(self, y, x) -> str:
        y, x = int(y), int(x)
        hands.aim(*eyes.screen_px(y, x))
        gy, gx = eyes.pointer_grid()
        return f"Pointer at y {gy} x {gx}."
    def click(self, how: str) -> str:
        hands.button(str(how))
        gy, gx = eyes.pointer_grid()
        label = {"left": "Left", "right": "Right", "double": "Double"}[str(how)]
        return f"{label} click at y {gy} x {gx}."
    def stroke(self, points: str) -> str:
        parts = [part for part in str(points).split(";") if part.strip()]
        if not parts or len(parts) > 32:
            raise ValueError(str(points))
        places = []
        coords = []
        for part in parts:
            y, x = (int(n) for n in part.split())
            places.append(f"y {y} x {x}")
            coords.append(eyes.screen_px(y, x))
        hands.stroke(coords)
        return "Stroke through " + "; ".join(places) + "."
    def type_text(self, text: str) -> str:
        title = hands.foreground_title()
        hands.type_text(str(text))
        return f'Typed "{text}" into window "{title}".'
    def press(self, keys: str) -> str:
        title = hands.foreground_title()
        hands.press(str(keys))
        return f'Pressed "{keys}" into window "{title}".'
    def run(self, command: str) -> str:
        return hands.run(str(command))
    def say(self, text: str) -> str:
        words = str(text)
        if self.line.up:
            self.speak(words)
            return "The words were spoken on the call."
        self.line.send_text(words)
        return "The words were sent as a Telegram message."
    def note(self, text: str) -> str:
        self.memory.replace(str(text))
        return "The note was replaced."
    def call_owner(self, opening: str) -> str:
        words = str(opening).strip()
        was = self.line.up
        if not was:
            self.line.dial()
        if not self.line.up:
            raise RuntimeError("The call was missed.")
        if words and not was:
            self.speak(words)
        return "The call is already up." if was else "The call is answered."
    def hang_up(self) -> str:
        self.line.hang()
        return "The call is down."
    def done(self, summary: str) -> Stop:
        self.mirror(str(summary), [])
        return Stop()
    def consult(self, question: str) -> str:
        folder = run_dir() / "consult"
        folder.mkdir(exist_ok=True)
        shot = folder / "screen.png"
        png = eyes.picture()
        shot.write_bytes(png)
        prompt = (
            "You advise Gemma. Advice only. Do not edit files.\n"
            f"{GRID}\n"
            f"Her note:\n{self.memory.note}\n"
            f"She asks:\n{question}\n"
            f"Read {shot}. Those bytes are the whole screen with the pointer arrow drawn on it. "
            "Answer with places on that grid.\n"
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
