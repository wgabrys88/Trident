import json, msvcrt, os, queue, subprocess, sys, tempfile, time
from pathlib import Path
from typing import Literal
import desktop
from acts import NAMES
from audio import transcription
from core import CONFIG, ROOT, STATE, Interrupted, encode
from engines import Engines
from telegram import Line, tool_record

TEACH = (
    "The actor finished work on the owner's computer. Teach the local model that will next use the same system prompt and the same tool descriptions. "
    "Return only one JSON object with keys prompt, tools, and teaching. "
    "prompt is the full system prompt. tools is the full tool definition object with the same tool names and the same parameter names. "
    "Change descriptions and the prompt so the small model would have done this work the way you did. "
    "teaching is what changed and why. Keep every fact, decision, place, and open step the next task needs."
)

class Trident:
    def __init__(self, folder, resources):
        self.work = Path(resources.enter_context(tempfile.TemporaryDirectory(prefix=".work_", dir=folder)))
        self.lock = resources.enter_context((STATE / "seat.lock").open("a+b"))
        self.lock.write(b"\0")
        self.lock.seek(0)
        msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
        self.note_path = STATE / "memory.json"
        self.memory = json.loads(self.note_path.read_text(encoding="utf-8"))["note"] if self.note_path.exists() else ""
        self.events = queue.Queue()
        self.line = Line(self.events, folder)
        resources.callback(self.line.stop)
        self.line.checkpoint = self.checkpoint
        self.engines = Engines(self.work, self.checkpoint)
        resources.callback(self.engines.close)
        self.learner = self.learn_err = None
        self.lessons = 0
        resources.callback(self.close_learner)
        self.methods = {"look": self.look, "mouse": self.mouse, "keyboard": self.keyboard,
                        "tell": self.tell, "remember": self.remember}
        if set(self.methods) != set(NAMES):
            raise RuntimeError("Tool names do not match the mechanism")
        self.end = self.leave = False

    def checkpoint(self):
        with self.events.mutex:
            for kind, payload in self.events.queue:
                if kind == "fatal":
                    raise payload
                if kind in ("text", "audio"):
                    raise Interrupted("New owner input interrupted the current request")

    def close_learner(self):
        if self.learner and self.learner.poll() is None:
            self.learner.terminate()
            try:
                self.learner.wait(5)
            except subprocess.TimeoutExpired:
                self.learner.kill()
                self.learner.wait(5)
        if self.learn_err and not self.learn_err.closed:
            self.learn_err.close()

    def learner_ok(self):
        code = self.learner.poll()
        if code is None:
            return
        self.learn_err.flush()
        raise RuntimeError(f"Learner exited {code}\n{(self.line.folder / 'learn.err').read_text(encoding='utf-8')}")

    def gpt(self, prompt, image):
        args = [*(os.path.expandvars(part) for part in CONFIG["cloud"]["command"]), "-p", "--trust", "--model", CONFIG["cloud"]["model"],
                "--output-format", "text", "--workspace", str(ROOT), "--mode", "ask"]
        if image:
            args += ["--image", str(image)]
        raw = self.engines.command(args, prompt.encode("utf-8")).decode("utf-8").strip()
        if not raw:
            raise RuntimeError("cursor-agent returned nothing")
        return raw

    def exchange(self, step, request, response, result):
        folder = self.line.folder / "gpt"
        folder.mkdir(exist_ok=True)
        path = folder / f"{step:05}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(encode({"request": request, "response": response, "result": result}), encoding="utf-8")
        temporary.replace(path)

    def wait_file(self, path):
        while not path.exists():
            self.learner_ok()
            self.checkpoint()
            time.sleep(0.05)

    def teach(self):
        folder = self.line.folder
        files = sorted((folder / "gpt").glob("*.json"))
        if not files:
            return
        mark = folder / "studied"
        while True:
            self.learner_ok()
            self.checkpoint()
            try:
                current = mark.read_text(encoding="utf-8")
            except FileNotFoundError:
                current = ""
            if current == files[-1].name:
                break
            time.sleep(0.05)
        self.lessons += 1
        name = f"{self.lessons:05}"
        teach = folder / "teach"
        teach.mkdir(exist_ok=True)
        notes = (STATE / "learn.txt").read_text(encoding="utf-8") if (STATE / "learn.txt").exists() else ""
        trace = "\n\n".join(path.read_text(encoding="utf-8") for path in files)
        request = (f"{TEACH}\n\nCurrent prompt:\n{(ROOT / 'organism.txt').read_text(encoding='utf-8')}\n"
                   f"Current tools:\n{(ROOT / 'tools.json').read_text(encoding='utf-8')}\nNotes:\n{notes}\nTrace:\n{trace}")
        (teach / f"{name}.request.txt").write_text(request, encoding="utf-8")
        raw = self.gpt(request, None)
        temporary = teach / f"{name}.tmp"
        temporary.write_text(raw, encoding="utf-8")
        temporary.replace(teach / f"{name}.json")
        self.wait_file(teach / f"{name}.applied")

    def task(self, words):
        self.end = False
        prompt = (ROOT / "organism.txt").read_text(encoding="utf-8")
        tools = (ROOT / "tools.json").read_text(encoding="utf-8")
        transcript = "\n\n".join(f"Owner words:\n{part}" for part in words)
        image, step = None, 0
        while not self.end:
            self.checkpoint()
            self.learner_ok()
            request = (f"{prompt}\n\nTools:\n{tools}\n\nNote:\n{self.memory}\n"
                       f"Call: {'up' if self.line.up else 'down'}\n\n{transcript}")
            response = self.gpt(request, image)
            image = None
            call = json.loads(response)
            name, arguments = call["name"], call["arguments"]
            result = self.methods[name](**arguments)
            text, shot = result if isinstance(result, tuple) else (str(result), None)
            if name != "look":
                self.line.send(tool_record(name, arguments, text), model=CONFIG["cloud"]["model"], direction="tool")
            step += 1
            self.exchange(step, request, response, text)
            if shot:
                image = self.work / "screen.png"
                image.write_bytes(shot)
            transcript += f"\n\nTool {name} returned:\n{text}"

    def hear(self, payload):
        path = self.work / "call.wav"
        try:
            heard = json.loads(self.engines.command(transcription(path, payload), interrupt=False))["text"]
            self.line.send(f"EARS -> TRIDENT\n\n{heard}\n", model=CONFIG["ears"]["model"], direction="resp")
            return heard
        finally:
            path.unlink()

    def look(self):
        image = desktop.picture()[1]
        y, x = desktop.position()
        words = f"Screen captured. Pointer y {y} x {x}."
        self.line.send(tool_record("look", {}, words), [("png", image)], model=CONFIG["cloud"]["model"], direction="tool")
        return words, image

    def mouse(self, action: Literal["left", "right", "double", "drag"], y: int, x: int, points: str = ""):
        if action == "drag":
            return desktop.stroke(points)
        return desktop.click(action, y, x)

    def keyboard(self, action: Literal["type", "press", "run"], text: str):
        return {"type": desktop.type_text, "press": desktop.press, "run": desktop.run}[action](text)

    def tell(self, mode: Literal["speak", "call", "hang", "done", "wait", "stop"], text: str = ""):
        if mode == "call":
            self.line.wait(self.line.place(), 150)
        if mode in ("call", "speak"):
            if self.line.up:
                pcm = self.engines.say(text)
                self.line.wait(self.line.speak(pcm), len(pcm) / 96000 + 30)
                self.line.send(f"GEMMA -> OWNER\n\n{text}\n", model=CONFIG["mouth"]["model"], direction="speak")
                return "Spoken on the call"
            self.line.send(f"GEMMA -> OWNER\n\n{text}")
        elif mode == "hang":
            self.line.wait(self.line.hang())
        elif mode == "stop":
            if self.line.up:
                self.line.wait(self.line.hang())
            self.leave = self.end = True
        elif mode in ("done", "wait"):
            if mode == "done":
                self.line.send(f"GEMMA -> OWNER\n\n{text}")
            self.end = True
        else:
            raise ValueError(mode)
        return f"tell({mode}) delivered; Call: {'up' if self.line.up else 'down'}"

    def remember(self, text: str):
        path = self.note_path.with_suffix(".tmp")
        path.write_text(encode({"note": text}), encoding="utf-8")
        path.replace(self.note_path)
        self.memory = text
        return "Note replaced"

    def serve(self):
        self.line.start()
        self.engines.mouth_up()
        self.engines.brain()
        self.learn_err = (self.line.folder / "learn.err").open("w", encoding="utf-8")
        self.learner = subprocess.Popen([sys.executable, "-u", str(ROOT / "learn.py"), str(self.line.folder), str(os.getpid())],
                                        cwd=ROOT, stdin=subprocess.DEVNULL, stdout=self.learn_err, stderr=subprocess.STDOUT,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
        self.wait_file(self.line.folder / "learner.ready")
        while not self.leave:
            kind, payload = self.events.get()
            words = []
            while True:
                if kind == "fatal":
                    raise payload
                if kind == "audio":
                    kind, payload = "text", self.hear(payload)
                if kind == "text":
                    words.append(payload)
                try:
                    kind, payload = self.events.get_nowait()
                except queue.Empty:
                    break
            if not words:
                continue
            try:
                self.task(words)
                self.teach()
            except Interrupted as error:
                self.line.send(f"TRIDENT -> OWNER\n\n{error}", direction="interrupted")
