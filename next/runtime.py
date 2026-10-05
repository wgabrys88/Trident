import base64
import json
import msvcrt
import os
import queue
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from next import BIN, CONFIG, MODELS, ROOT, STATE, contract
from next import desktop
from next.speech import transcription_command
from next.telegram import Line

FLAGS = subprocess.CREATE_NO_WINDOW


class EmptyFuel(Exception):
    pass


class End:
    pass


class Restart:
    pass


def process(args, data=None, cwd=ROOT, timeout=1800):
    result = subprocess.run(args, input=data, cwd=cwd, capture_output=True, creationflags=FLAGS, timeout=timeout)
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
        self.server = None
        self.url = f"http://{CONFIG['brain']['host']}:{CONFIG['brain']['port']}"

    def charge(self):
        if self.requests >= CONFIG["brain"]["request_limit"]:
            raise EmptyFuel(f"request limit {CONFIG['brain']['request_limit']}")
        self.requests += 1
        os.environ["TRIDENT_REQUESTS"] = str(self.requests)

    def start_model(self):
        cfg = CONFIG["brain"]
        with socket.socket() as port:
            if port.connect_ex((cfg["host"], cfg["port"])) == 0:
                raise RuntimeError(f"Model port {cfg['port']} is already in use.")
        args = [str(BIN / "llama" / "llama-server.exe"), "--model", str(MODELS / cfg["model"]),
                "--mmproj", str(MODELS / cfg["mmproj"]), "--alias", "Gemma", "--jinja",
                "--no-context-shift", "--no-webui", "--log-disable"]
        for flag, value in {"host": cfg["host"], "port": cfg["port"], "ctx-size": cfg["context"],
                            "parallel": cfg["slots"], "n-gpu-layers": cfg["gpu_layers"], "threads": cfg["threads"],
                            "ubatch-size": cfg["ubatch"], "batch-size": cfg["ubatch"], "flash-attn": "off",
                            "cache-type-k": "f16", "cache-type-v": "f16",
                            "image-min-tokens": cfg["image_tokens"], "image-max-tokens": cfg["image_tokens"]}.items():
            args += ["--" + flag, str(value)]
        self.server = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, creationflags=FLAGS)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if self.server.poll() is not None:
                raise RuntimeError(f"llama-server exited {self.server.returncode}")
            try:
                with urllib.request.urlopen(self.url + "/health", timeout=2) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.25)
        self.stop_model()
        raise TimeoutError("llama-server startup timed out")

    def stop_model(self):
        server, self.server = self.server, None
        if server and server.poll() is None:
            server.terminate()
            server.wait()

    def gate(self, args, data=None):
        self.stop_model()
        try:
            return process(args, data, timeout=600)
        finally:
            self.start_model()

    def request_text(self, new):
        return "\n".join([self.memory, f"Call: {'up' if self.line.up else 'down'}", new])

    def completion(self, messages):
        self.charge()
        cfg = CONFIG["brain"]
        body = {"model": "Gemma", "messages": messages, "tools": self.tools, "tool_choice": "auto",
                "parallel_tool_calls": False, "chat_template_kwargs": {"enable_thinking": True},
                "max_tokens": cfg["max_tokens"], "temperature": cfg["temperature"],
                "top_k": cfg["top_k"], "top_p": cfg["top_p"], "min_p": cfg["min_p"]}
        request = urllib.request.Request(self.url + "/v1/chat/completions", json.dumps(body).encode("utf-8"),
                                         {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            raise RuntimeError(error.read().decode("utf-8", "replace")) from error
        self.line.send_text(raw.decode("utf-8"))
        choice = json.loads(raw)["choices"][0]
        if choice["finish_reason"] == "length":
            raise RuntimeError("Model response reached max_tokens; request ended.")
        return choice["message"]

    def turn(self, text):
        prompt = self.request_text(text)
        self.line.send_text(self.system)
        self.line.send_text(json.dumps(self.tools, ensure_ascii=False))
        self.line.send_text(prompt)
        messages = [{"role": "system", "content": self.system}, {"role": "user", "content": prompt}]
        while True:
            reply = self.completion(messages)
            messages.append(reply)
            calls = reply.get("tool_calls") or []
            if not calls:
                return
            for call in calls:
                name = call["function"]["name"]
                try:
                    result = self.methods[name](**json.loads(call["function"]["arguments"]))
                except EmptyFuel:
                    raise
                except Exception as error:
                    result = str(error)
                if isinstance(result, (End, Restart)):
                    return result
                if isinstance(result, (bytes, tuple)):
                    words, png = ("", result) if isinstance(result, bytes) else result
                    self.line.send_text(self.request_text(words))
                    content = [{"type": "text", "text": self.request_text(words)},
                               {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}}]
                else:
                    content = self.request_text(str(result))
                    self.line.send_text(content)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})

    def speak(self, text: str):
        if self.line.up:
            pcm = self.gate([sys.executable, "-m", "next.speech"], text.encode("utf-8"))
            self.line.wait(self.line.speak(pcm), len(pcm) / 96000 + 30)
            return "The words were spoken on the call."
        self.line.send_text(text)
        return "The words were sent as a Telegram message."

    def look(self):
        png = desktop.picture()
        self.line.send_file(png, "screen.png")
        self.line.wait(self.line.show(png))
        return png

    def point(self, y: int, x: int):
        return desktop.move(y, x)

    def click(self, how: str):
        return desktop.click(how)

    def stroke(self, points: str):
        return desktop.stroke(points)

    def type_text(self, text: str):
        return desktop.type_text(text)

    def press(self, keys: str):
        return desktop.press(keys)

    def run(self, command: str):
        return desktop.run(command)

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
        answer = process(args + [prompt], cwd=folder).decode("utf-8", "replace")
        if not answer.strip():
            raise RuntimeError("cursor-agent returned nothing.")
        return answer

    def consult(self, question: str):
        folder = self.run_dir / "consult"
        folder.mkdir(exist_ok=True)
        png = self.look()
        shot = folder / "screen.png"
        shot.write_bytes(png)
        grid = next(line for line in self.system.splitlines() if line.startswith("The screen is a grid")).split(". ", 1)[0] + "."
        prompt = f"You advise Gemma. Advice only; do not act, edit files or launch agents. Read {shot}; it is the whole current screen with its pointer arrow.\n{grid}\nNote:\n{self.memory}\nQuestion:\n{question}"
        self.line.send_text(prompt)
        try:
            answer = "The advisor says: " + self.cursor(prompt, folder)
        except EmptyFuel:
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
                return Restart()
            self.pending_restart = True
            return "Heal committed and tagged. Restart waits until Call: down."

    def done(self, summary: str):
        self.line.send_text(summary)
        return End()

    def serve(self):
        self.events.put(("text", "Wake"))
        self.line.start()
        self.start_model()
        self.line.send_file(self.system.encode("utf-8"), "Organism.txt")
        while True:
            kind, payload = self.events.get()
            try:
                if kind == "call_down":
                    if self.pending_restart and self.line.wait(self.line.reserve_restart()):
                        return Restart()
                    continue
                if kind == "audio":
                    raw = self.gate(transcription_command(self.run_dir / "call.wav", payload))
                    payload = json.loads(raw)["text"]
                    if not payload:
                        continue
                result = self.turn(payload)
                if isinstance(result, Restart):
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
            self.stop_model()
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
    if isinstance(result, Restart):
        os.execv(sys.executable, [sys.executable, str(ROOT / "trident.py")])
