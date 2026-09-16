import ctypes
import math
import struct
import platform
import re
import uuid
import wave
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
import venv
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHATTERBOX = ROOT.parent / "chatterbox.cpp"
MODELS = ROOT / "models"
REF = ROOT / "reference.wav"
GGML_REV = "7840aaba1989c6deeefede1d77d5aaf8f52b947e"
# Required chatterbox.cpp experimental source commit; measurements are pending.
# Bump it in the same commit that adapts to an engine change.
ENGINE_REV = "5c48d99b0d680dd10e73d577db34d1768e8a4f52"
VULKAN = Path("C:/VulkanSDK/1.4.357.0")
CMAKE = "C:/Program Files/CMake/bin/cmake.exe"
DETACH = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
K32 = ctypes.WinDLL("kernel32", use_last_error=True)
K32.WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
K32.WaitNamedPipeW.restype = ctypes.c_int
K32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
K32.OpenProcess.restype = ctypes.c_void_p
K32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
K32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
K32.CloseHandle.argtypes = [ctypes.c_void_p]
PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_FILE_NOT_FOUND = 2


@dataclass(frozen=True)
class Variant:
    name: str
    hf: str
    assets: tuple[str, ...]
    t3_name: str
    t3_ckpt: str
    s3_name: str
    pid_name: str
    build_name: str
    venv_name: str
    ckpt_name: str
    pipe_tag: bytes
    other_pids: tuple[str, ...]
    t3_script: str
    s3_script: str
    knobs: tuple[str, ...]
    needs_language: bool = False


STATS_KEYS = ("predicted", "dropped", "eos", "n_past", "units", "text_tokens", "max_unit_predicted")


@dataclass
class SpeakResult:
    wall_s: float
    predicted_count: int | None = None
    dropped_count: int | None = None
    eos: int | None = None
    n_past: int | None = None
    units: int | None = None
    text_tokens: int | None = None
    max_unit_predicted: int | None = None
    knobs: dict | None = None


@dataclass
class LaunchArgs:
    text: str
    language: str | None
    knobs: dict[str, str]


CONVERT_STAMP_EXTRA = ("tensor_types", "n_tensors", "nbytes")


ACTIVE_RUN = None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def file_identity(path: Path) -> dict:
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}


def safetensors_inventory(path: Path) -> dict:
    with path.open("rb") as f:
        prefix = f.read(8)
        if len(prefix) != 8:
            raise RuntimeError(f"truncated safetensors header: {path}")
        length = struct.unpack("<Q", prefix)[0]
        if length > path.stat().st_size - 8:
            raise RuntimeError(f"invalid safetensors header length: {path}")
        header = json.loads(f.read(length))
    return {"path": str(path), "metadata": header.pop("__metadata__", {}), "tensors": header}


def source_identity(repo: Path) -> dict:
    remote = git_out(["remote", "get-url", "origin"], repo)
    parsed = urlsplit(remote)
    if parsed.scheme and parsed.hostname:
        remote = urlunsplit((parsed.scheme, parsed.hostname, parsed.path, "", ""))
    return {"head": git_out(["rev-parse", "HEAD"], repo),
            "branch": git_out(["rev-parse", "--abbrev-ref", "HEAD"], repo),
            "dirty": git_out(["status", "--porcelain"], repo), "origin": remote}


class RunEvidence:
    def __init__(self, cfg, argv):
        self.id = uuid.uuid4().hex
        self.directory = MODELS / "runs" / self.id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.out = self.directory / f"{cfg.name}.wav"
        self.start = time.perf_counter()
        self.seq = 0
        self.summary = {"schema_version": 1, "run_id": self.id, "variant": cfg.name,
                        "engine_family": cfg.build_name, "argv": argv, "status": "running",
                        "logs": {"client": "client.jsonl", "setup": "setup.log",
                                 "server": "server.log", "engine": self.out.name + ".engine.jsonl", "bake": "bake.engine.jsonl"},
                        "quality": "pending_human_review"}
        self.log = (self.directory / "client.jsonl").open("a", encoding="utf-8")
        self.emit("request_received", "request", argv=argv, cwd=str(Path.cwd()),
                  python=sys.version, host=platform.platform(), machine=platform.machine())
        write_json(self.directory / "review.json", {"run_id": self.id, "wav_sha256": None,
            "input_sha256": None, "reviewer": None, "verdict": "pending", "every_sentence": None,
            "order": None, "omissions": None, "repetitions": None, "ending": None,
            "seams": None, "numeric_readings": None, "notes": []})
        self.persist()

    def emit(self, event, stage, **data):
        event_data = {"schema_version": 1, "run_id": self.id, "component": "client",
                      "seq": self.seq, "event": event, "stage": stage,
                      "utc": datetime.now(timezone.utc).isoformat(),
                      "monotonic_elapsed_s": time.perf_counter() - self.start, **data}
        self.log.write(json.dumps(event_data, ensure_ascii=True, allow_nan=False) + "\n")
        self.log.flush()
        self.seq += 1

    def persist(self):
        write_json(self.out.with_suffix(".wav.provenance.json"), self.summary)

    @contextmanager
    def stage(self, name):
        self.summary["active_stage"] = name
        self.emit(name + "_start", name)
        self.persist()
        start = time.perf_counter()
        try:
            yield
        except BaseException as exc:
            self.emit(name + "_failed", name, error=str(exc), error_type=type(exc).__name__,
                      host_wall_s=time.perf_counter() - start)
            raise
        else:
            self.emit(name + "_end", name, host_wall_s=time.perf_counter() - start)


