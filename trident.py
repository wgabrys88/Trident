import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from organs import CONFIG, log, run_dir, state_dir
from organs.brain import Brain
from organs.ears import transcribe, write_wav
from organs.memory import Memory
from organs.telegram import Line

LOG = log("trident")
OWNER = CONFIG["owner"]["name"]
INBOX = state_dir() / "inbox.txt"

# The second vision model stays in the tree. This wave does not call it.
SECOND_VISION = False

NOTE = (
    "You are Gemma. He just spoke to you. "
    "In note, write that request in your own words for an advisor who cannot hear him and cannot see his account. "
    "Ask what the advisor needs. "
    "Do not choose a tool. Do not name a pixel."
)
NOTE_SCHEMA = {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]}
PASS = {"type": "object", "properties": {"answer": {"type": "string"}, "confident": {"type": "boolean"}, "y": {"type": "integer"}, "x": {"type": "integer"}}, "required": ["answer", "confident"]}
SOURCE = {"chat": "a request", "consult": "a request", "call": "his voice on the call", "typed": "a line he typed on the computer"}


def advisor_prompt(note: str, line: str, memory: str, trace: str) -> str:
    shown = trace.strip()
    if len(shown) > 12000:
        shown = shown[:2000] + "\n...\n" + shown[-9000:]
    if not shown:
        shown = "Nothing yet."
    remembered = memory.strip()
    remembered = remembered + "\n" if remembered else ""
    return (
        "Reply with one JSON object and no other text. "
        "You are the navigator. Gemma is the eyes, the hands, and the voice on his computer. "
        "You never talk to Telegram, you never see his account, and you never hold the call. "
        "You only answer her. That answer is her next move. "
        "She asked what you need. Answer with one tool. "
        "Choose it from the meaning of what she wrote and what she has reported. "
        "Do not invent a pixel. Do not run anything yourself. "
        "A place exists only after a look. Her words are not a place. "
        "If he asked her to do something on the machine, the next move sees or does that. "
        "say is for telling him the result, or for a request that was only conversation. "
        "A missing preference is not a reason to stop before she has looked. "
        "When a look leaves you stuck, call_owner. "
        'look {"tool":"look","what":"the thing"} She screenshots and tells you where that thing is. '
        "Name one thing, such as a white pawn, a Play button, or the corner of the canvas. Not the whole screen. "
        "This is the expensive step. Ask only when you need it. "
        "If the place she reports is not the thing, look again and name the thing you mean. "
        'click {"tool":"click"} She clicks the place the last look returned. '
        "She reports that place as pixel x y, screen pixels, x then y. "
        "click with those numbers presses. Any other numbers do not, and she tells you the pixel. "
        "click with no numbers presses the place she just reported. "
        'drag {"tool":"drag"} She strokes from the previous place to the last look, when both were looks. '
        "Numbers stroke only when both ends are places she reported. "
        'type_text {"tool":"type_text","text":"..."} She types it. '
        'press {"tool":"press","keys":"enter"} She presses it. '
        'run {"tool":"run","command":"..."} She runs that PowerShell command on Windows. The start alias is not there. Start-Process opens a program. '
        'remember {"tool":"remember","fact":"..."} She stores it. '
        'call_owner {"tool":"call_owner","opening":"..."} She calls him. A missed call is not an error. If the line is already up, she says so. '
        'hang_up {"tool":"hang_up"} She ends the call and stays. '
        'say {"tool":"say","text":"..."} She tells him this and the turn ends. '
        f"The line is {line}.\n"
        f"{remembered}"
        f"She wrote:\n{note.strip()}\n"
        f"So far:\n{shown}"
    )


def parse_move(text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        raise RuntimeError("empty consult")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("empty consult")
        try:
            data = json.loads(raw[start:end + 1])
        except json.JSONDecodeError as exc:
            raise RuntimeError("empty consult") from exc
    if not isinstance(data, dict) or not str(data.get("tool", "")).strip():
        raise RuntimeError("empty consult")
    return data


def ask_cursor(folder: Path, prompt: str) -> dict:
    root = Path(os.environ["LOCALAPPDATA"]) / "cursor-agent" / "versions"
    version = max(p for p in root.iterdir() if p.name[:1].isdigit() and (p / "node.exe").is_file())
    LOG.info("consult %s on this machine", CONFIG["cloud"]["model"])
    done = subprocess.run(
        [str(version / "node.exe"), str(version / "index.js"), "-p", "--mode", "ask", "--trust", "--model", CONFIG["cloud"]["model"], "--output-format", "text", "--workspace", str(folder), prompt],
        capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=900, cwd=str(folder), creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout or "").strip() or f"agent exit {done.returncode}")
    return parse_move(done.stdout or "")


