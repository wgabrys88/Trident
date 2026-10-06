import base64, io, json, msvcrt, os, queue, subprocess, sys, tempfile
from contextlib import ExitStack
from pathlib import Path
from PIL import Image
import desktop
from audio import transcription
from core import CONFIG, ROOT, STATE, SYSTEM, Interrupted, encode, timestamp, tool
from models import Models
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
        self.models = Models(self.work, self.checkpoint)
        resources.callback(self.models.stop)
        self.methods = {name: getattr(self, name) for name in vars(Trident) if hasattr(getattr(self, name), "schema")}
        self.tools = [method.schema for method in self.methods.values()]
        self.fuel = int(os.environ.get("TRIDENT_REQUESTS", "0"))
        self.restart = self.end = False

    def checkpoint(self):
        with self.events.mutex:
            for kind, payload in self.events.queue:
                if kind == "fatal":
                    raise payload
                if kind in ("text", "audio"):
                    raise Interrupted("New owner input interrupted the current request")

    def charge(self):
        if self.fuel >= CONFIG["brain"]["request_limit"]:
            raise RuntimeError(f"Request limit {self.fuel}")
        self.fuel += 1
        os.environ["TRIDENT_REQUESTS"] = str(self.fuel)

    def context(self, text):
        return f"Note:\n{self.memory}\nCall: {'up' if self.line.up else 'down'}\n{text}"

    def mirror(self, raw, direction):
        files = []
        if direction == "req":
            for message in json.loads(raw)["messages"]:
                content = message.get("content")
                if isinstance(content, list):
                    files += [("png", base64.b64decode(part["image_url"]["url"].split(",", 1)[1]))
                              for part in content if part["type"] == "image_url"]
        self.line.send(raw.decode("utf-8"), files, "Gemma", direction)

    def turn(self, text):
        self.end = False
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": self.context(text)}]
        cfg = CONFIG["brain"]
        while not self.end:
            self.checkpoint()
            self.charge()
            body = {"model": "Gemma", "messages": messages, "tools": self.tools, "tool_choice": "auto",
                "parallel_tool_calls": False, "chat_template_kwargs": {"enable_thinking": cfg["thinking"], "preserve_thinking": True},
                **{key: cfg[key] for key in ("temperature", "top_k", "top_p", "min_p", "max_tokens")}}
            raw = encode(body).encode("utf-8")
            self.mirror(raw, "req")
            raw = self.models.completion(raw)
            self.mirror(raw, "resp")
            choice = json.loads(raw)["choices"][0]
            if choice["finish_reason"] == "length":
                raise RuntimeError("Gemma reached max_tokens; request ended")
            reply = choice["message"]
            messages.append(reply)
            if not reply.get("tool_calls"):
                return
            for call in reply["tool_calls"]:
                self.checkpoint()
                name = call["function"]["name"]
                result = self.methods[name](**json.loads(call["function"]["arguments"]))
                words, png = result if isinstance(result, tuple) else (str(result), None)
                content = self.context(f"Tool result from {name}:\n{words}")
                if png:
                    content = [{"type": "text", "text": content}, {"type": "image_url",
                        "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}}]
                messages.append({"role": "tool", "name": name, "tool_call_id": call["id"], "content": content})
                if self.end:
                    return

    @tool("Speak on the current call, otherwise send Telegram text. Never dial.", text="Words to say to the owner")
    def speak(self, text: str):
        if self.line.up:
            args = [sys.executable, str(ROOT / "audio.py"), str(self.work / "voice.pt")]
            pcm = self.worker("TTS", args, text.encode("utf-8"), binary=True)
            self.line.wait(self.line.speak(pcm), len(pcm) / 96000 + 30)
            return "Spoken on the call"
        self.line.send(text)
        return "Sent to Telegram"

    @tool("See the whole screen now, with the pointer arrow and grid labels")
    def look(self):
        png = desktop.picture()
        self.line.wait(self.line.show(png))
        width, height = Image.open(io.BytesIO(png)).size
        return (f"Whole-screen image {width} x {height} pixels. Grid y = pixel_y * 1000 / {height - 1}; "
                f"x = pixel_x * 1000 / {width - 1}. Use named y and x arguments.", png)

    point = staticmethod(tool("Move only the pointer", y="Vertical screen grid: 0 top, 1000 bottom",
                             x="Horizontal screen grid: 0 left, 1000 right")(desktop.point))
    click = staticmethod(tool("Click at the live pointer", how="left, right or double")(desktop.click))
    stroke = staticmethod(tool("Drag through grid places", points="1 to 32 y x pairs separated by semicolons")(desktop.stroke))
    type_text = staticmethod(tool("Type into the focused window", text="Exact text to type")(desktop.type_text))
    press = staticmethod(tool("Press keys", keys="Chords separated by spaces; held keys joined by +, e.g. ctrl+s")(desktop.press))
    run = staticmethod(tool("Run PowerShell and return output", command="PowerShell command")(desktop.run))

    @tool("Replace the whole saved note", text="Task, verified progress, remaining work and any question asked")
    def note(self, text: str):
        path = self.note_path.with_suffix(".tmp")
        path.write_text(encode({"note": text}), encoding="utf-8")
        path.replace(self.note_path)
        self.memory = text
        return "Note replaced"

    @tool("Dial a video call to the owner", opening="Words spoken when the owner answers")
    def call_owner(self, opening: str):
        self.line.wait(self.line.place(), 150)
        self.speak(opening)
        return "Owner answered"

    @tool("End the call and stay available")
    def hang_up(self):
        self.line.wait(self.line.hang())
        return "Call: down"

    def worker(self, name, args, data=b"", files=(), binary=False, interrupt=True):
        if name in ("ASR", "TTS"):
            self.models.stop()
        label = {"ASR": CONFIG["ears"]["model"], "TTS": CONFIG["mouth"]["model"],
                 "Advisor": CONFIG["cloud"]["model"], "Writer": CONFIG["cloud"]["model"]}[name]
        self.line.send(encode({"command": [str(arg) for arg in args], "stdin": data.decode("utf-8")}), files, label, "req")
        raw = self.models.command(args, data, interrupt)
        self.line.send("PCM signed 16-bit, 48000 Hz, mono" if binary else raw.decode("utf-8"),
                       [("pcm", raw)] if binary else (), label, "resp")
        return raw

    def cursor(self, prompt, files=(), writing=False):
        self.charge()
        versions = Path(os.environ["LOCALAPPDATA"]) / "cursor-agent" / "versions"
        version = max((p for p in versions.iterdir() if (p / "node.exe").is_file() and (p / "index.js").is_file()),
                      key=lambda p: p.stat().st_mtime)
        args = [version / "node.exe", version / "index.js", "-p", "--trust", "--model", CONFIG["cloud"]["model"],
                "--output-format", "text", "--workspace", ROOT, *(["--force"] if writing else ["--mode", "ask"]), prompt]
        raw = self.worker("Writer" if writing else "Advisor", args, files=files, interrupt=not writing)
        if not raw.strip():
            raise RuntimeError("cursor-agent returned nothing")
        return raw.decode("utf-8")

    @tool("Ask a cloud advisor about a fresh screen. Its answer is advice; you decide.", question="Question for the advisor")
    def consult(self, question: str):
        words, png = self.look()
        shot = self.work / "consult.png"
        shot.write_bytes(png)
        context = self.context("Question:\n" + question)
        prompt = (f"You advise Gemma. Read the image at {shot}. Advice only: never act, edit, or launch agents.\n"
            f"{words}\nUse named point(y=vertical, x=horizontal) arguments. Locate targets in image pixels, then scale to "
            f"the whole-screen grid. Say what a look must verify.\n{context}")
        try:
            return "The advisor says: " + self.cursor(prompt, [("png", png)]), png
        finally:
            shot.unlink()

    @tool("Repair code or Organism with a coding agent, only while Call is down", goal="Exact repair goal")
    def heal(self, goal: str):
        if self.line.state != "down":
            raise RuntimeError("heal requires Call: down")
        with (STATE / "writer.lock").open("a+b") as lock:
            lock.write(b"\0")
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            if git("branch", "--show-current") != "runner-h" or git("status", "--porcelain"):
                raise RuntimeError("heal requires a clean runner-h checkout")
            if not self.line.wait(self.line.reserve_restart()):
                raise RuntimeError("heal requires Call: down")
            self.models.stop()
            before = git("rev-parse", "HEAD")
            prompt = ("You are the sole Trident Writer on runner-h. Read every tracked Python file, then repair the goal. "
                "Keep Gemma as decider, native tools, one GPU worker, Telegram user calls, exact request/image mirroring, "
                "and the saved note. organism.txt is the sole system text. No tests, live steps, agents, fallbacks or "
                "host changes. If already satisfied, change nothing and say so. Otherwise commit, create an annotated "
                "tag for the commit, and push runner-h and that tag without force. Never delete the note, runs, models "
                f"or environment.\nOrganism:\n{SYSTEM}\nTASK Goal:\n{goal}")
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

    @tool("Post a completion report to Telegram only; never speak", summary="Completion or blocked report")
    def done(self, summary: str):
        self.line.send(summary)
        self.end = True
        return "Done posted to Telegram"

    def serve(self):
        self.line.start()
        self.line.send(SYSTEM, model="Gemma", direction="wake")
        self.events.put(("wake", "Wake"))
        while not self.restart and self.fuel < CONFIG["brain"]["request_limit"]:
            kind, payload = self.events.get()
            words, wake = [], False
            while True:
                if kind == "fatal":
                    raise payload
                if kind == "audio":
                    path = self.work / "call.wav"
                    args = transcription(path, payload)
                    try:
                        raw = self.worker("ASR", args, files=[("wav", path.read_bytes())], interrupt=False)
                        words.append(json.loads(raw)["text"])
                    finally:
                        path.unlink()
                elif kind == "text":
                    words.append(payload)
                elif kind == "wake":
                    wake = True
                try:
                    kind, payload = self.events.get_nowait()
                except queue.Empty:
                    break
            if words or wake:
                try:
                    self.turn("Owner words:\n" + "\n\n".join(words) if words else "Wake")
                except Interrupted as error:
                    self.models.stop()
                    self.line.send(str(error), direction="interrupted")

if __name__ == "__main__":
    STATE.mkdir(parents=True, exist_ok=True)
    folder = ROOT / ("run_" + timestamp())
    folder.mkdir()
    with ExitStack() as resources:
        trident = Trident(folder, resources)
        try:
            trident.serve()
        except Exception as error:
            if trident.line.owner:
                trident.line.send(f"{type(error).__name__}: {error}", direction="blocked")
            raise
    if trident.restart:
        os.execv(sys.executable, [sys.executable, str(ROOT / "trident.py")])