def run(cmd, **kw):
    if ACTIVE_RUN is None:
        subprocess.run(cmd, check=True, **kw)
        return
    ACTIVE_RUN.emit("command_start", ACTIVE_RUN.summary.get("active_stage", "setup"),
                    argv=[str(x) for x in cmd], cwd=str(kw.get("cwd", Path.cwd())))
    start = time.perf_counter()
    with (ACTIVE_RUN.directory / "setup.log").open("ab", buffering=0) as log:
        log.write((json.dumps({"argv": [str(x) for x in cmd], "cwd": str(kw.get("cwd", Path.cwd()))}) + "\n").encode())
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
        try:
            while True:
                block = proc.stdout.read1(65536)
                if not block:
                    break
                log.write(block)
                sys.stderr.buffer.write(block)
                sys.stderr.buffer.flush()
            code = proc.wait()
        finally:
            proc.stdout.close()
    ACTIVE_RUN.emit("command_end", ACTIVE_RUN.summary.get("active_stage", "setup"),
                    exit_code=code, host_wall_s=time.perf_counter() - start)
    if code:
        raise subprocess.CalledProcessError(code, cmd)


def build_target(build: Path, target: str):
    # Build the two product executables separately. This avoids compiling the
    # large inference and bake libraries concurrently on MSVC and, if a native
    # failure remains, keeps the real compiler/linker diagnostic adjacent to
    # the target that failed instead of burying it under a generic traceback.
    try:
        run([CMAKE, "--build", str(build), "--config", "Release",
             "--target", target, "--parallel", "2"])
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"native build failed for {target} (exit {exc.returncode}); "
            "the MSVC/CMake diagnostic is immediately above"
        ) from None


def git_out(args, repo=CHATTERBOX):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def json_text(obj) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n"


def read_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"invalid JSON file {path}: {exc}") from exc


def write_json(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json_text(obj), encoding="utf-8")
    tmp.replace(path)


def ensure_engine() -> str:
    if not CHATTERBOX.is_dir():
        raise SystemExit(f"missing chatterbox.cpp sibling at {CHATTERBOX}")
    if not (CHATTERBOX / ".git").is_dir():
        raise SystemExit(f"{CHATTERBOX} is not a Git checkout")
    dirty = subprocess.run(
        ["git", "-C", str(CHATTERBOX), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        raise SystemExit("chatterbox.cpp has uncommitted changes")
    sha = git_out(["rev-parse", "HEAD"])
    if sha != ENGINE_REV:
        raise SystemExit(f"chatterbox.cpp HEAD {sha} != pinned ENGINE_REV {ENGINE_REV}")
    return sha


def download(url: str, dest: Path):
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url) as resp, open(tmp, "wb") as out:
            while True:
                block = resp.read(1024 * 1024)
                if not block:
                    break
                out.write(block)
        if not tmp.is_file() or tmp.stat().st_size == 0:
            raise OSError("empty download")
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def paths(cfg: Variant):
    t3 = MODELS / cfg.t3_name
    s3 = MODELS / cfg.s3_name
    pid = MODELS / cfg.pid_name
    build = CHATTERBOX / "build" / cfg.build_name
    bin_dir = build / "bin"
    exe = bin_dir / "chatterbox-server.exe"
    bake = bin_dir / "chatterbox-bake.exe"
    tag = hashlib.sha256(str(ROOT).encode() + cfg.pipe_tag).hexdigest()[:12]
    if cfg.pipe_tag == b"turbo":
        pipe = rf"\\.\pipe\chatterbox-turbo-{tag}"
    elif cfg.pipe_tag == b"v3":
        pipe = rf"\\.\pipe\chatterbox-v3-{tag}"
    else:
        pipe = rf"\\.\pipe\chatterbox-{tag}"
    return t3, s3, pid, build, bin_dir, exe, bake, pipe


K32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint)]
K32.QueryFullProcessImageNameW.restype = ctypes.c_int
K32.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
K32.GetProcessTimes.restype = ctypes.c_int
K32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
K32.GetExitCodeProcess.restype = ctypes.c_int


def process_identity(handle):
    size = ctypes.c_uint(32768)
    image = ctypes.create_unicode_buffer(size.value)
    creation, exit_time, kernel, user = (ctypes.c_ulonglong() for _ in range(4))
    if not K32.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not K32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel), ctypes.byref(user)):
        raise ctypes.WinError(ctypes.get_last_error())
    return {"image": str(Path(image.value).resolve()), "creation_time": creation.value}