def click_pixel(aim, move: dict) -> tuple[tuple[int, int] | None, str]:
    if aim is None:
        return None, "no pixel was given"
    if "x" in move or "y" in move:
        try:
            pair = (int(move["x"]), int(move["y"]))
        except (KeyError, TypeError, ValueError):
            return None, f"the pixel is {aim[0]} {aim[1]}"
        if pair != aim:
            return None, f"the pixel is {aim[0]} {aim[1]}"
    return aim, ""


def drag_ends(aim, given, move: dict) -> tuple[tuple[tuple[int, int], tuple[int, int]] | None, str]:
    distinct = []
    for point in given:
        if not distinct or distinct[-1] != point:
            distinct.append(point)

    def told() -> str:
        if len(distinct) >= 2:
            a, b = distinct[-2], distinct[-1]
            return f"the stroke is {a[0]} {a[1]} {b[0]} {b[1]}"
        if aim is not None:
            return f"the pixel is {aim[0]} {aim[1]}"
        return "no pixel was given"

    if not any(k in move for k in ("x0", "y0", "x1", "y1")):
        if aim is None:
            return None, "no pixel was given"
        if len(distinct) < 2 or distinct[-1] != aim:
            return None, "no end was given"
        return (distinct[-2], distinct[-1]), ""
    try:
        start = (int(move["x0"]), int(move["y0"]))
        end = (int(move["x1"]), int(move["y1"]))
    except (KeyError, TypeError, ValueError):
        return None, told()
    if start not in given or end not in given or start == end:
        return None, told()
    return (start, end), ""


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

    def her_note(self, kind: str, text: str) -> str:
        situation = f"[{time.strftime('%H:%M')} | line {'up' if self.line.up else 'down'} | {SOURCE[kind]}]"
        data = self.brain.ask_json(f"{NOTE}\n{situation}\n{text}", NOTE_SCHEMA)
        note = " ".join(str(data.get("note", "")).split())
        if not note:
            raise RuntimeError("empty note")
        LOG.info("note: %s", note)
        return note

    def turn(self, kind: str, text: str):
        self.request = text
        self.note = ""
        self.used_tool = False
        self.aim = None
        self.given = []
        self.marks = []
        try:
            note = self.her_note(kind, text)
        except Exception as exc:
            LOG.exception("note failed: %s", exc)
            note = text.strip()
        self.note = note
        self.line.send_text(note)
        spoken = self.navigate(note)
        if spoken or self.note or self.used_tool:
            self.memory.add_turn(text, spoken or self.note)
        if kind in ("chat", "call", "typed"):
            self.memory.set_task(text)
        return spoken

    def navigate(self, note: str, ask=None) -> str:
        folder = run_dir() / "consult"
        folder.mkdir(exist_ok=True)
        shot = folder / "screen.png"
        if shot.exists():
            shot.unlink()
        ask = ask or (lambda prompt: ask_cursor(folder, prompt))
        trace = ""
        misses = 0
        for _ in range(CONFIG["brain"]["max_tool_steps"]):
            prompt = advisor_prompt(note, "up" if self.line.up else "down", self.memory.facts_block(), trace)
            (run_dir() / "advisor.txt").write_text(prompt, encoding="utf-8")
            try:
                move = ask(prompt)
            except Exception as exc:
                misses += 1
                LOG.exception("consult failed: %s", exc)
                if misses >= 2:
                    return f"The consult came back empty. {exc}"
                trace += f"The consult came back empty. {exc}\n"
                continue
            misses = 0
            self.line.send_text(json.dumps(move, ensure_ascii=False))
            if str(move.get("tool", "")).strip().casefold() == "say":
                spoken = str(move.get("text", "")).strip()
                if spoken:
                    return spoken
                trace += json.dumps(move, ensure_ascii=False) + "\nShe needs the words to say.\n"
                continue
            try:
                result = self.apply(move)
            except Exception as exc:
                result = f"bad arguments: {exc}"
            self.used_tool = True
            text = str(result)
            LOG.info("move %s -> %s", json.dumps(move, ensure_ascii=False), text)
            self.line.send_text(text)
            trace += json.dumps(move, ensure_ascii=False) + "\n" + text + "\n"
        return "I am still working on it."

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

    def keep(self, found: dict) -> dict:
        if "x" not in found:
            self.aim = None
            return found
        self.aim = (int(found["x"]), int(found["y"]))
        self.given.append(self.aim)
        return found

    def look(self, what: str) -> str:
        what = str(what).strip()
        if not what:
            return "Name what to look for."
        from organs import eyes

        png = eyes.overlay(eyes.screenshot(), None, self.marks)
        self.line.send_photo(png, what)
        words = (
            f"Find {what}. "
            "A list line is y then x then the name, on the 1000 grid of the screen. "
            "If a line names it, copy that y and x. "
            "If it is drawn and has no line, give that same y then x for where it sits. "
            "The answer is its name. "
            "The pointer plate is not the thing unless you were asked for the pointer. "
            "If you cannot see it, confident is false and omit y and x."
        )
        data = self.brain.ask_json(words, PASS, png)
        found = {"answer": str(data["answer"]), "confident": data["confident"] is True}
        spot = self.picked(found["answer"], what) if found["confident"] else None
        if spot:
            found["x"], found["y"] = eyes.center_px(spot)
        else:
            pixel = eyes.pixel_from_look(data)
            if pixel:
                found["x"], found["y"] = pixel
        found = self.keep(found)
        if "x" not in found:
            return f"She looked for {what}. No pixel. {found['answer']}"
        return f"She looked for {what}. {found['answer']} at pixel {found['x']} {found['y']}."

    def picked(self, answer: str, prompt: str):
        want, ask = answer.casefold().strip(), prompt.casefold().strip()
        hits = []
        for name, spot in self.marks:
            key = name.casefold().strip()
            if key and (key == want or key == ask or key in want or key in ask or ask in key):
                hits.append(spot)
        return hits[0] if len(hits) == 1 else None

    def survey(self, prompt: str) -> str:
        from organs import eyes

        raw = eyes.screenshot()
        found = self.brain.survey(raw, prompt)
        self.marks = [(item["name"], [item["y0"], item["x0"], item["y1"], item["x1"]]) for item in found]
        png = eyes.overlay(raw, None, self.marks)
        self.line.send_photo(png, prompt)
        return "marked " + ", ".join(item["name"] for item in found) if found else "marked none"

    def click(self, move: dict) -> str:
        from organs import eyes, hands

        point, told = click_pixel(self.aim, move)
        if point is None:
            return told
        x, y = point
        how = str(move.get("how") or "left")
        ax, ay = hands.aim(x, y)
        png = eyes.overlay(eyes.screenshot(), None, self.marks)
        (run_dir() / "aim.png").write_bytes(png)
        if abs(ax - x) > 2 or abs(ay - y) > 2:
            self.line.send_photo(png, "aim")
            return f"aimed {ax} {ay}"
        LOG.info("aim cursor %s %s before %s press %s %s", ax, ay, how, x, y)
        hands.strike(x, y, how)
        self.line.send_photo(png, "aim")
        self.marks = []
        return f"{how} click at {x} {y}"

    def drag(self, move: dict) -> str:
        from organs import hands

        ends, told = drag_ends(self.aim, self.given, move)
        if ends is None:
            return told
        (x0, y0), (x1, y1) = ends
        hands.drag(x0, y0, x1, y1)
        self.marks = []
        return f"stroke {x0} {y0} {x1} {y1}"

    def apply(self, move: dict) -> str:
        from organs import hands

        name = str(move.get("tool", "")).strip()
        key = name.casefold()
        if key == "look":
            return self.look(str(move.get("what", "")))
        if key == "survey":
            if not SECOND_VISION:
                return "survey is not connected"
            return self.survey(str(move.get("prompt") or move.get("what") or ""))
        if key == "click":
            return self.click(move)
        if key == "drag":
            return self.drag(move)
        if key == "type_text":
            if "text" not in move:
                return "bad arguments: text"
            hands.type_text(str(move["text"]))
            return "typed"
        if key == "press":
            if "keys" not in move:
                return "bad arguments: keys"
            hands.press(str(move["keys"]))
            return f"pressed {move['keys']}"
        if key == "run":
            if "command" not in move:
                return "bad arguments: command"
            return hands.run(str(move["command"]))
        if key == "remember":
            if "fact" not in move:
                return "bad arguments: fact"
            self.memory.remember(str(move["fact"]))
            return "remembered"
        if key == "call_owner":
            opening = str(move.get("opening") or "I am up. Do you want anything?")
            if self.line.up:
                return "the line is already up; just speak"
            try:
                self.line.dial()
            except Exception as exc:
                return f"he did not answer: {exc}"
            self.speak(opening)
            return "he answered and can see the screen; say what he should hear next, or hang_up"
        if key == "hang_up":
            self.line.hang()
            return "hung up"
        return f"unknown tool {name}"


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
