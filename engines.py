import _winapi, json, msvcrt, os, socket, subprocess, tempfile, time, urllib.error, urllib.request
import win32job, win32process
from concurrent.futures import ThreadPoolExecutor
from audio import call_pcm
from core import BIN, CONFIG, HARDWARE, MODELS, ROOT, encode
from hardware import environment
from telegram import brain_response

CFG = CONFIG["brain"]

class Child:
    """Own the entire worker job before its first instruction can execute."""
    def __init__(self, args, folder, data=b"", env=None):
        self.job = win32job.CreateJobObject(None, "")
        self.process = thread = None
        self.streams = [tempfile.TemporaryFile(dir=folder) for _ in range(3)]
        stdin, self.output, self.errors = self.streams
        try:
            stdin.write(data)
            stdin.seek(0)
            limits = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
            limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, limits)
            handles = [msvcrt.get_osfhandle(stream.fileno()) for stream in self.streams]
            for stream in self.streams:
                os.set_inheritable(stream.fileno(), True)
            startup = subprocess.STARTUPINFO(dwFlags=_winapi.STARTF_USESTDHANDLES, hStdInput=handles[0],
                hStdOutput=handles[1], hStdError=handles[2], lpAttributeList={"handle_list": handles})
            self.process, thread, self.pid, _ = _winapi.CreateProcess(str(args[0]),
                subprocess.list2cmdline([str(arg) for arg in args]), None, None, True,
                subprocess.CREATE_NO_WINDOW | 4, env, str(ROOT), startup)
            win32job.AssignProcessToJobObject(self.job, self.process)
            win32process.ResumeThread(thread)
        except BaseException:
            if self.process:
                _winapi.TerminateProcess(self.process, 1)
            self.close()
            raise
        finally:
            if thread:
                _winapi.CloseHandle(thread)
            for stream in self.streams:
                if not stream.closed:
                    os.set_inheritable(stream.fileno(), False)

    def poll(self):
        if _winapi.WaitForSingleObject(self.process, 0) == _winapi.WAIT_OBJECT_0:
            return _winapi.GetExitCodeProcess(self.process)

    def empty(self, deadline, checkpoint=lambda: None):
        while True:
            checkpoint()
            active = win32job.QueryInformationJobObject(self.job, win32job.JobObjectBasicAccountingInformation)["ActiveProcesses"]
            if not active:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Job still has {active} processes")
            time.sleep(0.05)

    def read(self, stream):
        stream.seek(0)
        return stream.read()

    def error(self):
        return self.read(self.errors).decode("utf-8", "replace") or f"Worker exited {self.poll()}"

    def result(self, checkpoint):
        self.empty(time.monotonic() + 1800, checkpoint)
        if self.poll():
            raise RuntimeError(self.error())
        return self.read(self.output)

    def close(self):
        if self.job:
            win32job.TerminateJobObject(self.job, 1)
            self.empty(time.monotonic() + 30)
            self.job.Close()
            self.job = None
        if self.process:
            _winapi.CloseHandle(self.process)
            self.process = None
        for stream in self.streams:
            stream.close()