def owned_process(pid: Path, terminate=False):
    if not pid.is_file():
        return None
    raw = pid.read_text(encoding="ascii").strip()
    try:
        record = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError(f"invalid PID record: {pid}") from exc
    legacy = isinstance(record, int)
    number = record if legacy else record["pid"]
    access = PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE | (PROCESS_TERMINATE if terminate else 0)
    handle = K32.OpenProcess(access, False, number)
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists.
            pid.unlink(missing_ok=True)
            return None
        raise ctypes.WinError(error)
    try:
        state = ctypes.c_uint()
        if not K32.GetExitCodeProcess(handle, ctypes.byref(state)):
            raise ctypes.WinError(ctypes.get_last_error())
        if state.value != 259:
            K32.CloseHandle(handle)
            pid.unlink(missing_ok=True)
            return None
        actual = process_identity(handle)
        if legacy:
            raise RuntimeError(f"Legacy live PID {number} has no creation identity. Stop that server manually, then remove {pid}; refusing unsafe termination.")
        if actual != {"image": record["image"], "creation_time": record["creation_time"]}:
            raise RuntimeError(f"PID identity mismatch: {pid}; refusing to use or kill reused PID")
        return handle
    except BaseException:
        K32.CloseHandle(handle)
        raise


def kill(pid: Path):
    handle = owned_process(pid, terminate=True)
    if handle is None:
        return
    try:
        if not K32.TerminateProcess(handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        if K32.WaitForSingleObject(handle, 15000) != 0:
            raise RuntimeError(f"server exit not confirmed: {pid}")
    finally:
        K32.CloseHandle(handle)
    pid.unlink(missing_ok=True)


def running(pid: Path):
    handle = owned_process(pid)
    if handle is None:
        return False
    K32.CloseHandle(handle)
    return True


def wait_pipe_absent(pipe: str):
    for _ in range(50):
        if not K32.WaitNamedPipeW(pipe, 0) and ctypes.get_last_error() == ERROR_FILE_NOT_FOUND:
            return
        time.sleep(0.1)
    raise RuntimeError("pipe busy")


KNOB_KIND = {
    "repeat-penalty": "f+",
    "temperature": "f",
    "top-k": "i",
    "top-p": "f",
    "seed": "i",
    "n-predict": "i",
    "min-p": "f",
    "cfg-weight": "f",
}
GPT2_KNOBS = (
    "repeat-penalty", "temperature", "top-k", "top-p", "seed",
    "n-predict",
)
V3_KNOBS = (
    "repeat-penalty", "temperature", "top-p", "seed", "n-predict",
    "min-p", "cfg-weight",
)


def spawn(cfg: Variant, exe: Path, t3: Path, s3: Path, pipe: str, pid: Path, language: str | None, knobs: dict[str, str]):
    args = [str(exe), str(t3), str(s3), pipe]
    if cfg.needs_language:
        if not language:
            raise SystemExit("language is required")
        args += ["--language", language]
    for name in cfg.knobs:
        val = knobs.get(name, "")
        if val:
            args += [f"--{name}", val]
    log_path = ACTIVE_RUN.directory / "server.log"
    log = open(log_path, "ab", buffering=0)
    try:
        log.write(
            f"# spawn {time.strftime('%Y-%m-%dT%H:%M:%S%z')} family={cfg.name} {' '.join(args)}\n".encode("utf-8")
        )
        log.flush()
        proc = subprocess.Popen(
            args,
            cwd=str(exe.parent),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            creationflags=DETACH,
        )
    finally:
        log.close()
    handle = K32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, proc.pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        identity = process_identity(handle)
    finally:
        K32.CloseHandle(handle)
    write_json(pid, {"pid": proc.pid, **identity})
    ACTIVE_RUN.emit("server_start", "server", argv=args, pid=proc.pid, identity=identity, log=str(log_path))


def wav_duration_s(path: Path) -> float:
    with wave.open(str(path), "rb") as f:
        if f.getnchannels() != 1 or f.getsampwidth() != 2 or f.getframerate() != 24000 or f.getcomptype() != "NONE":
            raise RuntimeError("WAV format mismatch")
        frames = f.getnframes()
        if frames <= 0 or len(f.readframes(frames)) != frames * 2:
            raise RuntimeError("empty or truncated WAV")
        return frames / 24000.0


def parse_synth_stats(line: bytes) -> dict:
    text = line.decode("ascii", errors="replace").strip()
    if text.startswith("err "):
        raise RuntimeError(text[4:])
    if text.startswith("ok "):
        text = text[2:].lstrip()
    out = {}
    for part in text.split():
        key, sep, raw = part.partition("=")
        if not sep:
            continue
        try:
            out[key] = int(raw, 10)
        except ValueError:
            try:
                out[key] = float(raw)
            except ValueError:
                out[key] = raw
    if any(key not in out or not isinstance(out[key], int) for key in STATS_KEYS):
        raise RuntimeError("missing or invalid engine stats")
    return out


def speak_result_from_stats(wall_s: float, stats: dict) -> SpeakResult:
    knobs = {k: v for k, v in stats.items() if k not in STATS_KEYS}
    return SpeakResult(
        wall_s=wall_s,
        predicted_count=stats.get("predicted"),
        dropped_count=stats.get("dropped"),
        eos=stats.get("eos"),
        n_past=stats.get("n_past"),
        units=stats.get("units"),
        text_tokens=stats.get("text_tokens"),
        max_unit_predicted=stats.get("max_unit_predicted"),
        knobs=knobs or None,
    )


def speak_batch(pipe: str, pid: Path, out: Path, text: str) -> SpeakResult:
    for _ in range(120):
        if not running(pid):
            raise RuntimeError("daemon")
        if K32.WaitNamedPipeW(pipe, 1000):
            break
        time.sleep(1)
    else:
        raise RuntimeError("daemon timeout")
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    payload = body.encode("utf-8")
    if not payload or len(payload) > 2147483647:
        raise RuntimeError("invalid payload length")
    if any(c in str(out) for c in "\r\n\0"):
        raise RuntimeError("invalid output path")
    t0 = time.perf_counter()
    with open(pipe, "r+b", buffering=0) as f:
        ACTIVE_RUN.emit("pipe_connected", "transport", pipe=pipe)
        message = memoryview(f"{out}\n{len(payload)}\n".encode("utf-8") + payload)
        while message:
            sent = f.write(message)
            if not sent:
                raise RuntimeError("pipe write incomplete")
            message = message[sent:]
        ACTIVE_RUN.emit("request_sent", "transport", utf8_bytes=len(payload), input_sha256=hashlib.sha256(payload).hexdigest())
        ack = f.readline(32769)
        if len(ack) > 32768 or not ack.endswith(b"\n"):
            raise RuntimeError("invalid or truncated acknowledgement")
        ACTIVE_RUN.emit("reply_received", "transport", reply=ack.decode("utf-8", errors="strict"))
    wall = time.perf_counter() - t0
    if not ack.startswith(b"ok "):
        if ack.startswith(b"err "):
            raise RuntimeError(ack[4:].decode("utf-8", errors="replace").strip())
        raise RuntimeError("synthesize")
    return speak_result_from_stats(wall, parse_synth_stats(ack))


def usage(cfg: Variant):
    flags = " ".join(f"[--{n} <v>]" for n in cfg.knobs)
    if cfg.needs_language:
        return f"usage: python tts_{cfg.name}.py [-h] {flags} <text> <language>"
    return f"usage: python tts_{cfg.name}.py [-h] {flags} <text>"


def parse_variant_args(cfg: Variant, argv: list[str]) -> LaunchArgs:
    args = argv[1:]
    cli = {}
    i = 0
    allowed = set(cfg.knobs)
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help", "-?"):
            print(usage(cfg))
            raise SystemExit(0)
        if not a.startswith("--"):
            break
        if i + 1 >= len(args):
            raise SystemExit(usage(cfg))
        name = a[2:]
        if name not in allowed:
            raise SystemExit(usage(cfg))
        if name in cli:
            raise SystemExit(f"duplicate flag: --{name}")
        cli[name] = args[i + 1]
        i += 2
    rest = args[i:]
    if cfg.needs_language:
        if len(rest) != 2:
            raise SystemExit(usage(cfg))
        return LaunchArgs(rest[0], rest[1].lower(), cli)
    if len(rest) != 1:
        raise SystemExit(usage(cfg))
    return LaunchArgs(rest[0], None, cli)


def normalize_knob(name: str, raw: str) -> str:
    kind = KNOB_KIND[name]
    try:
        if kind == "i":
            if not re.fullmatch(r"[+-]?[0-9]+", raw):
                raise ValueError("integer syntax")
            value = int(raw, 10)
            if not -2147483648 <= value <= 2147483647:
                raise ValueError("integer range")
        else:
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError("non-finite value")
        if name in ("top-k", "temperature", "cfg-weight") and value < 0:
            raise ValueError("must be nonnegative")
        if name in ("n-predict", "repeat-penalty") and value <= 0:
            raise ValueError("must be positive")
        if name == "top-p" and not 0 < value <= 1:
            raise ValueError("must be in (0,1]")
        if name == "min-p" and not 0 <= value <= 1:
            raise ValueError("must be in [0,1]")
        return str(value)
    except ValueError as exc:
        raise SystemExit(f"invalid {name}: {exc}") from exc


def wanted_knobs(cfg: Variant, cli: dict[str, str]) -> dict[str, str]:
    return {name: normalize_knob(name, cli[name]) for name in cfg.knobs if name in cli}


CMAKE_FLAGS = {
    "GGML_VULKAN": "ON",
    "GGML_CUDA": "OFF",
    "GGML_CPU": "OFF",
    "GGML_OPENMP": "OFF",
    "BUILD_SHARED_LIBS": "ON",
    "TTS_CPP_BUILD_EXECUTABLES": "ON",
    "GGML_BUILD_TESTS": "OFF",
    "GGML_BUILD_EXAMPLES": "OFF",
}


def build_contract(cfg: Variant):
    return {
        "ggml_rev": GGML_REV,
        "family": cfg.build_name,
        "generator": "Visual Studio 17 2022 x64",
        "cmake_flags": {**CMAKE_FLAGS, "TTS_FAMILY": cfg.build_name, "VulkanSDK": str(VULKAN)},
    }


def cmake_identity(obj):
    if not obj:
        return None
    return {
        "family": obj.get("family"),
        "ggml_rev": obj.get("ggml_rev"),
        "generator": obj.get("generator"),
        "cmake_flags": obj.get("cmake_flags"),
    }


def ensure_ggml():
    ggml = CHATTERBOX / "ggml"
    if not (ggml / "CMakeLists.txt").is_file():
        run(["git", "clone", "--filter=blob:none", "https://github.com/ggml-org/ggml.git", str(ggml)])
        run(["git", "-C", str(ggml), "checkout", "--detach", GGML_REV])
        return
    actual = git_out(["rev-parse", "HEAD"], ggml)
    if actual != GGML_REV:
        raise SystemExit(f"ggml {actual} != required {GGML_REV}")
    dirty = subprocess.run(
        ["git", "-C", str(ggml), "status", "--porcelain"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if dirty:
        raise SystemExit("ggml checkout is dirty")


def ensure_build(cfg: Variant, pid: Path, build: Path, exe: Path, bake: Path):
    stamp = MODELS / f"{cfg.build_name}.build-contract.json"
    wanted = build_contract(cfg)
    kill(pid)
    for name in cfg.other_pids:
        kill(MODELS / name)
    ensure_ggml()
    need_configure = cmake_identity(read_json(stamp)) != cmake_identity(wanted) or not (build / "CMakeCache.txt").is_file()
    ACTIVE_RUN.emit("build_decision", "build", configure=need_configure, incremental=True, contract=wanted)
    if need_configure:
        run([
            CMAKE, "-S", str(CHATTERBOX), "-B", str(build), "-G", "Visual Studio 17 2022", "-A", "x64",
            "-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_CPU=OFF", "-DGGML_OPENMP=OFF",
            "-DBUILD_SHARED_LIBS=ON", "-DTTS_CPP_BUILD_EXECUTABLES=ON", "-DGGML_BUILD_TESTS=OFF",
            "-DGGML_BUILD_EXAMPLES=OFF", f"-DTTS_FAMILY={cfg.build_name}",
            f"-DVulkan_INCLUDE_DIR={VULKAN / 'Include'}", f"-DVulkan_LIBRARY={VULKAN / 'Lib/vulkan-1.lib'}",
            f"-DVulkan_GLSLC_EXECUTABLE={VULKAN / 'Bin/glslc.exe'}",
        ])
    build_target(build, "chatterbox-server")
    build_target(build, "chatterbox-bake")
    write_json(stamp, wanted)


def ensure_converter_venv(cfg: Variant) -> Path:
    py = ROOT / cfg.venv_name / "Scripts" / "python.exe"
    if py.is_file():
        ACTIVE_RUN.emit("converter_environment", "assets", reused=True, python=str(py))
        return py
    venv.EnvBuilder(with_pip=True).create(ROOT / cfg.venv_name)
    pip = [str(py), "-m", "pip", "install", "--disable-pip-version-check"]
    run([*pip, "torch==2.6.0", "--index-url", "https://download.pytorch.org/whl/cpu"])
    run([*pip, "numpy==1.26.4", "gguf==0.19.0", "safetensors==0.5.3", "scipy==1.15.3", "librosa==0.11.0"])
    return py


def ensure_assets(cfg: Variant) -> Path:
    ckpt = ROOT / cfg.ckpt_name
    ckpt.mkdir(parents=True, exist_ok=True)
    for name in cfg.assets:
        dest = ckpt / name
        reused = dest.is_file()
        if not reused:
            download(f"{cfg.hf}/{name}", dest)
        ACTIVE_RUN.emit("asset_resolved", "assets", name=name, configured_source=f"{cfg.hf}/{name}",
                        reused=reused, origin_verified=not reused,
                        verification="downloaded from configured immutable URL" if not reused else "local bytes hashed; cached origin not independently verified")
    return ckpt


CONVERT_DEPS = ("quant_policy.py",)
BAKE_SOURCES = (
    "src/bake.cpp", "src/bake_native.h", "src/execution_trace.cpp", "src/execution_trace.h", "src/main.cpp", "src/voice_features.cpp", "src/voice_features.h",
    "src/mel_extract_stft.cpp", "src/voice_encoder.cpp", "src/voice_encoder.h", "src/campplus.cpp",
    "src/campplus.h", "src/s3tokenizer.cpp", "src/s3tokenizer.h",
)


def blob_rev(path: str) -> str:
    # Git blob id of one engine file at HEAD. GGUF conversion and bake depend
    # on these files, not on the engine commit id, so an engine commit that
    # only touches inference must not trigger a reconvert or rebake.
    return git_out(["rev-parse", f"HEAD:{path}"])


def conversion_contract(cfg: Variant, engine_rev: str, kind: str):
    if kind not in ("t3", "s3"):
        raise SystemExit(f"unknown conversion kind: {kind}")
    script = cfg.t3_script if kind == "t3" else cfg.s3_script
    hf_base = cfg.hf
    if kind == "s3" and cfg.build_name == "gpt2":
        hf_base = "https://huggingface.co/ResembleAI/chatterbox-nano/resolve/71ccd1d0081b430592cea481f4307e764e07bc64"
    contract = {
        "kind": kind,
        "hf_base": hf_base,
        "script": script,
        "script_blob": blob_rev(f"scripts/{script}"),
        "deps_blob": [blob_rev(f"scripts/{d}") for d in CONVERT_DEPS],
    }
    if kind == "t3":
        contract["t3_ckpt"] = cfg.t3_ckpt
    return contract


def conversion_identity(obj):
    if obj is None:
        return None
    keys = ("kind", "hf_base", "script", "script_blob", "deps_blob")
    if obj.get("flags") not in (None, [], ""):
        raise SystemExit("substantive legacy conversion flags require inspection")
    if obj["kind"] == "t3":
        keys += ("t3_ckpt",)
    return {key: obj[key] for key in keys}


def gguf_type_histogram(py: Path, path: Path) -> dict:
    script = (
        "import json,sys\n"
        "from collections import Counter\n"
        "from gguf import GGUFReader\n"
        "r=GGUFReader(sys.argv[1],'r')\n"
        "c=Counter()\n"
        "inventory=[]\n"
        "metadata={}\n"
        "for key,f in r.fields.items():\n"
        "    if key.startswith('tokenizer.'): continue\n"
        "    metadata[key]={'types':[str(t) for t in f.types],'data':[f.parts[i].tolist() for i in f.data]}\n"
        "nbytes=0\n"
        "for t in r.tensors:\n"
        "    c[t.tensor_type.name]+=1\n"
        "    nbytes+=int(t.n_bytes)\n"
        "    inventory.append({'name':t.name,'type':t.tensor_type.name,'shape':[int(x) for x in t.shape]})\n"
        "print(json.dumps({'n_tensors':len(r.tensors),'nbytes':nbytes,"
        "'tensor_types':dict(sorted(c.items())),'inventory':inventory,'metadata':metadata}))\n"
    )
    out = subprocess.run(
        [str(py), "-c", script, str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(out.stdout)


def write_convert_stamp(path: Path, contract: dict, types: dict) -> dict:
    obj = {**contract, **types}
    write_json(path, obj)
    return obj


def convert_t3(cfg: Variant, py: Path, ckpt: Path, t3: Path, contract):
    tmp = t3.with_suffix(".gguf.converting")
    run([str(py), str(CHATTERBOX / "scripts" / cfg.t3_script), str(ckpt), str(tmp), cfg.t3_ckpt])
    gguf_type_histogram(py, tmp)
    tmp.replace(t3)
    write_json(MODELS / f"{t3.stem}.convert.json", contract)


def convert_s3(cfg: Variant, py: Path, ckpt: Path, s3: Path, contract):
    if s3.exists():
        raise SystemExit(f"S3 reconversion is forbidden: {s3}")
    tmp = s3.with_suffix(".gguf.converting")
    run([str(py), str(CHATTERBOX / "scripts" / cfg.s3_script), str(ckpt), str(tmp)])
    gguf_type_histogram(py, tmp)
    tmp.replace(s3)
    write_json(MODELS / f"{s3.stem}.convert.json", contract)


def ensure_converted(cfg: Variant, engine_rev: str, py: Path, ckpt: Path, t3: Path, s3: Path):
    t3_contract = conversion_contract(cfg, engine_rev, "t3")
    s3_contract = conversion_contract(cfg, engine_rev, "s3")
    t3_stamp = MODELS / f"{t3.stem}.convert.json"
    s3_stamp = MODELS / f"{s3.stem}.convert.json"
    t3_changed = (not t3.is_file()) or conversion_identity(read_json(t3_stamp)) != t3_contract
    s3_changed = (not s3.is_file()) or conversion_identity(read_json(s3_stamp)) != s3_contract
    changed = t3_changed or s3_changed
    if s3_changed and s3.exists():
        raise SystemExit(f"S3 conversion contract mismatch: {s3_stamp}")
    ACTIVE_RUN.emit("conversion_decision", "conversion", t3_reused=not t3_changed, s3_reused=not s3_changed,
                    t3_contract=t3_contract, s3_contract=s3_contract)
    if t3_changed:
        convert_t3(cfg, py, ckpt, t3, t3_contract)
    if s3_changed:
        convert_s3(cfg, py, ckpt, s3, s3_contract)
    t3_types = gguf_type_histogram(py, t3)
    s3_types = gguf_type_histogram(py, s3)
    write_convert_stamp(t3_stamp, t3_contract, t3_types)
    write_convert_stamp(s3_stamp, s3_contract, s3_types)
    return t3_contract, s3_contract, changed, t3_types, s3_types


def ensure_baked(cfg: Variant, engine_rev: str, py: Path, ckpt: Path, t3: Path, s3: Path, bake: Path, bin_dir: Path, pid: Path, t3_contract, s3_contract, converted: bool):
    wanted = {"family": cfg.name, "bake_blob": [blob_rev(p) for p in BAKE_SOURCES],
              "bake_binary": file_identity(bake), "reference": file_identity(REF),
              "t3_conversion": t3_contract, "s3_conversion": s3_contract}
    stamp = MODELS / f"{cfg.name}.bake-contract.json"
    pending = MODELS / f"{s3.stem}.bake-pending.json"
    if pending.exists():
        raise RuntimeError(f"Incomplete bake transaction: inspect {pending}; do not run inference or reconvert S3")
    prior = read_json(stamp)
    actual = {"t3": file_identity(t3), "s3": file_identity(s3)}
    if not converted and prior and prior.get("identity") == wanted and prior.get("outputs") == actual:
        ACTIVE_RUN.emit("bake_decision", "bake", reused=True, identity=prior)
        return prior
    ACTIVE_RUN.emit("bake_decision", "bake", reused=False, identity=wanted)
    kill(pid)
    write_json(pending, {"status": "started", "identity": wanted, "before": actual,
                         "recovery": "Inspect both GGUFs and rerun the same reference bake after explicitly resolving this marker. Never convert S3 again."})
    run([str(bake), str(t3), str(s3), str(REF)], cwd=str(ACTIVE_RUN.directory))
    result = {"identity": wanted, "outputs": {"t3": file_identity(t3), "s3": file_identity(s3)}}
    write_json(stamp, result)
    pending.unlink()
    return result


def migrate_nano_s3(cfg, engine_rev):
    if cfg.build_name != "gpt2":
        return
    old = MODELS / "chatterbox-s3gen-nano-q4_0.gguf"
    new = MODELS / cfg.s3_name
    old_stamp = MODELS / "nano.s3-convert.json"
    new_stamp = MODELS / f"{new.stem}.convert.json"
    if not old.exists():
        return
    contract = read_json(old_stamp)
    if contract is None:
        raise RuntimeError("old Nano S3 has no contract; refusing guessed migration")
    contract.pop("family", None)
    if conversion_identity(contract) != conversion_contract(cfg, engine_rev, "s3"):
        raise RuntimeError("old Nano S3 contract mismatch; preserve original and inspect")
    if new.exists():
        if sha256(old) != sha256(new) or conversion_identity(read_json(new_stamp)) != conversion_identity(contract):
            raise RuntimeError("conflicting old/new Nano S3 assets")
        ACTIVE_RUN.emit("s3_migration", "conversion", status="identical_duplicate_preserved")
        return
    write_json(new_stamp, contract)
    old.rename(new)
    old_stamp.unlink()
    ACTIVE_RUN.emit("s3_migration", "conversion", status="renamed_without_reconversion", output=file_identity(new))


def host_inventory():
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
           "Get-CimInstance Win32_VideoController | Select-Object Name,PNPDeviceID,DriverVersion,DriverDate,AdapterRAM | ConvertTo-Json -Compress"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return {"platform": platform.platform(), "machine": platform.machine(),
            "cpu": platform.processor(), "python": sys.version,
            "video_controllers": json.loads(result.stdout),
            "selected_device_source": "engine backend_identity event; inventory alone does not identify the selected Vulkan device"}


def run_variant(cfg: Variant, args: LaunchArgs):
    evidence = ACTIVE_RUN
    values = wanted_knobs(cfg, args.knobs)
    text = args.text.replace("\r\n", "\n").replace("\r", "\n")
    evidence.summary.update(original_text=args.text, transport_text=text,
                            input_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                            utf8_bytes=len(text.encode("utf-8")), characters=len(text),
                            language=args.language, knobs_cli=values)
    evidence.persist()
    with evidence.stage("prerequisites"):
        if not REF.is_file():
            raise FileNotFoundError(str(REF))
        engine_rev = ensure_engine()
        evidence.summary["sources"] = {"trident": source_identity(ROOT), "engine": source_identity(CHATTERBOX),
                                       "engine_pin": ENGINE_REV, "ggml_pin": GGML_REV,
                                       "engine_tree": git_out(["ls-tree", "-r", "HEAD"], CHATTERBOX),
                                       "trident_tree": git_out(["ls-tree", "-r", "HEAD"], ROOT)}
        evidence.emit("source_identity", "prerequisites", sources=evidence.summary["sources"])
        evidence.summary["host_inventory"] = host_inventory()
        if not Path(CMAKE).is_file() or not (VULKAN / "Bin/glslc.exe").is_file():
            raise RuntimeError("Configured CMAKE/VULKAN paths are missing; set the installed SDK paths in tts_common.py and commit them before running")
        evidence.summary["tool_versions"] = {
            "cmake": subprocess.run([CMAKE, "--version"], capture_output=True, text=True, check=True).stdout,
            "git": subprocess.run(["git", "--version"], capture_output=True, text=True, check=True).stdout}
    t3, s3, pid, build, bin_dir, exe, bake, pipe = paths(cfg)
    with evidence.stage("build"):
        ensure_build(cfg, pid, build, exe, bake)
        evidence.summary["sources"]["ggml"] = source_identity(CHATTERBOX / "ggml")
        evidence.summary["build_contract"] = build_contract(cfg)
        evidence.summary["binaries"] = [file_identity(x) for x in sorted(bin_dir.iterdir()) if x.suffix.lower() in (".exe", ".dll")]
        evidence.summary["cmake_cache"] = file_identity(build / "CMakeCache.txt")
        # Preserve compiler/toolchain cache details along with the complete build transcript.
        (evidence.directory / "CMakeCache.txt").write_bytes((build / "CMakeCache.txt").read_bytes())
    with evidence.stage("assets"):
        py = ensure_converter_venv(cfg)
        ckpt = ensure_assets(cfg)
        evidence.summary["assets"] = {"hf_base": cfg.hf, "files": [file_identity(ckpt / name) for name in cfg.assets],
                                      "reference": file_identity(REF),
                                      "safetensors": [safetensors_inventory(ckpt / name) for name in cfg.assets if name.endswith(".safetensors")]}
        versions = subprocess.run([str(py), "-m", "pip", "freeze"], check=True, capture_output=True, text=True).stdout
        evidence.summary["converter_packages"] = versions
        evidence.emit("assets", "assets", identity=evidence.summary["assets"], converter_packages=versions)
    with evidence.stage("conversion"):
        migrate_nano_s3(cfg, engine_rev)
        t3_contract, s3_contract, converted, _, _ = ensure_converted(cfg, engine_rev, py, ckpt, t3, s3)
        evidence.summary["conversion"] = {"t3": t3_contract, "s3": s3_contract}
    with evidence.stage("bake"):
        evidence.summary["bake"] = ensure_baked(cfg, engine_rev, py, ckpt, t3, s3, bake, bin_dir, pid, t3_contract, s3_contract, converted)
        evidence.summary["models"] = {"t3": {**file_identity(t3), **gguf_type_histogram(py, t3)},
                                       "s3": {**file_identity(s3), **gguf_type_histogram(py, s3)}}
        if cfg.name == "nano":
            (MODELS / "chatterbox-t3-nano-f16-mixed.gguf").unlink(missing_ok=True)
            (MODELS / "nano.t3-convert.json").unlink(missing_ok=True)
    with evidence.stage("server"):
        wait_pipe_absent(pipe)
        spawn(cfg, exe, t3, s3, pipe, pid, args.language, values)
    with evidence.stage("request"):
        result = speak_batch(pipe, pid, evidence.out, text)
        duration = wav_duration_s(evidence.out)
        engine_log = Path(str(evidence.out) + ".engine.jsonl")
        events = [json.loads(line) for line in engine_log.read_text(encoding="utf-8").splitlines()]
        if not any(e["event"] == "request_complete" for e in events):
            raise RuntimeError("missing engine completion evidence")
        output_event = next(e for e in reversed(events) if e["event"] == "output_write_end")
        output = file_identity(evidence.out)
        if output["sha256"] != output_event["wav_sha256"]:
            raise RuntimeError("WAV hash differs from engine evidence")
        synth = next(e for e in reversed(events) if e["event"] == "synthesis_complete")
        evidence.summary["metrics"] = {**vars(result), "duration_s": duration,
            "request_wall_rtf": result.wall_s / duration,
            "synthesis_host_wall_s": synth["host_wall_s"], "synthesis_host_wall_rtf": synth["host_wall_s"] / duration,
            "rtf_definition": "host wall seconds / final WAV duration; request includes cold load; synthesis excludes model load"}
        evidence.summary["output"] = output
        evidence.summary["engine_events"] = events
        evidence.emit("output_verified", "output", identity=output, metrics=evidence.summary["metrics"])
        write_json(evidence.directory / "review.json", {"run_id": evidence.id, "wav_sha256": output["sha256"],
            "input_sha256": evidence.summary["input_sha256"], "reviewer": None, "verdict": "pending",
            "every_sentence": None, "order": None, "omissions": None, "repetitions": None,
            "ending": None, "seams": None, "numeric_readings": None, "notes": []})
        print(f"text_tokens={result.text_tokens} predicted={result.predicted_count} eos={result.eos} "
              f"units={result.units} max_unit_predicted={result.max_unit_predicted} duration_s={duration:.3f} "
              f"wall_s={result.wall_s:.3f} request_wall_rtf={result.wall_s / duration:.3f} "
              f"synthesis_host_wall_rtf={synth['host_wall_s'] / duration:.3f}", file=sys.stderr, flush=True)
        print(evidence.out, flush=True)
        print(f"Logs and pending human review: {evidence.directory}", file=sys.stderr, flush=True)


def launch_variant(cfg: Variant, argv: list[str]):
    global ACTIVE_RUN
    ACTIVE_RUN = RunEvidence(cfg, argv)
    evidence = ACTIVE_RUN
    print(f"Run evidence: {evidence.directory}", file=sys.stderr, flush=True)
    try:
        args = parse_variant_args(cfg, argv)
        run_variant(cfg, args)
        evidence.summary.update(status="execution_complete", exit_code=0, launcher_wall_s=time.perf_counter() - evidence.start,
                                active_stage="complete")
        evidence.emit("request_complete", "request", status="pending_human_review")
    except BaseException as exc:
        code = exc.code if isinstance(exc, SystemExit) and isinstance(exc.code, int) else 1
        evidence.summary.update(status="help" if code == 0 else "failed", error=str(exc),
                                error_type=type(exc).__name__, exit_code=code,
                                launcher_wall_s=time.perf_counter() - evidence.start)
        engine_log = Path(str(evidence.out) + ".engine.jsonl")
        if engine_log.exists():
            evidence.summary["engine_trace"] = str(engine_log)
        if evidence.out.exists():
            evidence.summary["unacknowledged_output"] = file_identity(evidence.out)
        evidence.emit("request_failed", evidence.summary.get("active_stage", "parse"),
                      error=str(exc), error_type=type(exc).__name__, exit_code=code)
        raise
    finally:
        try:
            evidence.persist()
        finally:
            evidence.log.close()
            ACTIVE_RUN = None
