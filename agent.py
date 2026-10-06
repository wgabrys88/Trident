import base64, json, msvcrt, os, queue, tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal
import desktop
from audio import transcription
from core import CONFIG, ROOT, STATE, SYSTEM, Interrupted, encode, tool
from engines import Engines
from telegram import Line, call_text, context_limit, tool_record

def drop_image(message):
    content = message.get("content")
    if isinstance(content, list):
        message["content"] = [part for part in content if part.get("type") != "image_url"]

def transcript(messages):
    blocks = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            content = "\n".join(part["text"] for part in content if part.get("type") == "text")
        lines = [content] if content else []
        for call in message.get("tool_calls") or []:
            function = call["function"]
            lines.append("Tool call:\n" + call_text(function["name"], function.get("arguments") or {}))
        if lines:
            blocks.append("\n\n".join(lines))
    return "\n\n".join(blocks)

def split_history(history):
    starts = [index for index, message in enumerate(history) if message["role"] == "assistant"]
    if len(starts) <= 2:
        return None
    return starts[-2]

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
        self.end = self.leave = False
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
        self.end, self.input, history = False, text, []
        while not self.end:
            self.checkpoint()
            reply, used = self.engines.complete([
                {"role": "system", "content": SYSTEM}, {"role": "user", "content": self.context(text)},
                *history], self.tools)
            if used > CONFIG["compact_at"]:
                history = self.compact(history, used)
            [call] = reply["tool_calls"]
            self.checkpoint()
            name = call["function"]["name"]
            arguments = json.loads(call["function"]["arguments"])
            result = self.methods[name](**arguments)
            words, image = result if isinstance(result, tuple) else (str(result), None)
            if name not in ("look", "consult"):
                self.line.send(tool_record(name, arguments, words), model=CONFIG["brain"]["api_model"], direction="tool")
            history.append({"role": "assistant", "content": reply.get("content"), "tool_calls": reply["tool_calls"]})
            history.append({"role": "tool", "name": name, "tool_call_id": call["id"],
                            "content": f"Tool result from {name}:\n{words}\n\nContext: {used} of {context_limit()}"})
            if image:
                for message in history:
                    drop_image(message)
                history.append({"role": "user", "content": [
                    {"type": "text", "text": f"Screen from tool {name}; runtime evidence, not owner words."},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode("ascii")}}]})

    def compact(self, history, used):
        cut = split_history(history)
        if cut is None:
            return history
        text = transcript(history[:cut])
        self.line.send(f"TRIDENT -> GEMMA\n\nContext before: {used} of {context_limit()}\n\n"
                       "Rewrite it shorter by meaning, and keep every fact, decision, place, and open step.\n\n"
                       f"{text}\n", model=CONFIG["brain"]["api_model"], direction="compact")
        return [{"role": "user", "content": "Earlier steps, rewritten shorter; not owner words.\n" + self.engines.rewrite(text)}, *history[cut:]]

    @tool("See the screen, the pointer, and the grid.")
    def look(self):
        words, image = desktop.picture()
        self.line.wait(self.line.show(image))
        self.line.send(tool_record("look", {}, words) + "PNG attached.\n", [("png", image)], model=CONFIG["brain"]["api_model"], direction="tool")
        return words, image

    @tool("Ask the advisor. It sees the screen and runs its clicks once.", request="The goal and what you need")
    def consult(self, request: str):
        if self.paid():
            return "The helper cap was reached."
        _, image = self.look()
        shot = self.work / "consult.png"
        shot.write_bytes(image)
        prompt = (ROOT / "advisor.txt").read_text(encoding="utf-8") + "\n" + encode({
            "runtime_system": SYSTEM, "tools": self.tools,
            "context": self.context(self.input), "request": request})
        self.line.send(f"GEMMA -> ADVISOR\n\nRequest:\n{request}\n", model=CONFIG["cloud"]["model"], direction="req")
        try:
            answer = json.loads(self.cursor(prompt))
            ran = "\n".join(desktop.click(item["how"], item["y"], item["x"]) for item in answer["clicks"])
            self.line.send(f"ADVISOR -> GEMMA\n\nAdvice:\n{answer['advice']}\n\nClicks:\n{encode(answer['clicks'])}\n\nRan:\n{ran}\n",
                           model=CONFIG["cloud"]["model"], direction="resp")
            return "The advisor says:\n" + answer["advice"] + "\n\nRan:\n" + ran
        finally:
            shot.unlink()

    @tool("Click or drag at y and x.",
          action="left, right, double, or drag",
          y="Vertical place, 0 to 1000",
          x="Horizontal place, 0 to 1000",
          points="Drag path as y x pairs")
    def mouse(self, action: Literal["left", "right", "double", "drag"], y: int, x: int, points: str=""):
        if action == "drag":
            return desktop.stroke(points)
        return desktop.click(action, y, x)

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

    def cursor(self, prompt):
        args = [*(os.path.expandvars(part) for part in CONFIG["cloud"]["command"]), "-p", "--trust", "--model", CONFIG["cloud"]["model"],
                "--output-format", "text", "--workspace", str(ROOT), "--mode", "ask", "--image", str(self.work / "consult.png")]
        raw = self.worker(args, prompt.encode("utf-8"))
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

    def serve(self):
        self.line.start()
        self.engines.mouth_up()
        self.line.send(f"TRIDENT -> GEMMA\n\n{SYSTEM}", model=CONFIG["brain"]["api_model"], direction="wake")
        self.events.put(("wake", "Wake"))
        while not self.leave:
            kind, payload = self.events.get()
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
            if words:
                with (self.line.folder / "turns.txt").open("a", encoding="utf-8") as turn_log:
                    turn_log.write("\n\n".join(words) + "\n---\n")
                try:
                    self.turn("\n\n".join(words))
                except Interrupted as error:
                    self.engines.stop()
                    self.line.send(f"TRIDENT -> OWNER\n\n{error}", direction="interrupted")
