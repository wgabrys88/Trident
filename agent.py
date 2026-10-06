import base64, json, msvcrt, os, queue, subprocess, tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal
import desktop
from audio import transcription
from core import CONFIG, ROOT, STATE, SYSTEM, Interrupted, encode, tool
from engines import Engines
from telegram import Line, tool_record

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
        resources.callback(self.engines.close)
        self.methods = {name: getattr(self, name) for name in vars(Trident) if hasattr(getattr(self, name), "schema")}
        self.tools = [method.schema for method in self.methods.values()]
        self.restart = self.end = self.leave = False
        self.carried = None
        self.helpers = 0

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
            self.carried = None
            messages.append(reply)
            [call] = reply["tool_calls"]
            self.checkpoint()
            name = call["function"]["name"]
            arguments = json.loads(call["function"]["arguments"])
            result = self.methods[name](**arguments)
            words, image = result if isinstance(result, tuple) else (str(result), None)
            if name not in ("look", "consult", "delegate", "heal"):
                self.line.send(tool_record(name, arguments, words), model=CONFIG["brain"]["api_model"], direction="tool")
            messages[1]["content"] = self.context(text)
            messages.append({"role": "tool", "name": name, "tool_call_id": call["id"],
                             "content": f"Tool result from {name}:\n{words}"})
            if image:
                messages = [item for item in messages if not isinstance(item.get("content"), list)]
                messages.append({"role": "user", "content": [
                    {"type": "text", "text": f"Screen from tool {name}; runtime evidence, not owner words."},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode("ascii")}}]})

    @tool("See the screen, the pointer, and the grid.")
    def look(self):
        words, image = desktop.picture()
        self.line.wait(self.line.show(image))
        self.line.send(tool_record("look", {}, words), [("png", image)], model=CONFIG["brain"]["api_model"], direction="tool")
        return words, image

    @tool("Move, click, or drag the pointer.",
          action="point, left, right, double, or drag",
          y="Vertical place, 0 to 1000",
          x="Horizontal place, 0 to 1000",
          points="Drag path as y x pairs")
    def mouse(self, action: Literal["point", "left", "right", "double", "drag"], y: int=None, x: int=None, points: str=""):
        if action == "point":
            return desktop.point(y, x)
        if action == "drag":
            return desktop.stroke(points)
        return desktop.click(action)

    @tool("Type, press keys, or run PowerShell.",
          action="type, press, or run", text="Text, keys, or the command")
    def keyboard(self, action: Literal["type", "press", "run"], text: str):
        return {"type": desktop.type_text, "press": desktop.press, "run": desktop.run}[action](text)

    @tool("Speak, call, hang up, finish, wait, or stop. Keep each message to a few short sentences. Before stop, rewrite the note with what is still open.",
          mode="speak, call, hang, done, wait, or stop",
          text="Words to say or the report")
    def tell(self, mode: Literal["speak", "call", "hang", "done", "wait", "stop"], text: str=""):
        if mode == "call":
            self.line.wait(self.line.place(), 150)
        if mode in ("call", "speak"):
            if self.line.up:
                pieces = self.engines.utterances(text)
                pool = ThreadPoolExecutor(max_workers=1)
                pending = pool.submit(self.engines.say, pieces[0])
                try:
                    for index, piece in enumerate(pieces):
                        pcm = pending.result()
                        if index + 1 < len(pieces):
                            pending = pool.submit(self.engines.say, pieces[index + 1])
                        self.line.wait(self.line.speak(pcm), len(pcm) / 96000 + 30)
                        self.line.send(f"GEMMA -> OWNER\n\n{piece}\n", model=CONFIG["mouth"]["model"], direction="speak")
                finally:
                    pool.shutdown(wait=False, cancel_futures=True)
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

    @tool("Replace the saved note.", text="The task, what you asked, and what is done")
    def remember(self, text: str):
        path = self.note_path.with_suffix(".tmp")
        path.write_text(encode({"note": text}), encoding="utf-8")
        path.replace(self.note_path)
        self.memory = text
        return "Note replaced"

    def worker(self, args, data=b"", interrupt=True):
        return self.engines.command(args, data, interrupt)

    def cursor(self, prompt, workspace, writing=False, interrupt=True):
        args = [*(os.path.expandvars(part) for part in CONFIG["cloud"]["command"]), "-p", "--trust", "--model", CONFIG["cloud"]["model"],
                "--output-format", "text", "--workspace", str(workspace), *(["--force"] if writing else
                ["--mode", "ask", "--conversation-history-file", self.work / "consult.json"])]
        raw = self.worker(args, prompt.encode("utf-8"), interrupt=interrupt)
        if not raw.strip():
            raise RuntimeError("cursor-agent returned nothing")
        return raw.decode("utf-8")

    def paid(self):
        self.helpers += 1
        if self.helpers < CONFIG["helper_cap"]:
            return False
        self.line.send("TRIDENT -> OWNER\n\nThe helper cap was reached.")
        if self.line.up:
            self.line.wait(self.line.hang())
        self.leave = self.end = True
        return True

    @tool("Ask the advisor how to do the work.", request="The goal and what you need")
    def consult(self, request: str):
        if self.paid():
            return "The helper cap was reached."
        words, image = self.look()
        shot = self.work / "consult.json"
        history = encode({"messages": [{"user": {"content": [
            {"text": {"text": "Fresh consultation screen; runtime evidence, not owner words."}},
            {"image": {"mimeType": "image/png", "data": base64.b64encode(image).decode("ascii")}}]}}]}).encode("utf-8")
        shot.write_bytes(history)
        prompt = ((ROOT / "advisor.txt").read_text(encoding="utf-8") + "\n" + encode({
            "dimensions": words, "runtime_system": SYSTEM, "tools": self.tools,
            "context": self.context(self.input), "request": request}))
        self.line.send(f"GEMMA -> ADVISOR\n\nRequest:\n{request}\n", model=CONFIG["cloud"]["model"], direction="req")
        try:
            answer = json.loads(self.cursor(prompt, ROOT))
            advice = "The advisor says:\n" + answer["advice"]
            image = desktop.annotate(image, answer["marks"])
            self.checkpoint()
            self.line.wait(self.line.show(image))
            self.line.send("ADVISOR -> GEMMA\n\nAnnotated screen", [("png", image)], model=CONFIG["cloud"]["model"], direction="screen")
            self.line.send(f"ADVISOR -> GEMMA\n\nAdvice:\n{answer['advice']}\n\nMarks:\n{encode(answer['marks'])}\n",
                           model=CONFIG["cloud"]["model"], direction="resp")
            return advice, image
        finally:
            shot.unlink()

    @tool("Have a helper do a job on this computer.", job="The job, in your own words")
    def delegate(self, job: str):
        if self.paid():
            return "The helper cap was reached."
        folder = Path(tempfile.mkdtemp(prefix="delegate_", dir=self.work))
        head, tree = git("rev-parse", "HEAD"), git("status", "--porcelain")
        self.line.send(f"GEMMA -> HELPER\n\n{job}\n", model=CONFIG["cloud"]["model"], direction="req")
        try:
            answer = self.cursor(job, folder, writing=True, interrupt=False)
        finally:
            if git("rev-parse", "HEAD") != head or git("status", "--porcelain") != tree:
                raise RuntimeError("delegate changed the checkout")
        self.line.send(f"HELPER -> GEMMA\n\n{answer}\n", model=CONFIG["cloud"]["model"], direction="resp")
        self.carried = answer
        return answer

    @tool("Fix your own code. Only while the call is down.", goal="What to repair")
    def heal(self, goal: str):
        if self.paid():
            return "The helper cap was reached."
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
                "Keep native tools, one GPU worker, Telegram user calls, one human-readable record per event "
                "(no raw dumps, screenshots once), and saved memory. "
                "organism.txt is the sole runtime system text; advisor.txt defines the planning response contract. "
                "No tests, live steps, additional agents, fallbacks or host changes. If already satisfied, change nothing "
                "and say so. Otherwise commit, annotate the commit, and push runner-h and its new tag without force. "
                f"Never delete notes, runs, models or environments.\nRuntime system:\n{SYSTEM}\nTASK Goal:\n{goal}")
            self.line.send(f"GEMMA -> HEAL\n\nGoal:\n{goal}\n", model=CONFIG["cloud"]["model"], direction="req")
            try:
                answer = self.cursor(prompt, ROOT, writing=True, interrupt=False)
                self.line.send(f"HEAL -> GEMMA\n\n{answer}\n", model=CONFIG["cloud"]["model"], direction="resp")
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
        self.engines.mouth_up()
        self.line.send(f"TRIDENT -> GEMMA\n\n{SYSTEM}", model=CONFIG["brain"]["api_model"], direction="wake")
        self.events.put(("wake", "Wake"))
        while not self.restart and not self.leave:
            try:
                kind, payload = self.events.get(timeout=120)
            except queue.Empty:
                kind, payload = "wake", "Wake"
            words = []
            while True:
                if kind == "fatal":
                    raise payload
                if kind == "audio":
                    path = self.work / "call.wav"
                    args = transcription(path, payload)
                    try:
                        raw = self.worker(args, interrupt=False)
                        heard = json.loads(raw)["text"]
                        self.line.send(f"EARS -> TRIDENT\n\n{heard}\n", model=CONFIG["ears"]["model"], direction="resp")
                        kind, payload = "text", heard
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
            if self.carried:
                words.insert(0, "The helper says:\n" + self.carried)
                self.carried = None
            if words:
                try:
                    self.turn("\n\n".join(words))
                except Interrupted as error:
                    self.engines.stop()
                    self.line.send(f"TRIDENT -> OWNER\n\n{error}", direction="interrupted")
