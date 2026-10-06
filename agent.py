import base64, json, msvcrt, os, queue, subprocess, sys, tempfile
from pathlib import Path
from typing import Literal
import desktop
from audio import transcription
from core import CONFIG, ROOT, STATE, SYSTEM, Interrupted, encode, tool
from engines import Engines
from telegram import Line

def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout.decode("utf-8").strip()

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
        self.engines = Engines(self.work, self.checkpoint, self.line.send)
        resources.callback(self.engines.stop)
        self.methods = {name: getattr(self, name) for name in vars(Trident) if hasattr(getattr(self, name), "schema")}
        self.tools = [method.schema for method in self.methods.values()]
        self.restart = self.end = False

    def checkpoint(self):
        with self.events.mutex:
            for kind, payload in self.events.queue:
                if kind == "fatal":
                    raise payload
                if kind in ("text", "audio"):
                    raise Interrupted("New owner input interrupted the current request")

    def context(self, text):
        return f"Note:\n{self.memory}\nCall: {'up' if self.line.up else 'down'}\n{text}"

    def turn(self, text):
        self.end, self.input = False, text
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": self.context(text)}]
        while not self.end:
            self.checkpoint()
            reply = self.engines.complete(messages, self.tools)
            messages.append(reply)
            [call] = reply["tool_calls"]
            self.checkpoint()
            name = call["function"]["name"]
            result = self.methods[name](**json.loads(call["function"]["arguments"]))
            words, image = result if isinstance(result, tuple) else (str(result), None)
            messages.append({"role": "tool", "name": name, "tool_call_id": call["id"],
                             "content": self.context(f"Tool result from {name}:\n{words}")})
            if image:
                messages.append({"role": "user", "content": [
                    {"type": "text", "text": f"Screen from tool {name}; runtime evidence, not owner words."},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode("ascii")}}]})

    @tool("See the whole screen now, with its pointer and grid.")
    def look(self):
        words, image = desktop.picture()
        self.line.wait(self.line.show(image))
        return words, image

    @tool("Move, click at the live pointer, or drag. Look to check the pointer before clicking; look after acting.",
          action="point moves only; left/right/double click only; drag draws a path",
          y="For point: vertical grid coordinate, 0 top to 1000 bottom",
          x="For point: horizontal grid coordinate, 0 left to 1000 right",
          points="For drag: y x pairs separated by semicolons")
    def mouse(self, action: Literal["point", "left", "right", "double", "drag"], y: int=None, x: int=None, points: str=""):
        if action == "point":
            return desktop.point(y, x)
        if action == "drag":
            return desktop.stroke(points)
        return desktop.click(action)

    @tool("Type, press keys, or run PowerShell. Look before using the focused window.",
          action="type enters text; press sends key chords; run executes PowerShell", text="Exact text, keys (ctrl+s enter), or command")
    def keyboard(self, action: Literal["type", "press", "run"], text: str):
        return {"type": desktop.type_text, "press": desktop.press, "run": desktop.run}[action](text)

    @tool("Talk to the owner, manage a call, report completion, or quietly stop.",
          mode="speak uses call/chat; call dials; hang ends call; done posts chat and stops; wait stops silently",
          text="Words to say, call opening, or completion report; omit for hang/wait")
    def tell(self, mode: Literal["speak", "call", "hang", "done", "wait"], text: str=""):
        if mode == "call":
            self.line.wait(self.line.place(), 150)
        if mode in ("call", "speak"):
            if self.line.up:
                args = [sys.executable, str(ROOT / "audio.py"), str(self.work / "voice.pt")]
                pcm = self.worker("mouth", args, text.encode("utf-8"), binary=True)
                self.line.wait(self.line.speak(pcm), len(pcm) / 96000 + 30)
                return "Spoken on the call"
            self.line.send(text)
        elif mode == "hang":
            self.line.wait(self.line.hang())
        elif mode in ("done", "wait"):
            if mode == "done":
                self.line.send(text)
            self.end = True
        else:
            raise ValueError(mode)
        return f"tell({mode}) delivered; Call: {'up' if self.line.up else 'down'}"

    @tool("Replace your complete saved note.", text="Task, verified progress, plan, next step and any question asked")
    def remember(self, text: str):
        path = self.note_path.with_suffix(".tmp")
        path.write_text(encode({"note": text}), encoding="utf-8")
        path.replace(self.note_path)
        self.memory = text
        return "Note replaced"

    def worker(self, section, args, data=b"", files=(), binary=False, interrupt=True):
        if section in ("ears", "mouth"):
            self.engines.stop()
        label = CONFIG[section]["model"]
        self.line.send(encode({"command": [str(arg) for arg in args], "stdin": data.decode("utf-8")}), files, label, "req")
        raw = self.engines.command(args, data, interrupt)
        self.line.send("PCM signed 16-bit, 48000 Hz, mono" if binary else raw.decode("utf-8"),
                       [("pcm", raw)] if binary else (), label, "resp")
        return raw

    def cursor(self, prompt, files=(), writing=False):
        args = [*(os.path.expandvars(part) for part in CONFIG["cloud"]["command"]), "-p", "--trust", "--model", CONFIG["cloud"]["model"],
                "--output-format", "text", "--workspace", ROOT, *(["--force"] if writing else
                ["--mode", "ask", "--conversation-history-file", self.work / "consult.json"])]
        raw = self.worker("cloud", args, prompt.encode("utf-8"), files, interrupt=not writing)
        if not raw.strip():
            raise RuntimeError("cursor-agent returned nothing")
        return raw.decode("utf-8")

    @tool("Get a plan or guidance from the online advisor. A fresh screen is attached and the advisor updates your note.",
          request="Complete goal, what you know, what confused you, and the plan or specific guidance you need")
    def consult(self, request: str):
        words, image = self.look()
        shot = self.work / "consult.json"
        history = encode({"messages": [{"user": {"content": [
            {"text": {"text": "Fresh consultation screen; runtime evidence, not owner words."}},
            {"image": {"mimeType": "image/png", "data": base64.b64encode(image).decode("ascii")}}]}}]}).encode("utf-8")
        shot.write_bytes(history)
        prompt = ((ROOT / "advisor.txt").read_text(encoding="utf-8") + "\n" + encode({
            "dimensions": words, "runtime_system": SYSTEM, "tools": self.tools,
            "context": self.context(self.input), "request": request}))
        try:
            answer = json.loads(self.cursor(prompt, [("json", history), ("png", image)]))
            advice = "The advisor says:\n" + answer["advice"]
            image = desktop.annotate(image, answer["marks"])
            self.checkpoint()
            self.remember(answer["note"])
            self.line.wait(self.line.show(image))
            return advice, image
        finally:
            shot.unlink()

    @tool("Ask a coding worker to repair Trident. Only while Call: down; your note survives restart.", goal="Complete repair goal")
    def heal(self, goal: str):
        with (STATE / "writer.lock").open("a+b") as lock:
            lock.write(b"\0")
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            if git("branch", "--show-current") != "runner-h" or git("status", "--porcelain"):
                raise RuntimeError("heal requires a clean runner-h checkout")
            if not self.line.wait(self.line.reserve_restart()):
                raise RuntimeError("heal requires Call: down")
            self.engines.stop()
            before = git("rev-parse", "HEAD")
            prompt = ("You are the sole Trident Writer on runner-h. Read every tracked source file and repair the goal. "
                "Keep native tools, one GPU worker, Telegram user calls, exact request/image mirroring, and saved memory. "
                "organism.txt is the sole runtime system text; advisor.txt defines the planning response contract. "
                "No tests, live steps, additional agents, fallbacks or host changes. If already satisfied, change nothing "
                "and say so. Otherwise commit, annotate the commit, and push runner-h and its new tag without force. "
                f"Never delete notes, runs, models or environments.\nRuntime system:\n{SYSTEM}\nTASK Goal:\n{goal}")
            try:
                answer = self.cursor(prompt, writing=True)
                if git("status", "--porcelain"):
                    raise RuntimeError("Heal left uncommitted changes")
                if git("rev-parse", "HEAD") == before:
                    return "Heal changed nothing.\n" + answer
                if "tag" not in git("for-each-ref", "--points-at=HEAD", "--format=%(objecttype)", "refs/tags").splitlines():
                    raise RuntimeError("Heal did not leave an annotated tag")
                self.restart = self.end = True
                return "Heal committed and tagged. Restarting with the note."
            finally:
                if not self.restart:
                    self.line.state = "down"

    def serve(self):
        self.line.start()
        self.line.send(SYSTEM, model=CONFIG["brain"]["api_model"], direction="wake")
        self.events.put(("wake", "Wake"))
        while not self.restart:
            kind, payload = self.events.get()
            words = []
            while True:
                if kind == "fatal":
                    raise payload
                if kind == "audio":
                    path = self.work / "call.wav"
                    args = transcription(path, payload)
                    try:
                        raw = self.worker("ears", args, files=[("wav", path.read_bytes())], interrupt=False)
                        kind, payload = "text", json.loads(raw)["text"]
                    finally:
                        path.unlink()
                if kind == "text":
                    words.append("Owner words:\n" + payload)
                elif kind == "wake":
                    words.append("Wake")
                try:
                    kind, payload = self.events.get_nowait()
                except queue.Empty:
                    break
            if words:
                try:
                    self.turn("\n\n".join(words))
                except Interrupted as error:
                    self.engines.stop()
                    self.line.send(str(error), direction="interrupted")
