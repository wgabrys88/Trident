import ctypes
import msvcrt
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes as W
from next import BIN, CONFIG, MODELS, ROOT

KERNEL = ctypes.WinDLL("kernel32", use_last_error=True)


class Startup(ctypes.Structure):
    _fields_ = [("cb", W.DWORD), ("reserved", W.LPWSTR), ("desktop", W.LPWSTR), ("title", W.LPWSTR),
                *[(name, W.DWORD) for name in ("x", "y", "width", "height", "columns", "rows", "fill", "flags")],
                ("show", W.WORD), ("reserved_size", W.WORD), ("reserved_data", ctypes.c_void_p),
                ("stdin", W.HANDLE), ("stdout", W.HANDLE), ("stderr", W.HANDLE)]


class ProcessInfo(ctypes.Structure):
    _fields_ = [("process", W.HANDLE), ("thread", W.HANDLE), ("pid", W.DWORD), ("tid", W.DWORD)]


class Limits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", W.DWORD),
                ("working_min", ctypes.c_size_t), ("working_max", ctypes.c_size_t), ("active_limit", W.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", W.DWORD), ("scheduling", W.DWORD)]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", Limits), ("io", ctypes.c_uint64 * 6),
                *[(name, ctypes.c_size_t) for name in ("process_memory", "job_memory", "peak_process", "peak_job")]]


class Accounting(ctypes.Structure):
    _fields_ = [("times", ctypes.c_int64 * 4), ("faults", W.DWORD), ("total", W.DWORD),
                ("active", W.DWORD), ("terminated", W.DWORD)]


for name, arguments, result in (
    ("CreateJobObjectW", [ctypes.c_void_p, W.LPCWSTR], W.HANDLE),
    ("SetInformationJobObject", [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD], W.BOOL),
    ("QueryInformationJobObject", [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD, ctypes.c_void_p], W.BOOL),
    ("AssignProcessToJobObject", [W.HANDLE, W.HANDLE], W.BOOL),
    ("TerminateJobObject", [W.HANDLE, W.UINT], W.BOOL),
    ("CreateProcessW", [W.LPCWSTR, W.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, W.BOOL, W.DWORD,
                        ctypes.c_void_p, W.LPCWSTR, ctypes.POINTER(Startup), ctypes.POINTER(ProcessInfo)], W.BOOL),
    ("ResumeThread", [W.HANDLE], W.DWORD),
    ("TerminateProcess", [W.HANDLE, W.UINT], W.BOOL),
    ("WaitForSingleObject", [W.HANDLE, W.DWORD], W.DWORD),
    ("GetExitCodeProcess", [W.HANDLE, ctypes.POINTER(W.DWORD)], W.BOOL),
    ("CloseHandle", [W.HANDLE], W.BOOL),
):
    function = getattr(KERNEL, name)
    function.argtypes, function.restype = arguments, result


def checked(result):
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    return result


class Child:
    """Start suspended, join a kill-on-close job, then allow model code to run."""
    def __init__(self, args, folder, name, data=b""):
        self.job = checked(KERNEL.CreateJobObjectW(None, None))
        self.info = ProcessInfo()
        self.output, self.errors = folder / (name + ".stdout"), folder / (name + ".stderr")
        source = folder / (name + ".stdin")
        try:
            source.write_bytes(data)
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            checked(KERNEL.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
            with source.open("rb") as stdin, self.output.open("wb") as stdout, self.errors.open("wb") as stderr:
                handles = [msvcrt.get_osfhandle(stream.fileno()) for stream in (stdin, stdout, stderr)]
                for stream in (stdin, stdout, stderr):
                    os.set_inheritable(stream.fileno(), True)
                startup = Startup(cb=ctypes.sizeof(Startup), flags=0x100, stdin=handles[0], stdout=handles[1], stderr=handles[2])
                command = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(arg) for arg in args]))
                checked(KERNEL.CreateProcessW(str(args[0]), command, None, None, True,
                                             0x08000004, None, str(ROOT), ctypes.byref(startup), ctypes.byref(self.info)))
                checked(KERNEL.AssignProcessToJobObject(self.job, self.info.process))
                if KERNEL.ResumeThread(self.info.thread) == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            if self.info.process:
                checked(KERNEL.TerminateProcess(self.info.process, 1))
            self.close()
            raise
        finally:
            if self.info.thread:
                KERNEL.CloseHandle(self.info.thread)

    def poll(self):
        status = KERNEL.WaitForSingleObject(self.info.process, 0)
        if status == 258:  # WAIT_TIMEOUT
            return None
        if status != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = W.DWORD()
        checked(KERNEL.GetExitCodeProcess(self.info.process, ctypes.byref(code)))
        return code.value

    def wait_empty(self, deadline):
        while True:
            accounting = Accounting()
            checked(KERNEL.QueryInformationJobObject(self.job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None))
            if accounting.active == 0:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Model job {self.info.pid} still has {accounting.active} active processes.")
            time.sleep(0.05)

    def wait(self, seconds):
        self.wait_empty(time.monotonic() + seconds)
        return self.poll()

    def close(self):
        if self.job:
            checked(KERNEL.TerminateJobObject(self.job, 1))
            self.wait_empty(time.monotonic() + 30)
            KERNEL.CloseHandle(self.job)
            self.job = None
        if self.info.process:
            KERNEL.CloseHandle(self.info.process)
            self.info.process = None


