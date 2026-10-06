import _winapi, msvcrt, os, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
import win32job, win32process
from concurrent.futures import ThreadPoolExecutor
from core import BIN, CONFIG, MODELS, ROOT

class Child:
    """Own a suspended process and its descendants before allowing execution."""
    def __init__(self, args, folder, data=b"", cwd=ROOT):
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
                subprocess.CREATE_NO_WINDOW | 4, None, str(cwd), startup)
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

class Mouth:
    """Resident Chatterbox Nano. Models.stop does not close it."""
    def __init__(self, folder):
        self.folder, self.proc, self.job, self.err, self.live = folder, None, None, None, False

    def start(self):
        self.job = win32job.CreateJobObject(None, "")
        limits = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, limits)
        self.err = (self.folder / "mouth.err").open("wb", buffering=0)
        self.proc = subprocess.Popen([sys.executable, "-u", str(ROOT / "audio.py"), str(self.folder / "voice.pt")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.err, bufsize=0, cwd=ROOT,
            creationflags=subprocess.CREATE_NO_WINDOW)
        win32job.AssignProcessToJobObject(self.job, self.proc._handle)

    def failure(self):
        code = self.proc.poll() if self.proc else None
        text = (self.folder / "mouth.err").read_text(encoding="utf-8", errors="replace").strip()
        return text[-4000:] or f"Mouth exited {code}"

    def _exact(self, count):
        data = b""
        while len(data) < count:
            piece = self.proc.stdout.read(count - len(data))
            if not piece:
                raise RuntimeError(self.failure())
            data += piece
        return data

    def ready(self):
        if self.live:
            return
        mark = self._exact(4)
        if mark != b"\0\0\0\0":
            raise RuntimeError(f"Mouth handshake {mark!r}: {self.failure()}")
        self.live = True

    def pcm(self, text, checkpoint=lambda: None):
        self.ready()
        raw = text.encode("utf-8")
        self.proc.stdin.write(len(raw).to_bytes(4, "little") + raw)
        self.proc.stdin.flush()
        def receive():
            size = int.from_bytes(self._exact(4), "little")
            if not size:
                raise RuntimeError(self.failure() if self.proc.poll() is not None else "Mouth returned no audio")
            return self._exact(size)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(receive)
            caught = None
            while not future.done():
                try:
                    checkpoint()
                except BaseException as error:
                    caught = error
                    break
                time.sleep(0.05)
            audio = future.result()
            if caught:
                raise caught
            return audio

    def close(self):
        if self.proc and self.proc.poll() is None and self.proc.stdin:
            try:
                self.proc.stdin.write((0).to_bytes(4, "little"))
                self.proc.stdin.flush()
            except OSError:
                pass
        if self.job:
            win32job.TerminateJobObject(self.job, 1)
            self.job.Close()
            self.job = None
        if self.proc:
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
        if self.err and not self.err.closed:
            self.err.close()
        self.err, self.live = None, False

class Models:
    def __init__(self, folder, checkpoint):
        self.folder, self.checkpoint, self.child = folder, checkpoint, None
        cfg = CONFIG["brain"]
        self.url = f"http://{cfg['host']}:{cfg['port']}"

    def stop(self):
        if self.child:
            self.child.close()
            self.child = None

    def command(self, args, data=b"", interrupt=True, cwd=ROOT):
        child = Child(args, self.folder, data, cwd)
        try:
            return child.result(self.checkpoint if interrupt else lambda: None)
        finally:
            child.close()

    def brain(self):
        if self.child:
            if self.child.poll() is not None:
                raise RuntimeError(self.child.error())
            return
        cfg = CONFIG["brain"]
        if (BIN / "llama" / "release.txt").read_text().strip() != CONFIG["install"]["llama_tag"]:
            raise RuntimeError("Run install.py to install the configured llama.cpp release and chat template")
        with socket.socket() as port:
            if port.connect_ex((cfg["host"], cfg["port"])) == 0:
                raise RuntimeError(f"Port {cfg['port']} is already in use")
        args = [BIN / "llama" / "llama-server.exe", "--model", MODELS / cfg["model"], "--mmproj", MODELS / cfg["mmproj"],
                "--chat-template-file", BIN / "llama" / "gemma.jinja", "--alias", "Gemma", "--jinja",
                "--no-context-shift", "--no-webui", "--fit", "off", "--cache-ram", "0", "--log-verbosity", "0"]
        for flag, value in {"host": cfg["host"], "port": cfg["port"], "ctx-size": cfg["context"], "parallel": 1,
                "n-gpu-layers": cfg["gpu_layers"], "threads": cfg["threads"], "batch-size": cfg["batch"],
                "ubatch-size": cfg["ubatch"], "flash-attn": cfg["flash_attention"], "cache-type-k": cfg["cache_k"],
                "cache-type-v": cfg["cache_v"], "image-max-tokens": cfg["image_tokens"]}.items():
            args += ["--" + flag, str(value)]
        if not cfg["projector_gpu"]:
            args.append("--no-mmproj-offload")
        self.child = Child(args, self.folder)
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
                    raise RuntimeError(error.read().decode("utf-8", "replace")) from error
            except urllib.error.URLError:
                pass  # The owned server has not bound its socket yet.
            time.sleep(0.25)
        raise TimeoutError("llama-server startup timed out")

    def completion(self, raw):
        self.brain()
        request = urllib.request.Request(self.url + "/v1/chat/completions", raw, {"Content-Type": "application/json"})
        def receive():
            try:
                with urllib.request.urlopen(request, timeout=600) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                raise RuntimeError(error.read().decode("utf-8", "replace")) from error
        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(receive)
            try:
                while not future.done():
                    self.checkpoint()
                    time.sleep(0.05)
                self.checkpoint()
                return future.result()
            except BaseException:
                self.stop()
                raise
