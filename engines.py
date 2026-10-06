import _winapi, json, msvcrt, os, socket, subprocess, tempfile, time, urllib.error, urllib.request
import win32job, win32process
from concurrent.futures import ThreadPoolExecutor
from core import BIN, CONFIG, HARDWARE, MODELS, ROOT, encode
from hardware import environment
from telegram import brain_request, brain_response

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
        self.folder, self.checkpoint, self.record, self.child = folder, checkpoint, record, None
        self.url = f"http://{CFG['host']}:{CFG['port']}"

    def stop(self):
        if self.child:
            self.child.close()
            self.child = None

    def command(self, args, data=b"", interrupt=True):
        child = Child(args, self.folder, data)
        try:
            return child.result(self.checkpoint if interrupt else lambda: None)
        finally:
            child.close()

    def brain(self):
        if self.child:
            if self.child.poll() is not None:
                raise RuntimeError(self.child.error())
            return
        with socket.socket() as port:
            if port.connect_ex((CFG["host"], CFG["port"])) == 0:
                raise RuntimeError(f"Port {CFG['port']} is already in use")
        args = [BIN / "llama" / "llama-server.exe", "--model", MODELS / CFG["model"],
                "--mmproj", MODELS / CFG["mmproj"], "--chat-template-file", ROOT / CFG["template"],
                "--host", CFG["host"], "--port", str(CFG["port"]), "--threads", str(CFG["threads"]), "--parallel", "1",
                "--alias", CFG["api_model"], "--jinja", "--no-context-shift", "--no-webui", "--fit", "off",
                "--cache-ram", "0", "--log-verbosity", "0", *CFG["server_args"], *HARDWARE["server_args"]]
        self.child = Child(args, self.folder, env=environment(HARDWARE))
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            self.checkpoint()
            if self.child.poll() is not None:
                raise RuntimeError(self.child.error())
            try:
                with urllib.request.urlopen(self.url + "/health", timeout=2) as response:
                    if response.status == 200:
                        return
            except urllib.error.HTTPError as error:
                if error.code != 503:
                    raise RuntimeError(error.read().decode("utf-8")) from error
            except urllib.error.URLError:
                pass  # Readiness only: the owned server has not bound its socket yet.
            time.sleep(0.25)
        raise TimeoutError("llama-server startup timed out")

    def complete(self, messages, tools):
        {"llama": self.brain, "http": self.stop}[CFG["backend"]]()
        body = {**CFG["options"], "model": CFG["api_model"], "messages": messages, "tools": tools,
                "tool_choice": "required", "parallel_tool_calls": False}
        text = encode(body)
        self.record(brain_request(messages), (), CFG["api_model"], "req")
        headers = {"Content-Type": "application/json"}
        if CFG["api_key_env"]:
            headers["Authorization"] = "Bearer " + os.environ[CFG["api_key_env"]]
        endpoint = {"llama": self.url + "/v1/chat/completions", "http": CFG["endpoint"]}[CFG["backend"]]
        request = urllib.request.Request(endpoint, text.encode("utf-8"), headers)
        def receive():
            try:
                with urllib.request.urlopen(request, timeout=600) as response:
                    return response.read().decode("utf-8")
            except urllib.error.HTTPError as error:
                raise RuntimeError(error.read().decode("utf-8")) from error
        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(receive)
            try:
                while not future.done():
                    self.checkpoint()
                    time.sleep(0.05)
                self.checkpoint()
                text = future.result()
            except BaseException:
                self.stop()
                raise
        self.record(brain_response(text), (), CFG["api_model"], "resp")
        choice = json.loads(text)["choices"][0]
        if choice["finish_reason"] == "length":
            raise RuntimeError("Model context or output capacity exhausted; request ended without replay")
        return choice["message"]