class Models:
    """One owner for all local GPU model processes; speech never reloads Gemma."""
    def __init__(self, folder, report):
        self.folder, self.report = folder, report
        self.lock = threading.RLock()
        self.child = None
        self.name = ""
        self.serial = 0
        cfg = CONFIG["brain"]
        self.url = f"http://{cfg['host']}:{cfg['port']}"

    def event(self, text):
        with (self.folder / "models.log").open("a", encoding="utf-8") as record:
            record.write(time.strftime("%Y-%m-%dT%H:%M:%S%z ") + text + "\n")
        self.report(text)

    def start(self, name, args, data=b""):
        if self.child is not None:
            raise RuntimeError(f"GPU is owned by {self.name}.")
        self.serial += 1
        self.child = Child(args, self.folder, f"{self.serial:03d}-{name}", data)
        self.name = name
        self.event(f"GPU: {name} started; PID {self.child.info.pid}; command {subprocess.list2cmdline([str(arg) for arg in args])}")

    def stop(self):
        with self.lock:
            if self.child is not None:
                pid = self.child.info.pid
                self.child.close()
                self.child = None
                self.event(f"GPU: {self.name} exited; PID {pid}; job has zero active processes.")
                self.name = ""

    def speech(self, name, args, data=b""):
        with self.lock:
            self.stop()
            try:
                self.start(name, args, data)
                code = self.child.wait(600)
                output = self.child.output.read_bytes()
                if code:
                    raise RuntimeError(self.child.errors.read_text(encoding="utf-8", errors="replace") or f"{name} exited {code}")
                return output
            finally:
                self.stop()

    def brain(self):
        with self.lock:
            if self.child is not None:
                if self.name != "Gemma":
                    raise RuntimeError(f"GPU is owned by {self.name}.")
                code = self.child.poll()
                if code is not None:
                    error = self.child.errors.read_text(encoding="utf-8", errors="replace") or f"llama-server exited {code}"
                    self.stop()
                    raise RuntimeError(error)
                return
            cfg = CONFIG["brain"]
            with socket.socket() as port:
                if port.connect_ex((cfg["host"], cfg["port"])) == 0:
                    raise RuntimeError(f"Model port {cfg['port']} is already in use.")
            args = [BIN / "llama" / "llama-server.exe", "--model", MODELS / cfg["model"],
                    "--mmproj", MODELS / cfg["mmproj"], "--alias", "Gemma", "--jinja", "--no-context-shift", "--no-webui"]
            for flag, value in {"host": cfg["host"], "port": cfg["port"], "ctx-size": cfg["context"],
                                "parallel": 1, "n-gpu-layers": cfg["gpu_layers"], "threads": cfg["threads"],
                                "ubatch-size": cfg["ubatch"], "batch-size": cfg["ubatch"], "flash-attn": cfg["flash_attention"],
                                "image-min-tokens": cfg["image_tokens"], "image-max-tokens": cfg["image_tokens"]}.items():
                args += ["--" + flag, str(value)]
            try:
                self.start("Gemma", args)
                deadline = time.monotonic() + 300
                while time.monotonic() < deadline:
                    code = self.child.poll()
                    if code is not None:
                        raise RuntimeError(self.child.errors.read_text(encoding="utf-8", errors="replace") or f"llama-server exited {code}")
                    try:
                        with urllib.request.urlopen(self.url + "/health", timeout=2) as response:
                            if response.status == 200:
                                return
                    except (urllib.error.URLError, OSError):
                        pass
                    time.sleep(0.25)
                raise TimeoutError("llama-server startup timed out")
            except BaseException:
                self.stop()
                raise