class Engines:
    """Own GPU workers and deliver native chat payloads, independently of task behavior."""
    def __init__(self, folder, checkpoint, record):
        self.folder, self.checkpoint, self.record = folder, checkpoint, record
        self.child = self.mouth = None
        self.url = f"http://{CFG['host']}:{CFG['port']}"

    def stop(self):
        if self.child:
            self.child.close()
            self.child = None

    def close(self):
        self.stop()
        if self.mouth:
            self.mouth.close()
            self.mouth = None

    def listening(self, host, port):
        with socket.socket() as probe:
            return probe.connect_ex((host, port)) == 0

    def free(self, host, port):
        if self.listening(host, port):
            raise RuntimeError(f"Port {port} is already in use")

    def wait_ready(self, child, probe, label):
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            self.checkpoint()
            if child.poll() is not None:
                raise RuntimeError(child.error())
            if probe():
                return
            time.sleep(0.25)
        raise TimeoutError(f"{label} startup timed out")

    def command(self, args, data=b"", interrupt=True):
        child = Child(args, self.folder, data)
        try:
            return child.result(self.checkpoint if interrupt else lambda: None)
        finally:
            child.close()

    def healthy(self):
        try:
            with urllib.request.urlopen(self.url + "/health", timeout=2) as response:
                return response.status == 200
        except urllib.error.HTTPError as error:
            if error.code == 503:
                return False
            raise RuntimeError(error.read().decode("utf-8")) from error
        except urllib.error.URLError:
            return False

    def brain(self):
        if self.child:
            if self.child.poll() is not None:
                raise RuntimeError(self.child.error())
            return
        self.free(CFG["host"], CFG["port"])
        args = [BIN / "llama" / "llama-server.exe", "--model", MODELS / CFG["model"],
                "--mmproj", MODELS / CFG["mmproj"], "--chat-template-file", ROOT / CFG["template"],
                "--host", CFG["host"], "--port", str(CFG["port"]), "--threads", str(CFG["threads"]), "--parallel", "2",
                "--kv-unified", "--alias", CFG["api_model"], "--jinja", "--no-context-shift", "--no-webui", "--fit", "off",
                "--log-verbosity", "0", *CFG["server_args"], *HARDWARE["server_args"]]
        self.child = Child(args, self.folder, env=environment(HARDWARE))
        self.wait_ready(self.child, self.healthy, "llama-server")

    def mouth_ready(self):
        cfg = CONFIG["mouth"]
        try:
            with urllib.request.urlopen(f"http://{cfg['host']}:{cfg['port']}/health", timeout=2) as response:
                return response.status == 200
        except urllib.error.URLError:
            return False

    def mouth_up(self):
        cfg = CONFIG["mouth"]
        self.free(cfg["host"], cfg["port"])
        args = [BIN / "mouth" / "crispasr.exe", "--server", "--host", cfg["host"], "--port", str(cfg["port"]),
                "--backend", "chatterbox-nano", "-m", MODELS / cfg["model"], "--codec-model", MODELS / cfg["codec"],
                "--gpu-backend", "vulkan", "--tts-steps", "2", "--voice", ROOT / cfg["reference"], "--i-have-rights",
                "--no-spoken-disclaimer", "--no-punctuation"]
        self.mouth = Child(args, self.folder, env=environment(HARDWARE))
        self.wait_ready(self.mouth, self.mouth_ready, "mouth")
        self.say("Ready.")

    def say(self, text):
        cfg = CONFIG["mouth"]
        body = json.dumps({"input": text, "response_format": "wav"}).encode()
        request = urllib.request.Request(f"http://{cfg['host']}:{cfg['port']}/v1/audio/speech", body,
                                          {"Content-Type": "application/json"}, method="POST")
        return call_pcm(self.fetch(request, abort=False))

    def fetch(self, request, abort):
        def receive():
            try:
                with urllib.request.urlopen(request, timeout=600) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                raise RuntimeError(error.read().decode("utf-8")) from error
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(receive)
        try:
            while not future.done():
                self.checkpoint()
                time.sleep(0.05)
            self.checkpoint()
            return future.result()
        except BaseException:
            if abort:
                self.stop()
            raise
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def post(self, body, direction="resp"):
        self.brain()
        request = urllib.request.Request(self.url + "/v1/chat/completions", encode(body).encode("utf-8"),
                                          {"Content-Type": "application/json"})
        text = self.fetch(request, abort=True).decode("utf-8")
        self.record(brain_response(text), (), CFG["api_model"], direction)
        data = json.loads(text)
        choice = data["choices"][0]
        if choice["finish_reason"] == "length":
            raise RuntimeError("Model context or output capacity exhausted; request ended without replay")
        usage = data["usage"]
        return choice["message"], usage["prompt_tokens"] + usage["completion_tokens"]

    def complete(self, messages, tools):
        return self.post({**CFG["options"], "model": CFG["api_model"], "messages": messages, "tools": tools,
                          "tool_choice": "required", "parallel_tool_calls": False})

    def rewrite(self, text):
        message, _ = self.post({**CFG["options"], "model": CFG["api_model"], "messages": [
            {"role": "system", "content": "Rewrite it shorter by meaning, and keep every fact, decision, place, and open step."},
            {"role": "user", "content": text}], "chat_template_kwargs": {"enable_thinking": False}}, "rewrite")
        content = (message.get("content") or "").strip()
        if not content:
            raise RuntimeError("Compaction returned nothing")
        return content

    def utterances(self, text):
        # The splitter is the same local Gemma with tools off, not a second mind.
        message, _ = self.post({**CFG["options"], "model": CFG["api_model"], "messages": [
            {"role": "system", "content": "Split these words into natural spoken utterances of a few sentences each, well under twenty-five seconds, copying every word in order and separating the utterances with a blank line."},
            {"role": "user", "content": text}], "chat_template_kwargs": {"enable_thinking": False}})
        pieces = [part.strip() for part in (message.get("content") or "").split("\n\n") if part.strip()]
        if [word for part in pieces for word in part.split()] != text.split():
            raise RuntimeError("Speech split changed the words")
        return pieces
