import base64
import io
import json
import msvcrt
import os
import queue
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
from PIL import Image
from next import CONFIG, ROOT, STATE, contract
from next import desktop
from next.models import Child, Interrupted, Models
from next.speech import transcription_command
from next.telegram import Line

class EmptyFuel(Exception):
    pass


END, RESTART = object(), object()


def process(args, cwd=ROOT):
    result = subprocess.run(args, cwd=cwd, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=1800)
    if result.returncode:
        raise RuntimeError((result.stderr + result.stdout).decode("utf-8", "replace") or f"Exit {result.returncode}")
    return result.stdout


class Trident:
    def __init__(self):
        STATE.mkdir(parents=True, exist_ok=True)
        self.seat = (STATE / "seat.lock").open("a+b")
        self.seat.write(b"\0")
        self.seat.seek(0)
        msvcrt.locking(self.seat.fileno(), msvcrt.LK_NBLCK, 1)
        self.note_path = STATE / "memory.json"
        self.memory = json.loads(self.note_path.read_text(encoding="utf-8"))["note"] if self.note_path.exists() else ""
        self.run_dir = Path(tempfile.mkdtemp(prefix="run_", dir=STATE))
        self.events = queue.Queue()
        self.line = Line(self.events)
        self.system, self.tools = contract(self)
        self.methods = {tool["function"]["name"]: getattr(self, tool["function"]["name"]) for tool in self.tools}
        self.requests = int(os.environ.get("TRIDENT_REQUESTS", "0"))
        self.pending_restart = False
        self.models = Models(self.run_dir, self.line.send_text, self.checkpoint)
        self.line.checkpoint = self.checkpoint

    def owner_pending(self):
        with self.events.mutex:
            return any(kind in ("text", "audio") for kind, _ in self.events.queue)

    def checkpoint(self):
        if self.owner_pending():
            raise Interrupted("New owner input interrupted the current request.")

    @staticmethod
    def image_info(png):
        width, height = Image.open(io.BytesIO(png)).size
        return (f"Whole-screen image: width {width} pixels, height {height} pixels. "
                f"Grid x = pixel_x * 1000 / {width - 1}; grid y = pixel_y * 1000 / {height - 1}. "
                "Use named arguments y (vertical) and x (horizontal).")

    def charge(self):
        if self.requests >= CONFIG["brain"]["request_limit"]:
            raise EmptyFuel(f"request limit {CONFIG['brain']['request_limit']}")
        self.requests += 1
        os.environ["TRIDENT_REQUESTS"] = str(self.requests)

    def request_text(self, new, source):
        return "\n".join(["Note:\n" + self.memory, f"Call: {'up' if self.line.up else 'down'}",
                          "Wake" if source == "Wake" else source + ":\n" + new])

    def completion(self, messages):
        self.checkpoint()
        self.charge()
        self.models.brain()
        cfg = CONFIG["brain"]
        body = {"model": "Gemma", "messages": messages, "tools": self.tools, "tool_choice": "auto",
                "parallel_tool_calls": False, "chat_template_kwargs": {"enable_thinking": cfg["thinking"], "preserve_thinking": True},
                "max_tokens": cfg["max_tokens"], "temperature": cfg["temperature"],
                "top_k": cfg["top_k"], "top_p": cfg["top_p"], "min_p": cfg["min_p"]}
        request = urllib.request.Request(self.models.url + "/v1/chat/completions", json.dumps(body).encode("utf-8"),
                                         {"Content-Type": "application/json"})
        def receive():
            try:
                with urllib.request.urlopen(request, timeout=600) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                raise RuntimeError(error.read().decode("utf-8", "replace")) from error
        with ThreadPoolExecutor(max_workers=1) as worker:
            response = worker.submit(receive)
            try:
                while True:
                    self.checkpoint()
                    try:
                        raw = response.result(timeout=0.05)
                        break
                    except FutureTimeout:
                        if response.done():
                            raw = response.result()
                            break
            except Interrupted:
                self.models.stop()
                raise
        self.checkpoint()
        self.line.send_text(raw.decode("utf-8"))
        response = json.loads(raw)
        choice = response["choices"][0]
        if choice["finish_reason"] == "length":
            raise RuntimeError(f"Model response reached max_tokens={cfg['max_tokens']}; usage={response['usage']}; request ended.")
        return choice["message"]

    def turn(self, text, source):
        prompt = self.request_text(text, source)
        self.line.send_text(prompt)
        messages = [{"role": "system", "content": self.system}, {"role": "user", "content": prompt}]
        while True:
            reply = self.completion(messages)
            messages.append(reply)
            calls = reply.get("tool_calls") or []
            if not calls:
                return
            for call in calls:
                self.checkpoint()
                name = call["function"]["name"]
                try:
                    result = self.methods[name](**json.loads(call["function"]["arguments"]))
                except (EmptyFuel, Interrupted):
                    raise
                except Exception as error:
                    result = str(error)
                if result is END or result is RESTART:
                    return result
                if isinstance(result, (bytes, tuple)):
                    words, png = ("", result) if isinstance(result, bytes) else result
                else:
                    words, png = str(result), None
                content = self.request_text(words, "Tool result from " + name)
                if png is not None:
                    content += "\n" + self.image_info(png)
                self.line.send_text(content)
                if png is not None:
                    content = [{"type": "text", "text": content},
                               {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}}]
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})

    def speak(self, text: str):
        if self.line.up:
            pcm = self.models.speech("TTS", [sys.executable, "-m", "next.speech", str(self.run_dir / "voice.pt")], text.encode("utf-8"))
            self.line.wait(self.line.speak(pcm), len(pcm) / 96000 + 30)
            return "The words were spoken on the call."
        self.line.send_text(text)
        return "The words were sent as a Telegram message."

    def look(self):
        png = desktop.picture()
        self.line.send_file(png, "screen.png")
        self.line.wait(self.line.show(png))
        return png

    point = staticmethod(desktop.move)
    click = staticmethod(desktop.click)
    stroke = staticmethod(desktop.stroke)
    type_text = staticmethod(desktop.type_text)
    press = staticmethod(desktop.press)
    run = staticmethod(desktop.run)

    def note(self, text: str):
        path = self.note_path.with_suffix(".tmp")
        path.write_text(json.dumps({"note": text}, ensure_ascii=False), encoding="utf-8")
        path.replace(self.note_path)
        self.memory = text
        return "The note was replaced."

    def call_owner(self, opening: str):
        if self.line.up:
            return "The call is already up."
        self.line.wait(self.line.place(), 150)
        self.speak(opening)
        return "The call is answered."

    def hang_up(self):
        self.line.wait(self.line.hang())
        return "The call is down."

    def cursor(self, prompt, folder, writing=False):
        self.charge()
        versions = Path(os.environ["LOCALAPPDATA"]) / "cursor-agent" / "versions"
        version = max((p for p in versions.iterdir() if (p / "node.exe").is_file() and (p / "index.js").is_file()),
                      key=lambda p: p.stat().st_mtime)
        args = [str(version / "node.exe"), str(version / "index.js"), "-p", "--trust",
                "--model", CONFIG["cloud"]["model"], "--output-format", "text", "--workspace", str(folder)]
        args += ["--force"] if writing else ["--mode", "ask"]
        if writing:
            raw = process(args + [prompt], cwd=folder)
        else:
            child = Child(args + [prompt], folder, "advisor", cwd=folder)
            try:
                code = child.wait(1800, self.checkpoint)
                if code:
                    raise RuntimeError(child.error(code))
                raw = child.output.read_bytes()
            finally:
                child.close()
        answer = raw.decode("utf-8", "replace")
        if not answer.strip():
            raise RuntimeError("cursor-agent returned nothing.")
        return answer

    def consult(self, question: str):
        folder = self.run_dir / "consult"
        folder.mkdir(exist_ok=True)
        png = self.look()
        shot = folder / "screen.png"
        shot.write_bytes(png)
        prompt = f"You advise Gemma. Advice only; do not act, edit files or launch agents. Read {shot}; it is the whole current screen with its pointer arrow.\n{self.image_info(png)}\nLocate each target in image pixels, calculate its grid coordinates using the image dimensions, and give named arguments only: point(y=vertical, x=horizontal). Never give ambiguous positional pairs. Check that each computed point falls on the target in this image. Give one next action and what look must verify, using point, click, stroke, type_text or press as appropriate. Do not assume a previous action succeeded: the current image is the evidence. If you cannot see a target, say so. You do not execute anything; Gemma decides.\nNote:\n{self.memory}\nQuestion:\n{question}"
        self.line.send_text(prompt)
        try:
            answer = "The advisor says: " + self.cursor(prompt, folder)
        except (EmptyFuel, Interrupted):
            raise
        except Exception as error:
            answer = str(error)
        return answer, png

    def heal(self, goal: str):
        if self.line.state != "down":
            raise RuntimeError("heal requires Call: down.")
        with (STATE / "writer.lock").open("a+b") as lock:
            lock.write(b"\0")
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            if process(["git", "branch", "--show-current"]).strip() != b"runner-h":
                raise RuntimeError("heal requires runner-h.")
            if process(["git", "status", "--porcelain"]).strip():
                raise RuntimeError("heal requires a clean checkout.")
            before = process(["git", "rev-parse", "HEAD"])
            document = (ROOT / "README.md").read_bytes().decode("utf-8")
            prefix, task = document.split("## TASK\n", 1)
            headers, old_goal = task.split("Goal: ", 1)
            suffix = old_goal.split("\nEvidence:", 1)[1]
            prompt = prefix + "## TASK\n" + headers + "Goal: " + goal + "\nEvidence:" + suffix
            self.line.send_text(prompt)
            answer = self.cursor(prompt, ROOT, writing=True)
            self.line.send_text(answer)
            if process(["git", "status", "--porcelain"]).strip():
                raise RuntimeError("Heal left uncommitted changes; restart blocked.")
            if process(["git", "rev-parse", "HEAD"]) == before:
                return "Heal changed nothing.\n" + answer
            tags = process(["git", "for-each-ref", "--points-at=HEAD", "--format=%(objecttype)", "refs/tags"])
            if b"tag" not in tags.splitlines():
                raise RuntimeError("Heal did not leave an annotated tag; restart blocked.")
            if self.line.wait(self.line.reserve_restart()):
                self.line.send_text("Heal committed and tagged. Restarting with the note.")
                return RESTART
            self.pending_restart = True
            return "Heal committed and tagged. Restart waits until Call: down."

    def done(self, summary: str):
        self.line.send_text(summary)
        return END

    def serve(self):
        self.events.put(("wake", "Wake"))
        self.line.start()
        self.line.send_file(self.system.encode("utf-8"), "Organism.txt")
        self.line.send_text(self.system)
        self.line.send_text(json.dumps(self.tools, ensure_ascii=False))
        while True:
            kind, payload = self.events.get()
            try:
                if kind == "call_down":
                    if self.pending_restart and self.line.wait(self.line.reserve_restart()):
                        return RESTART
                    continue
                wake = kind == "wake"
                words, deferred = [], []
                while True:
                    if kind == "call_down":
                        deferred.append((kind, payload))
                    elif kind != "wake":
                        if kind == "audio":
                            raw = self.models.speech("ASR", transcription_command(self.run_dir / "call.wav", payload))
                            payload = json.loads(raw)["text"]
                        if payload:
                            words.append(payload)
                    if not self.owner_pending():
                        break
                    kind, payload = self.events.get()
                for event in deferred:
                    self.events.put(event)
                if not words and not wake:
                    continue
                result = self.turn("\n\n".join(words), "Owner words" if words else "Wake")
                if result is RESTART:
                    return result
                if self.requests >= CONFIG["brain"]["request_limit"]:
                    raise EmptyFuel(f"request limit {CONFIG['brain']['request_limit']}")
            except EmptyFuel as error:
                self.line.send_text(str(error))
                return
            except Exception as error:
                self.line.send_text(str(error))

    def stop(self):
        try:
            self.models.stop()
        finally:
            try:
                self.line.stop()
            finally:
                self.seat.close()


def main():
    trident = Trident()
    result = None
    try:
        result = trident.serve()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        if trident.line.client and trident.line.owner:
            trident.line.send_text(str(error))
        raise
    finally:
        trident.stop()
    if result is RESTART:
        os.execv(sys.executable, [sys.executable, str(ROOT / "trident.py")])
