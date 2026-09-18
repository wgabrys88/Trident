import ctypes
import math
import struct
import platform
import re
import shutil
import wave
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit
import hashlib
import json
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tts_metadata import find_binary_record, normalize_binary_records
from tts_settings import (
    ANALYSIS_MODES, CMAKE_ARCH, CMAKE_FLAGS, CMAKE_GENERATOR, CONTROL_DEFAULTS,
    CONVERSION_DEFAULTS, PYTHON_ENV_BOOTSTRAP, PYTORCH_CPU_INDEX, RUNTIME_DEFAULTS,
    RUNTIME_KINDS, WEIGHT_TYPES,
)

ROOT = Path(__file__).resolve().parent
CHATTERBOX = ROOT.parent / "chatterbox.cpp"
MODELS = ROOT / "models"
REF = ROOT / "reference.wav"
GGML_REV = "7840aaba1989c6deeefede1d77d5aaf8f52b947e"
VULKAN: Path | None = None
CMAKE: Path | None = None
VCVARS: Path | None = None
ENGINE_PIN = "dc53edc994ca919ff359ea31968dcdcde22bfe3c"
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
    t3_ckpt: str
    pid_name: str
    build_name: str
    venv_name: str
    ckpt_name: str
    pipe_tag: bytes
    other_pids: tuple[str, ...]
    t3_script: str
    s3_script: str
    needs_language: bool = False
    external_assets: tuple[tuple[str, str], ...] = ()

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
    t3_weight_type: str
    s3_weight_type: str
    reference: Path
    analysis: str
    determinism_repeats: int

CONVERT_STAMP_EXTRA = ("tensor_types", "n_tensors", "nbytes")

ACTIVE_RUN = None
_SHA256_CACHE = {}

def sha256(path: Path) -> str:
    path = path.resolve()
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    cached = _SHA256_CACHE.get(key)
    if cached is not None:
        return cached
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    digest = h.hexdigest()
    _SHA256_CACHE[key] = digest
    return digest

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
        now = datetime.now().astimezone()
        self.started_at = now.isoformat()
        self.output_root = Path.cwd().resolve()
        self.id = now.strftime("%S-%M-%H-%d-%m-%y") + f"_{cfg.name}"
        self.directory = self.output_root / f"{self.id}_log"
        self.out = self.output_root / f"{self.id}.wav"
        self.work_out = self.directory / f"{self.id}.wav"
        if self.directory.exists() or self.out.exists():
            raise SystemExit(f"output collision: {self.id}")
        self.directory.mkdir(parents=True)
        self.start = time.perf_counter()
        self.seq = 0
        for name in ("events.jsonl", "text_tokens.jsonl", "t3_tokens.jsonl", "s3_tokens.jsonl"):
            (self.directory / name).write_bytes(b"")
        self.summary = {"schema_version": 4, "run_id": self.id, "variant": cfg.name,
                        "engine_family": cfg.build_name, "argv": argv, "status": "running",
                        "started_at": self.started_at, "output_root": str(self.output_root),
                        "output_path": str(self.out), "log_dir": str(self.directory), "sr": 24000,
                        "logs": {"events": "events.jsonl", "text_tokens": "text_tokens.jsonl",
                                 "t3_tokens": "t3_tokens.jsonl", "s3_tokens": "s3_tokens.jsonl",
                                 "features": "features.parquet", "meta": "meta.json",
                                 "analysis": "analysis.jsonl", "asr": "asr_parakeet.json",
                                 "report": "report.html"}}
        self.log = (self.directory / "events.jsonl").open("a", encoding="utf-8")
        self.emit("request_received", "request", argv=argv, cwd=str(self.output_root),
                  python=sys.version, host=platform.platform(), machine=platform.machine())
        self.persist()

    def emit(self, event, stage, **data):
        event_data = {"schema_version": 4, "component": "client", "run_id": self.id,
                      "seq": self.seq, "event": event, "stage": stage, "unit_index": None,
                      "monotonic_elapsed_s": time.perf_counter() - self.start}
        for key, value in data.items():
            if key not in event_data:
                event_data[key] = value
        self.log.write(json.dumps(event_data, ensure_ascii=True, allow_nan=False) + "\n")
        self.log.flush()
        self.seq += 1

    def persist(self):
        existing = read_json(self.directory / "meta.json") or {}
        ours = self.meta()
        merged = {**existing, **ours}
        engine_knobs = existing.get("knobs") if isinstance(existing.get("knobs"), dict) else {}
        ours_knobs = ours.get("knobs") if isinstance(ours.get("knobs"), dict) else {}
        knobs = {**engine_knobs, **ours_knobs}
        if knobs:
            merged["knobs"] = knobs
            if "seed" in knobs:
                merged["seed"] = knobs["seed"]
        if ours.get("wav_sha256"):
            merged["wav_sha256"] = ours["wav_sha256"]
        elif existing.get("wav_sha256"):
            merged["wav_sha256"] = existing["wav_sha256"]
        if ours.get("duration_s") is not None:
            merged["duration_s"] = ours["duration_s"]
        elif existing.get("duration_s") is not None:
            merged["duration_s"] = existing["duration_s"]
        if ours.get("git_heads") and any((ours["git_heads"] or {}).values()):
            merged["git_heads"] = ours["git_heads"]
        if ours.get("ENGINE_REV"):
            merged["ENGINE_REV"] = ours["ENGINE_REV"]
        if ours.get("binary_sha256"):
            merged["binary_sha256"] = ours["binary_sha256"]
        if ours.get("gguf_sha256") and any((ours.get("gguf_sha256") or {}).values()):
            merged["gguf_sha256"] = ours["gguf_sha256"]
        merged["schema_version"] = 4
        merged["run_id"] = self.id
        write_json(self.directory / "meta.json", merged)

    def meta(self):
        knobs = self.summary.get("knobs_cli") or (self.summary.get("metrics") or {}).get("knobs") or {}
        sources = self.summary.get("sources") or {}
        models = self.summary.get("models") or {}
        binaries = normalize_binary_records(self.summary.get("binaries"))
        server = find_binary_record(binaries, "chatterbox-server.exe")
        out = self.summary.get("output") or {}
        metrics = self.summary.get("metrics") or {}
        ggml_src = sources.get("ggml") or {}
        return {
            "schema_version": 4,
            "run_id": self.id,
            "seed": knobs.get("seed"),
            "knobs": knobs,
            "language_id": self.summary.get("language"),
            "started_at": self.started_at,
            "output_path": str(self.out),
            "log_dir": str(self.directory),
            "original_text": self.summary.get("original_text"),
            "transport_text": self.summary.get("transport_text"),
            "normalization": self.summary.get("normalization"),
            "reference": (self.summary.get("assets") or {}).get("reference"),
            "git_heads": {
                "trident": (sources.get("trident") or {}).get("head"),
                "engine": (sources.get("engine") or {}).get("head"),
                "ggml": ggml_src.get("head"),
            },
            "ENGINE_REV": (sources.get("engine") or {}).get("head"),
            "binary_sha256": (server or {}).get("sha256"),
            "gguf_sha256": {
                "t3": (models.get("t3") or {}).get("sha256"),
                "s3": (models.get("s3") or {}).get("sha256"),
            },
            "wav_sha256": out.get("sha256"),
            "engine_wav_sha256": (self.summary.get("engine_output") or {}).get("sha256"),
            "duration_s": metrics.get("duration_s"),
            "conversion_types": self.summary.get("conversion_types"),
            "analysis_mode": self.summary.get("analysis_mode"),
            "determinism": self.summary.get("determinism"),
            "sr": 24000,
            "status": self.summary.get("status"),
            "variant": self.summary.get("variant"),
        }

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

def winget_install(package: str, override: str | None = None):
    cmd = ["winget", "install", "--id", package, "-e", "--silent", "--accept-package-agreements", "--accept-source-agreements"]
    if override:
        cmd += ["--override", override]
    run(cmd)

def vs_installation() -> Path | None:
    vswhere = Path("C:/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe")
    if not vswhere.is_file():
        return None
    proc = subprocess.run([str(vswhere), "-latest", "-products", "*", "-requires",
                           "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                          check=True, capture_output=True, text=True)
    value = proc.stdout.strip()
    return Path(value) if value else None

def resolve_windows_tools() -> Path:
    global CMAKE, VULKAN, VCVARS
    found_cmake = shutil.which("cmake")
    CMAKE = Path(found_cmake) if found_cmake else Path("C:/Program Files/CMake/bin/cmake.exe")
    if not CMAKE.is_file():
        winget_install("Kitware.CMake")
        CMAKE = Path("C:/Program Files/CMake/bin/cmake.exe")
    candidates = sorted(
        Path("C:/VulkanSDK").glob("*/Bin/glslc.exe"),
        key=lambda x: tuple(int(v) for v in re.findall(r"\d+", x.parts[-3])), reverse=True,
    )
    if not candidates:
        winget_install("KhronosGroup.VulkanSDK")
        candidates = sorted(
            Path("C:/VulkanSDK").glob("*/Bin/glslc.exe"),
            key=lambda x: tuple(int(v) for v in re.findall(r"\d+", x.parts[-3])), reverse=True,
        )
    if not candidates:
        raise SystemExit("Vulkan SDK installation not found")
    VULKAN = candidates[0].parents[1]
    install = vs_installation()
    if install is None:
        winget_install(
            "Microsoft.VisualStudio.2022.BuildTools",
            "--wait --quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended",
        )
        install = vs_installation()
    if install is None:
        raise SystemExit("Visual Studio Build Tools installation not found")
    VCVARS = install / "VC/Auxiliary/Build/vcvars64.bat"
    if not VCVARS.is_file():
        raise SystemExit(f"vcvars64.bat not found: {VCVARS}")
    return VCVARS


def python311() -> Path:
    if sys.version_info[:2] == (3, 11):
        return Path(sys.executable).resolve()
    launcher = shutil.which("py")
    if launcher:
        probe = subprocess.run([launcher, "-3.11", "-c", "import sys;print(sys.executable)"], capture_output=True, text=True)
        if probe.returncode == 0:
            path = Path(probe.stdout.strip())
            if path.is_file():
                return path
    direct = shutil.which("python3.11") or shutil.which("python3.11-64.exe")
    if direct and Path(direct).is_file():
        return Path(direct).resolve()
    candidates = (
        Path.home() / "AppData/Local/Programs/Python/Python311/python.exe",
        Path.home() / "AppData/Local/Python/pythoncore-3.11-64/python.exe",
        Path("C:/Program Files/Python311/python.exe"),
    )
    for path in candidates:
        if path.is_file():
            return path
    winget_install("Python.Python.3.11")
    for path in candidates:
        if path.is_file():
            return path
    launcher = shutil.which("py")
    if launcher:
        probe = subprocess.run([launcher, "-3.11", "-c", "import sys;print(sys.executable)"], check=True, capture_output=True, text=True)
        return Path(probe.stdout.strip())
    raise SystemExit("Python 3.11 was installed but no interpreter path was found; reopen PowerShell and rerun")


def _requirements_fingerprint(requirements: Path, bootstrap: dict) -> str:
    h = hashlib.sha256()
    h.update(requirements.read_bytes())
    h.update(b"\0")
    h.update(json.dumps({
        "torch": list(bootstrap["torch"]),
        "pre": list(bootstrap["pre"]),
        "msvc": bool(bootstrap["msvc"]),
        "no_binary": list(bootstrap.get("no_binary", ())),
        "torch_index": PYTORCH_CPU_INDEX,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return h.hexdigest()


def requirements_venv(directory: Path, requirements: Path, environment: str) -> Path:
    bootstrap = PYTHON_ENV_BOOTSTRAP[environment]
    fingerprint = _requirements_fingerprint(requirements, bootstrap)
    py = directory / "Scripts/python.exe"
    stamp = directory / ".requirements.sha256"
    current = stamp.read_text(encoding="ascii").strip() if stamp.is_file() else None
    if not py.is_file() or current != fingerprint:
        if directory.exists():
            shutil.rmtree(directory)
        run([str(python311()), "-m", "venv", str(directory)])
        pip = [str(py), "-m", "pip", "install", "--disable-pip-version-check"]
        if bootstrap["torch"]:
            run([*pip, *bootstrap["torch"], "--index-url", PYTORCH_CPU_INDEX])
        if bootstrap["pre"]:
            run([*pip, *bootstrap["pre"]])
        if bootstrap["msvc"]:
            vcvars = resolve_windows_tools()
            pip_req = [*pip, "--no-build-isolation"]
            no_binary = tuple(bootstrap.get("no_binary", ()))
            if no_binary:
                pip_req.extend(["--no-binary", ",".join(no_binary)])
            pip_req.extend(["-r", str(requirements)])
            command = subprocess.list2cmdline(pip_req)
            wrapper = directory / "_msvc_pip.cmd"
            wrapper.write_bytes(f'@echo off\r\ncall "{vcvars}" >nul && {command}\r\n'.encode("ascii"))
            try:
                run(["cmd.exe", "/d", "/c", str(wrapper)])
            finally:
                wrapper.unlink(missing_ok=True)
        else:
            run([*pip, "-r", str(requirements)])
        stamp.write_text(fingerprint + "\n", encoding="ascii")
    return py


_ANALYSIS_PY = None


def analysis_python() -> Path:
    global _ANALYSIS_PY
    if _ANALYSIS_PY is None:
        _ANALYSIS_PY = requirements_venv(ROOT / "tools/.venv-log", ROOT / "tools/log_analysis.requirements.txt", "analysis")
    return _ANALYSIS_PY


def _run_wav(run_dir: Path) -> Path:
    meta = read_json(run_dir / "meta.json") or {}
    configured = meta.get("output_path")
    if configured:
        candidate = Path(configured)
        if candidate.is_file():
            return candidate
    wavs = sorted(run_dir.glob("*.wav"))
    if len(wavs) != 1:
        raise RuntimeError(f"cannot resolve run WAV in {run_dir}")
    return wavs[0]


def _token_ids(run_dir: Path, table: str) -> list[int]:
    path = run_dir / f"{table}_tokens.jsonl"
    if not path.is_file():
        return []
    return [int(json.loads(line)["id"]) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _exact_repeat(primary: Path, repeat: Path) -> dict | None:
    wav_a, wav_b = _run_wav(primary), _run_wav(repeat)
    if sha256(wav_a) != sha256(wav_b) or wav_a.read_bytes() != wav_b.read_bytes():
        return None
    tables = {}
    for table in ("text", "t3", "s3"):
        a, b = _token_ids(primary, table), _token_ids(repeat, table)
        tables[table] = {"count_a": len(a), "count_b": len(b), "exact": a == b}
        if a != b:
            return None
    return {
        "exact_match": True,
        "pcm_exact": True,
        "wav_sha256_equal": True,
        "token_tables": tables,
        "acoustic_metrics_skipped_exact": True,
        "mel_l2": 0.0,
        "f0_l2": 0.0,
        "compare_exit_code": 0,
    }


def compare_repeat(primary: Path, repeat: Path, dest: Path) -> dict:
    exact = _exact_repeat(primary, repeat)
    if exact is not None:
        return exact
    dest.mkdir(parents=True, exist_ok=True)
    py = analysis_python()
    proc = subprocess.run(
        [str(py), str(ROOT / "tools/compare_runs.py"), str(primary), str(repeat)],
        cwd=str(dest), capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"determinism comparison failed: {proc.stderr.strip()}")
    payload = proc.stdout
    start, end = payload.find("{"), payload.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError(f"determinism comparison produced no JSON: {(proc.stderr or payload).strip()}")
    result = json.loads(payload[start:end + 1])
    result["exact_match"] = bool(result.get("exact_match"))
    result["compare_exit_code"] = proc.returncode
    return result


def run_determinism(evidence: RunEvidence, level: int, cfg: Variant, text: str, pipe: str, pid: Path,
                    exe: Path, t3: Path, s3: Path, language: str | None, knobs: dict[str, str],
                    tokenizer_py: Path | None, ckpt: Path | None) -> dict:
    if level <= 0:
        result = {"level": 0, "checks": {}}
        write_json(evidence.directory / "determinism.json", result)
        return result
    root = evidence.directory / "determinism"
    checks = {}
    same = root / "same_server"
    synthesize_pipe(pipe, pid, same / "repeat.wav", text)
    checks["same_server"] = compare_repeat(evidence.directory, same, root / "compare_same_server")
    if level >= 2:
        kill(pid)
        ensure_server(cfg, exe, t3, s3, pipe, pid, language, knobs, tokenizer_py, ckpt)
        cold = root / "cold_server"
        synthesize_pipe(pipe, pid, cold / "repeat.wav", text)
        checks["cold_server"] = compare_repeat(evidence.directory, cold, root / "compare_cold_server")
    result = {"level": level, "checks": checks, "all_exact": all(x.get("exact_match") for x in checks.values())}
    write_json(evidence.directory / "determinism.json", result)
    evidence.emit("determinism_complete", "determinism", level=level, all_exact=result["all_exact"], checks=checks)
    return result


def analyze_run(evidence: RunEvidence, reference: Path, mode: str):
    if mode == "none":
        evidence.emit("analysis_skipped", "analysis", mode=mode)
        return
    py = analysis_python()
    run([str(py), str(ROOT / "tools/run_tables.py"), str(evidence.directory)])
    if mode == "full":
        run([sys.executable, str(ROOT / "asr_parakeet.py"), "--run-dir", str(evidence.directory)])
        run([str(py), str(ROOT / "tools/analyze_run.py"), str(evidence.directory), str(reference)])
    run([str(py), str(ROOT / "tools/report_html.py"), str(evidence.directory), str(evidence.output_root)])
    freeze = subprocess.run([str(py), "-m", "pip", "freeze", "--all"], check=True, capture_output=True, text=True).stdout
    (evidence.directory / "analysis_packages.txt").write_text(freeze, encoding="utf-8")
    evidence.summary["analysis_packages_sha256"] = sha256(evidence.directory / "analysis_packages.txt")

def ensure_engine() -> str:
    if not CHATTERBOX.is_dir() or not (CHATTERBOX / ".git").is_dir():
        raise SystemExit(f"missing chatterbox.cpp Git sibling at {CHATTERBOX}")
    dirty = subprocess.run(["git", "-C", str(CHATTERBOX), "status", "--porcelain"], check=True, capture_output=True, text=True).stdout.strip()
    if dirty:
        raise SystemExit("chatterbox.cpp has uncommitted changes")
    sha = git_out(["rev-parse", "HEAD"])
    if sha != ENGINE_PIN:
        raise SystemExit(f"chatterbox.cpp HEAD {sha} != pinned {ENGINE_PIN}")
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

def pid_path(cfg: Variant) -> Path:
    return MODELS / cfg.pid_name


def pipe_name(cfg: Variant) -> str:
    tag = hashlib.sha256(str(ROOT).encode() + cfg.pipe_tag).hexdigest()[:12]
    if cfg.pipe_tag == b"turbo":
        return rf"\\.\pipe\chatterbox-turbo-{tag}"
    if cfg.pipe_tag == b"v3":
        return rf"\\.\pipe\chatterbox-v3-{tag}"
    return rf"\\.\pipe\chatterbox-{tag}"


def paths(cfg: Variant, args: LaunchArgs):
    t3 = MODELS / f"chatterbox-t3-{cfg.name}-precision1-{args.t3_weight_type}.gguf"
    s3_family = "v3" if cfg.build_name == "v3" else "meanflow"
    s3 = MODELS / f"chatterbox-s3gen-{s3_family}-precision1-{args.s3_weight_type}.gguf"
    build = CHATTERBOX / "build" / cfg.build_name
    bin_dir = build / "bin"
    return t3, s3, pid_path(cfg), build, bin_dir / "chatterbox-server.exe", bin_dir / "chatterbox-bake.exe", pipe_name(cfg)

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
        if error == 87:
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

def pid_record(pid: Path):
    return read_json(pid) if pid.is_file() else None

def pipe_ready(pipe: str, timeout_ms: int = 0) -> bool:
    return bool(K32.WaitNamedPipeW(pipe, timeout_ms))

def wait_pipe_absent(pipe: str):
    for _ in range(50):
        if not K32.WaitNamedPipeW(pipe, 0) and ctypes.get_last_error() == ERROR_FILE_NOT_FOUND:
            return
        time.sleep(0.1)
    raise RuntimeError("pipe busy")

def runtime_names(cfg: Variant) -> tuple[str, ...]:
    return tuple(RUNTIME_DEFAULTS[cfg.build_name])


def spawn(cfg: Variant, exe: Path, t3: Path, s3: Path, pipe: str, pid: Path, language: str | None,
          knobs: dict[str, str], tokenizer_py: Path | None = None, ckpt: Path | None = None, server_contract: dict | None = None):
    args = [str(exe), str(t3), str(s3), pipe]
    if cfg.needs_language:
        if not language or tokenizer_py is None or ckpt is None:
            raise SystemExit("multilingual tokenizer runtime is required")
        args += [
            "--language", language,
            "--tokenizer-python", str(tokenizer_py),
            "--tokenizer-script", str(CHATTERBOX / "scripts/mtl-tokenize-runtime.py"),
            "--tokenizer-source", str(ckpt / "official_mtl_tokenizer.py"),
            "--tokenizer-tts-source", str(ckpt / "official_mtl_tts.py"),
            "--tokenizer-json", str(ckpt / "grapheme_mtl_merged_expanded_v1.json"),
            "--cangjie-json", str(ckpt / "Cangjie5_TC.json"),
            "--dicta-model", str(ckpt / "dicta-1.0.int8.onnx"),
        ]
    for name in runtime_names(cfg):
        args += [f"--{name}", knobs[name]]
    log_path = ACTIVE_RUN.directory / "server.log"
    log = open(log_path, "ab", buffering=0)
    try:
        log.write(f"# spawn {time.strftime('%Y-%m-%dT%H:%M:%S%z')} family={cfg.name} {' '.join(args)}\n".encode("utf-8"))
        log.flush()
        proc = subprocess.Popen(args, cwd=str(exe.parent), stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=DETACH)
    finally:
        log.close()
    handle = K32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, proc.pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        identity = process_identity(handle)
    finally:
        K32.CloseHandle(handle)
    write_json(pid, {"pid": proc.pid, **identity, "contract": server_contract})
    ACTIVE_RUN.emit("server_start", "server", argv=args, pid=proc.pid, identity=identity, log=str(log_path), contract=server_contract)


def server_contract(cfg: Variant, exe: Path, t3: Path, s3: Path, language: str | None, knobs: dict[str, str],
                    tokenizer_py: Path | None, ckpt: Path | None) -> dict:
    contract = {
        "variant": cfg.name,
        "server": file_identity(exe),
        "t3": file_identity(t3),
        "s3": file_identity(s3),
        "language": language,
        "knobs": dict(knobs),
    }
    if cfg.needs_language:
        if tokenizer_py is None or ckpt is None:
            raise RuntimeError("missing tokenizer runtime")
        contract["tokenizer"] = {
            "python": str(tokenizer_py.resolve()),
            "environment_fingerprint": _requirements_fingerprint(ROOT / "tools/mtl_tokenizer.requirements.txt", PYTHON_ENV_BOOTSTRAP["tokenizer"]),
            "adapter": file_identity(CHATTERBOX / "scripts/mtl-tokenize-runtime.py"),
            "source": file_identity(ckpt / "official_mtl_tokenizer.py"),
            "tts_source": file_identity(ckpt / "official_mtl_tts.py"),
            "json": file_identity(ckpt / "grapheme_mtl_merged_expanded_v1.json"),
            "cangjie": file_identity(ckpt / "Cangjie5_TC.json"),
            "dicta": file_identity(ckpt / "dicta-1.0.int8.onnx"),
        }
    return contract


def ensure_server(cfg: Variant, exe: Path, t3: Path, s3: Path, pipe: str, pid: Path, language: str | None,
                  knobs: dict[str, str], tokenizer_py: Path | None, ckpt: Path | None) -> dict:
    wanted = server_contract(cfg, exe, t3, s3, language, knobs, tokenizer_py, ckpt)
    prior = pid_record(pid)
    if prior and prior.get("contract") == wanted and running(pid):
        for _ in range(30):
            if pipe_ready(pipe, 1000):
                ACTIVE_RUN.emit("server_reused", "server", pid=prior["pid"], contract=wanted)
                return prior
            time.sleep(0.1)
    if running(pid):
        kill(pid)
    wait_pipe_absent(pipe)
    spawn(cfg, exe, t3, s3, pipe, pid, language, knobs, tokenizer_py, ckpt, wanted)
    for _ in range(120):
        if not running(pid):
            raise RuntimeError("server exited during startup")
        if pipe_ready(pipe, 1000):
            record = pid_record(pid)
            ACTIVE_RUN.emit("server_ready", "server", pid=record["pid"], contract=wanted)
            return record
        time.sleep(0.25)
    raise RuntimeError("server pipe startup timeout")


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
    return SpeakResult(wall_s=wall_s, predicted_count=stats.get("predicted"), dropped_count=stats.get("dropped"),
                       eos=stats.get("eos"), n_past=stats.get("n_past"), units=stats.get("units"),
                       text_tokens=stats.get("text_tokens"), max_unit_predicted=stats.get("max_unit_predicted"),
                       knobs=knobs or None)


def synthesize_pipe(pipe: str, pid: Path, out: Path, text: str) -> SpeakResult:
    for _ in range(120):
        if not running(pid):
            raise RuntimeError("daemon")
        if pipe_ready(pipe, 1000):
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
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    if ACTIVE_RUN is not None:
        ACTIVE_RUN.emit("pipe_connected", "transport", pipe=pipe)
        ACTIVE_RUN.log.flush(); ACTIVE_RUN.log.close()
    try:
        with open(pipe, "r+b", buffering=0) as f:
            message = memoryview(f"{out}\n{len(payload)}\n".encode("utf-8") + payload)
            while message:
                sent = f.write(message)
                if not sent:
                    raise RuntimeError("pipe write incomplete")
                message = message[sent:]
            ack = f.readline(32769)
    finally:
        if ACTIVE_RUN is not None:
            ACTIVE_RUN.log = (ACTIVE_RUN.directory / "events.jsonl").open("a", encoding="utf-8")
    if len(ack) > 32768 or not ack.endswith(b"\n"):
        raise RuntimeError("invalid or truncated acknowledgement")
    if ACTIVE_RUN is not None:
        ACTIVE_RUN.emit("request_sent", "transport", utf8_bytes=len(payload), input_sha256=hashlib.sha256(payload).hexdigest(), output_path=str(out))
        ACTIVE_RUN.emit("reply_received", "transport", reply=ack.decode("utf-8", errors="strict"), output_path=str(out))
    wall = time.perf_counter() - t0
    if not ack.startswith(b"ok "):
        if ack.startswith(b"err "):
            raise RuntimeError(ack[4:].decode("utf-8", errors="replace").strip())
        raise RuntimeError("synthesize")
    return speak_result_from_stats(wall, parse_synth_stats(ack))


def usage(cfg: Variant):
    runtime = " ".join(f"[--{n} <v>]" for n in runtime_names(cfg))
    weight_types = "|".join(WEIGHT_TYPES)
    analysis_modes = "|".join(ANALYSIS_MODES)
    controls = f"[--t3-weight-type {weight_types}] [--s3-weight-type {weight_types}] [--reference <wav>] [--analysis {analysis_modes}] [--determinism-repeats 0|1|2]"
    tail = "<text> <language>" if cfg.needs_language else "<text>"
    return f"usage: python tts_{cfg.name}.py [-h] {runtime} {controls} {tail}"


def normalize_knob(name: str, raw: str) -> str:
    kind = RUNTIME_KINDS[name]
    try:
        if kind == "i":
            if not re.fullmatch(r"[+-]?[0-9]+", raw): raise ValueError("integer syntax")
            value = int(raw, 10)
            if not -2147483648 <= value <= 2147483647: raise ValueError("integer range")
        else:
            value = float(raw)
            if not math.isfinite(value): raise ValueError("non-finite value")
        if name in ("top-k", "temperature", "trim-fade-samples") and value < 0: raise ValueError("must be nonnegative")
        if name in ("n-predict", "repeat-penalty", "cfm-steps") and value <= 0: raise ValueError("must be positive")
        if name == "top-p" and not 0 < value <= 1: raise ValueError("must be in (0,1]")
        if name == "min-p" and not 0 <= value <= 1: raise ValueError("must be in [0,1]")
        return str(value)
    except ValueError as exc:
        raise SystemExit(f"invalid {name}: {exc}") from exc


def parse_variant_args(cfg: Variant, argv: list[str]) -> LaunchArgs:
    args = argv[1:]
    runtime = dict(RUNTIME_DEFAULTS[cfg.build_name])
    conversion = dict(CONVERSION_DEFAULTS[cfg.build_name])
    control = dict(CONTROL_DEFAULTS)
    reference = REF
    seen = set()
    i = 0
    allowed_runtime = set(runtime)
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help", "-?"):
            print(usage(cfg)); raise SystemExit(0)
        if not a.startswith("--"):
            break
        if i + 1 >= len(args): raise SystemExit(usage(cfg))
        name, raw = a[2:], args[i + 1]
        if name in seen: raise SystemExit(f"duplicate flag: --{name}")
        seen.add(name)
        if name in allowed_runtime:
            runtime[name] = normalize_knob(name, raw)
        elif name == "t3-weight-type":
            if raw not in WEIGHT_TYPES: raise SystemExit(f"invalid t3-weight-type: {raw}")
            conversion[name] = raw
        elif name == "s3-weight-type":
            if raw not in WEIGHT_TYPES: raise SystemExit(f"invalid s3-weight-type: {raw}")
            conversion[name] = raw
        elif name == "reference":
            reference = Path(raw).expanduser().resolve()
        elif name == "analysis":
            if raw not in ANALYSIS_MODES: raise SystemExit(f"invalid analysis mode: {raw}")
            control[name] = raw
        elif name == "determinism-repeats":
            try: repeats = int(raw)
            except ValueError as exc: raise SystemExit("invalid determinism-repeats") from exc
            if repeats < 0 or repeats > 2: raise SystemExit("determinism-repeats must be 0, 1, or 2")
            control[name] = str(repeats)
        else:
            raise SystemExit(usage(cfg))
        i += 2
    rest = args[i:]
    if cfg.needs_language:
        if len(rest) != 2: raise SystemExit(usage(cfg))
        text, language = rest[0], rest[1].lower()
    else:
        if len(rest) != 1: raise SystemExit(usage(cfg))
        text, language = rest[0], None
    return LaunchArgs(text, language, runtime, conversion["t3-weight-type"], conversion["s3-weight-type"],
                      reference, control["analysis"], int(control["determinism-repeats"]))

def cmake_definitions(cfg: Variant) -> dict[str, str]:
    if VULKAN is None:
        raise RuntimeError("Vulkan SDK was not resolved")
    return {
        **dict(CMAKE_FLAGS),
        "TTS_FAMILY": cfg.build_name,
        "Vulkan_INCLUDE_DIR": str((VULKAN / "Include").resolve()),
        "Vulkan_LIBRARY": str((VULKAN / "Lib/vulkan-1.lib").resolve()),
        "Vulkan_GLSLC_EXECUTABLE": str((VULKAN / "Bin/glslc.exe").resolve()),
    }


def _git_tree_digest(paths: tuple[str, ...]) -> str:
    listing = git_out(["ls-tree", "-r", "HEAD", "--", *paths], CHATTERBOX)
    return hashlib.sha256(listing.encode("utf-8")).hexdigest()


def build_contract(cfg: Variant):
    if CMAKE is None or VULKAN is None or VCVARS is None:
        raise RuntimeError("Windows build tools were not resolved")
    return {
        "native_tree": _git_tree_digest(("include", "src", "CMakeLists.txt")),
        "cmake_blob": blob_rev("CMakeLists.txt"),
        "ggml_rev": git_out(["rev-parse", "HEAD"], CHATTERBOX / "ggml"),
        "family": cfg.build_name,
        "generator": CMAKE_GENERATOR,
        "architecture": CMAKE_ARCH,
        "toolchain": {
            "cmake": file_identity(CMAKE),
            "glslc": file_identity(VULKAN / "Bin/glslc.exe"),
            "vulkan_library": file_identity(VULKAN / "Lib/vulkan-1.lib"),
            "vcvars64": file_identity(VCVARS),
        },
        "cmake_definitions": cmake_definitions(cfg),
    }


def cmake_identity(obj):
    if not obj:
        return None
    return {
        "cmake_blob": obj.get("cmake_blob"),
        "ggml_rev": obj.get("ggml_rev"),
        "family": obj.get("family"),
        "generator": obj.get("generator"),
        "architecture": obj.get("architecture"),
        "toolchain": obj.get("toolchain"),
        "cmake_definitions": obj.get("cmake_definitions"),
    }


def ensure_ggml():
    ggml = CHATTERBOX / "ggml"
    if not (ggml / ".git").exists():
        run(["git", "clone", "--filter=blob:none", "https://github.com/ggml-org/ggml.git", str(ggml)])
    if not (ggml / "CMakeLists.txt").is_file():
        raise SystemExit("ggml checkout is incomplete")
    dirty = subprocess.run(
        ["git", "-C", str(ggml), "status", "--porcelain"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if dirty:
        raise SystemExit("ggml checkout is dirty")
    actual = git_out(["rev-parse", "HEAD"], ggml)
    if actual != GGML_REV:
        run(["git", "-C", str(ggml), "fetch", "origin", GGML_REV, "--depth", "1"])
        run(["git", "-C", str(ggml), "checkout", "--detach", GGML_REV])
        actual = git_out(["rev-parse", "HEAD"], ggml)
    if actual != GGML_REV:
        raise SystemExit(f"ggml {actual} != required {GGML_REV}")
    dirty = subprocess.run(
        ["git", "-C", str(ggml), "status", "--porcelain"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if dirty:
        raise SystemExit("ggml checkout is dirty after pinning")


def _identity_matches(record: dict) -> bool:
    try:
        path = Path(record["path"])
        return path.is_file() and path.stat().st_size == record.get("bytes") and sha256(path) == record.get("sha256")
    except (KeyError, OSError, TypeError):
        return False


def ensure_build(cfg: Variant, pid: Path, build: Path, exe: Path, bake: Path):
    ensure_ggml()
    stamp = MODELS / f"{cfg.build_name}.build-contract.json"
    wanted = build_contract(cfg)
    prior = read_json(stamp) or {}
    prior_records = normalize_binary_records(prior.get("binaries"))
    server_record = find_binary_record(prior_records, "chatterbox-server.exe")
    bake_record = find_binary_record(prior_records, "chatterbox-bake.exe")
    reusable = (
        prior.get("identity") == wanted
        and server_record is not None and bake_record is not None
        and all(_identity_matches(record) for record in prior_records)
    )
    if reusable:
        result = {"identity": wanted, "binaries": prior_records}
        ACTIVE_RUN.emit("build_decision", "build", reused=True, configure=False, compile=False, contract=wanted)
        return result
    kill(pid)
    for name in cfg.other_pids:
        kill(MODELS / name)
    need_configure = cmake_identity(prior.get("identity")) != cmake_identity(wanted) or not (build / "CMakeCache.txt").is_file()
    ACTIVE_RUN.emit("build_decision", "build", reused=False, configure=need_configure, compile=True, contract=wanted)
    if need_configure:
        definitions = cmake_definitions(cfg)
        run([
            CMAKE, "-S", str(CHATTERBOX), "-B", str(build), "-G", CMAKE_GENERATOR, "-A", CMAKE_ARCH,
            *(f"-D{name}={value}" for name, value in definitions.items()),
        ])
    build_target(build, "chatterbox-server")
    build_target(build, "chatterbox-bake")
    if not exe.is_file() or not bake.is_file():
        raise RuntimeError("native build did not produce required executables")
    records = [file_identity(path) for path in sorted(exe.parent.iterdir()) if path.suffix.lower() in (".exe", ".dll")]
    result = {"identity": wanted, "binaries": records}
    write_json(stamp, result)
    return result


def ensure_converter_venv(cfg: Variant) -> Path:
    return requirements_venv(ROOT / cfg.venv_name, ROOT / "tools/converter.requirements.txt", "converter")


def ensure_tokenizer_venv() -> Path:
    return requirements_venv(ROOT / ".venv-tokenizer-v3", ROOT / "tools/mtl_tokenizer.requirements.txt", "tokenizer")


def ensure_assets(cfg: Variant) -> Path:
    ckpt = ROOT / cfg.ckpt_name
    ckpt.mkdir(parents=True, exist_ok=True)
    for name in cfg.assets:
        dest = ckpt / name
        reused = dest.is_file()
        source = f"{cfg.hf}/{name}"
        if not reused:
            download(source, dest)
        ACTIVE_RUN.emit("asset_resolved", "assets", name=name, configured_source=source, reused=reused,
                        origin_verified=not reused, verification="downloaded from configured immutable URL" if not reused else "local bytes hashed; cached origin not independently verified")
    for name, source in cfg.external_assets:
        dest = ckpt / name
        reused = dest.is_file()
        if not reused:
            download(source, dest)
        ACTIVE_RUN.emit("asset_resolved", "assets", name=name, configured_source=source, reused=reused,
                        origin_verified=not reused, verification="downloaded from configured immutable URL" if not reused else "local bytes hashed; cached origin not independently verified")
    return ckpt


CONVERT_DEPS = ("quant_policy.py", "precision_policy.json")
BAKE_SOURCES = (
    "src/bake.cpp", "src/bake_native.h", "src/main.cpp", "src/voice_features.cpp", "src/voice_features.h",
    "src/mel_extract_stft.cpp", "src/voice_encoder.cpp", "src/voice_encoder.h", "src/campplus.cpp",
    "src/campplus.h", "src/s3tokenizer.cpp", "src/s3tokenizer.h",
)


def blob_rev(path: str) -> str:
    return git_out(["rev-parse", f"HEAD:{path}"])


def conversion_input_names(cfg: Variant, kind: str) -> tuple[str, ...]:
    if kind == "t3":
        base = (cfg.t3_ckpt, "conds.pt", "ve.safetensors")
        if cfg.needs_language:
            return base + ("grapheme_mtl_merged_expanded_v1.json", "Cangjie5_TC.json", "official_mtl_tokenizer.py", "official_mtl_tts.py")
        return base + ("vocab.json", "merges.txt", "added_tokens.json")
    if cfg.needs_language:
        return ("s3gen.safetensors", "conds.pt")
    return ("s3gen_meanflow.safetensors", "conds.pt")


def conversion_contract(cfg: Variant, kind: str, weight_type: str, ckpt: Path):
    if kind not in ("t3", "s3"):
        raise SystemExit(f"unknown conversion kind: {kind}")
    script = cfg.t3_script if kind == "t3" else cfg.s3_script
    return {
        "kind": kind,
        "hf_base": cfg.hf,
        "script": script,
        "script_blob": blob_rev(f"scripts/{script}"),
        "deps_blob": [blob_rev(f"scripts/{d}") for d in CONVERT_DEPS],
        "converter_environment_fingerprint": _requirements_fingerprint(ROOT / "tools/converter.requirements.txt", PYTHON_ENV_BOOTSTRAP["converter"]),
        "weight_type": weight_type,
        "input_assets": {name: sha256(ckpt / name) for name in conversion_input_names(cfg, kind)},
        **({"t3_ckpt": cfg.t3_ckpt} if kind == "t3" else {}),
    }


def conversion_identity(obj):
    if obj is None:
        return None
    keys = ("kind", "hf_base", "script", "script_blob", "deps_blob", "converter_environment_fingerprint", "weight_type", "input_assets")
    if obj.get("kind") == "t3":
        keys += ("t3_ckpt",)
    return {key: obj.get(key) for key in keys}


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
        "    if key.startswith('tokenizer.') or key == 'chatterbox.tokenizer.json': continue\n"
        "    metadata[key]={'types':[str(t) for t in f.types],'data':[f.parts[i].tolist() for i in f.data]}\n"
        "nbytes=0\n"
        "for t in r.tensors:\n"
        "    c[t.tensor_type.name]+=1\n"
        "    nbytes+=int(t.n_bytes)\n"
        "    inventory.append({'name':t.name,'type':t.tensor_type.name,'shape':[int(x) for x in t.shape]})\n"
        "print(json.dumps({'n_tensors':len(r.tensors),'nbytes':nbytes,'tensor_types':dict(sorted(c.items())),'inventory':inventory,'metadata':metadata}))\n"
    )
    out = subprocess.run([str(py), "-c", script, str(path)], check=True, capture_output=True, text=True)
    return json.loads(out.stdout)


def write_convert_stamp(path: Path, contract: dict, types: dict) -> dict:
    obj = {**contract, **types}
    write_json(path, obj)
    return obj


def convert_t3(cfg: Variant, py: Path, ckpt: Path, t3: Path, contract, weight_type: str) -> dict:
    tmp = t3.with_suffix(".gguf.converting")
    tmp.unlink(missing_ok=True)
    run([str(py), str(CHATTERBOX / "scripts" / cfg.t3_script), str(ckpt), str(tmp), cfg.t3_ckpt, "--matrix-type", weight_type])
    types = gguf_type_histogram(py, tmp)
    tmp.replace(t3)
    return write_convert_stamp(MODELS / f"{t3.stem}.convert.json", contract, types)


def convert_s3(cfg: Variant, py: Path, ckpt: Path, s3: Path, contract, weight_type: str) -> dict:
    tmp = s3.with_suffix(".gguf.converting")
    tmp.unlink(missing_ok=True)
    run([str(py), str(CHATTERBOX / "scripts" / cfg.s3_script), str(ckpt), str(tmp), "--weight-type", weight_type])
    types = gguf_type_histogram(py, tmp)
    tmp.replace(s3)
    return write_convert_stamp(MODELS / f"{s3.stem}.convert.json", contract, types)


def ensure_converted(cfg: Variant, py: Path, ckpt: Path, t3: Path, s3: Path, args: LaunchArgs):
    t3_contract = conversion_contract(cfg, "t3", args.t3_weight_type, ckpt)
    s3_contract = conversion_contract(cfg, "s3", args.s3_weight_type, ckpt)
    t3_stamp = MODELS / f"{t3.stem}.convert.json"
    s3_stamp = MODELS / f"{s3.stem}.convert.json"
    prior_t3 = read_json(t3_stamp)
    prior_s3 = read_json(s3_stamp)
    t3_changed = (not t3.is_file()) or conversion_identity(prior_t3) != t3_contract
    s3_changed = (not s3.is_file()) or conversion_identity(prior_s3) != s3_contract
    ACTIVE_RUN.emit("conversion_decision", "conversion", t3_reused=not t3_changed, s3_reused=not s3_changed,
                    t3_contract=t3_contract, s3_contract=s3_contract)
    if t3_changed:
        prior_t3 = convert_t3(cfg, py, ckpt, t3, t3_contract, args.t3_weight_type)
    if s3_changed:
        prior_s3 = convert_s3(cfg, py, ckpt, s3, s3_contract, args.s3_weight_type)
    def cached_types(stamp, model, stamp_path, contract):
        if stamp and all(key in stamp for key in CONVERT_STAMP_EXTRA) and stamp.get("inventory") is not None:
            return {key: stamp[key] for key in (*CONVERT_STAMP_EXTRA, "inventory", "metadata")}
        types = gguf_type_histogram(py, model)
        write_convert_stamp(stamp_path, contract, types)
        return types
    t3_types = cached_types(prior_t3, t3, t3_stamp, t3_contract)
    s3_types = cached_types(prior_s3, s3, s3_stamp, s3_contract)
    return t3_contract, s3_contract, t3_types, s3_types


def ensure_baked(cfg: Variant, reference: Path, base_t3: Path, base_s3: Path, bake: Path, t3_contract: dict, s3_contract: dict):
    if not reference.is_file():
        raise FileNotFoundError(str(reference))
    wanted = {
        "family": cfg.name,
        "bake_blob": [blob_rev(p) for p in BAKE_SOURCES],
        "bake_binary": file_identity(bake),
        "reference": file_identity(reference),
        "base_t3": file_identity(base_t3),
        "base_s3": file_identity(base_s3),
        "t3_conversion": t3_contract,
        "s3_conversion": s3_contract,
    }
    key = hashlib.sha256(json.dumps(wanted, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()[:24]
    voice_dir = MODELS / "voices" / cfg.name / key
    t3 = voice_dir / "t3.gguf"
    s3 = voice_dir / "s3.gguf"
    stamp = voice_dir / "bake.json"
    prior = read_json(stamp)
    actual = {"t3": file_identity(t3), "s3": file_identity(s3)} if t3.is_file() and s3.is_file() else None
    if prior and prior.get("identity") == wanted and prior.get("outputs") == actual:
        ACTIVE_RUN.emit("bake_decision", "bake", reused=True, identity=prior, voice_key=key)
        return t3, s3, prior
    ACTIVE_RUN.emit("bake_decision", "bake", reused=False, identity=wanted, voice_key=key)
    tmp = voice_dir.with_name(voice_dir.name + ".baking")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=False)
    tmp_t3, tmp_s3 = tmp / "t3.gguf", tmp / "s3.gguf"
    shutil.copy2(base_t3, tmp_t3)
    shutil.copy2(base_s3, tmp_s3)
    try:
        run([str(bake), str(tmp_t3), str(tmp_s3), str(reference)], cwd=str(ACTIVE_RUN.directory))
        outputs = {"t3": file_identity(tmp_t3), "s3": file_identity(tmp_s3)}
        write_json(tmp / "bake.json", {"identity": wanted, "outputs": outputs})
        voice_dir.parent.mkdir(parents=True, exist_ok=True)
        if voice_dir.exists():
            shutil.rmtree(voice_dir)
        tmp.replace(voice_dir)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    result = read_json(stamp)
    ACTIVE_RUN.emit("bake_complete", "bake", voice_key=key, outputs=result["outputs"])
    return t3, s3, result

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
    values = dict(args.knobs)
    original = args.text.replace("\r\n", "\n").replace("\r", "\n")
    evidence.summary.update(
        original_text=args.text,
        language=args.language,
        knobs_cli=values,
        analysis_mode=args.analysis,
        determinism_level=args.determinism_repeats,
        conversion_types={"t3": args.t3_weight_type, "s3": args.s3_weight_type},
    )
    evidence.persist()
    with evidence.stage("prerequisites"):
        MODELS.mkdir(parents=True, exist_ok=True)
        if not args.reference.is_file():
            raise FileNotFoundError(str(args.reference))
        ensure_engine()
        vcvars = resolve_windows_tools()
        evidence.summary["sources"] = {
            "trident": source_identity(ROOT),
            "engine": source_identity(CHATTERBOX),
            "ggml_pin": GGML_REV,
            "engine_tree": git_out(["ls-tree", "-r", "HEAD"], CHATTERBOX),
            "trident_tree": git_out(["ls-tree", "-r", "HEAD"], ROOT),
        }
        evidence.summary["host_inventory"] = host_inventory()
        evidence.summary["tool_versions"] = {
            "cmake": subprocess.run([str(CMAKE), "--version"], capture_output=True, text=True, check=True).stdout,
            "git": subprocess.run(["git", "--version"], capture_output=True, text=True, check=True).stdout,
            "vulkan_sdk": str(VULKAN),
            "vcvars64": str(vcvars),
        }
        evidence.emit("source_identity", "prerequisites", sources=evidence.summary["sources"], tools=evidence.summary["tool_versions"])
    text = original
    with evidence.stage("input"):
        evidence.summary.update(
            transport_text=text,
            input_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            utf8_bytes=len(text.encode("utf-8")),
            characters=len(text),
        )
        evidence.emit("text_transport", "input", policy="verbatim_utf8_to_engine", original_text=original, transport_text=text)
        evidence.persist()
    base_t3, base_s3, pid, build, exe, bake, pipe = paths(cfg, args)
    with evidence.stage("build"):
        build_info = ensure_build(cfg, pid, build, exe, bake)
        evidence.summary["sources"]["ggml"] = source_identity(CHATTERBOX / "ggml")
        evidence.summary["build_contract"] = build_info["identity"]
        evidence.summary["binaries"] = normalize_binary_records(build_info["binaries"])
        evidence.summary["server"] = file_identity(exe)
        if (build / "CMakeCache.txt").is_file():
            evidence.summary["cmake_cache"] = file_identity(build / "CMakeCache.txt")
            (evidence.directory / "CMakeCache.txt").write_bytes((build / "CMakeCache.txt").read_bytes())
        evidence.persist()
    with evidence.stage("assets"):
        py = ensure_converter_venv(cfg)
        ckpt = ensure_assets(cfg)
        tokenizer_py = ensure_tokenizer_venv() if cfg.needs_language else None
        all_assets = [*cfg.assets, *(name for name, _ in cfg.external_assets)]
        evidence.summary["assets"] = {
            "hf_base": cfg.hf,
            "files": [file_identity(ckpt / name) for name in all_assets],
            "reference": file_identity(args.reference),
            "safetensors": [safetensors_inventory(ckpt / name) for name in cfg.assets if name.endswith(".safetensors")],
        }
        versions = subprocess.run([str(py), "-m", "pip", "freeze", "--all"], check=True, capture_output=True, text=True).stdout
        evidence.summary["converter_packages"] = versions
        if tokenizer_py is not None:
            evidence.summary["tokenizer_packages"] = subprocess.run(
                [str(tokenizer_py), "-m", "pip", "freeze", "--all"], check=True, capture_output=True, text=True
            ).stdout
        evidence.emit("assets", "assets", identity=evidence.summary["assets"], converter_packages=versions,
                      tokenizer_packages=evidence.summary.get("tokenizer_packages"))
    with evidence.stage("conversion"):
        t3_contract, s3_contract, t3_types, s3_types = ensure_converted(cfg, py, ckpt, base_t3, base_s3, args)
        evidence.summary["conversion"] = {"t3": t3_contract, "s3": s3_contract}
        evidence.summary["base_models"] = {
            "t3": {**file_identity(base_t3), **t3_types},
            "s3": {**file_identity(base_s3), **s3_types},
        }
    with evidence.stage("bake"):
        t3, s3, bake_info = ensure_baked(cfg, args.reference, base_t3, base_s3, bake, t3_contract, s3_contract)
        evidence.summary["bake"] = bake_info
        evidence.summary["models"] = {
            "t3": {**file_identity(t3), "tensor_types": t3_types.get("tensor_types")},
            "s3": {**file_identity(s3), "tensor_types": s3_types.get("tensor_types")},
        }
    with evidence.stage("server"):
        evidence.summary["server_process"] = ensure_server(cfg, exe, t3, s3, pipe, pid, args.language, values, tokenizer_py, ckpt)
    with evidence.stage("request"):
        result = synthesize_pipe(pipe, pid, evidence.work_out, text)
        engine_duration = wav_duration_s(evidence.work_out)
        events = [json.loads(line) for line in (evidence.directory / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        if not any(e.get("event") == "request_complete" and e.get("component") == "engine" for e in events):
            raise RuntimeError("missing engine completion evidence")
        if cfg.needs_language:
            front = next(e for e in reversed(events) if e.get("event") == "number_verbalized" and e.get("component") == "engine")
            normalization = {
                "policy": front["policy"], "provider": front["provider"], "language": front["language_id"],
                "original_text": front["original_text"], "transport_text": front["transport_text"],
                "changes": front.get("changes") or [],
            }
        else:
            normalization = {"policy": "engine_english_prepare_text", "language": "en", "original_text": original,
                             "transport_text": original, "changes": []}
        write_json(evidence.directory / "normalization.json", normalization)
        evidence.summary.update(transport_text=normalization["transport_text"], normalization=normalization)
        evidence.emit("text_frontend_observed", "request", **normalization)
        output_event = next(e for e in reversed(events) if e.get("event") == "output_write_end")
        generated = file_identity(evidence.work_out)
        if generated["sha256"] != output_event["wav_sha256"]:
            raise RuntimeError("WAV hash differs from engine evidence")
        synth = next(e for e in reversed(events) if e.get("event") == "synthesis_complete")
        evidence.work_out.replace(evidence.out)
        evidence.summary["engine_output"] = file_identity(evidence.out)
        evidence.summary["metrics"] = {
            **vars(result),
            "engine_duration_s": engine_duration,
            "request_wall_rtf": result.wall_s / engine_duration,
            "synthesis_host_wall_s": synth["host_wall_s"],
            "synthesis_host_wall_rtf": synth["host_wall_s"] / engine_duration,
            "rtf_definition": "host wall seconds / raw engine WAV duration; request includes connection; synthesis excludes model load",
        }
        evidence.summary["output"] = evidence.summary["engine_output"]
        evidence.summary["status"] = "raw_audio_complete"
        evidence.persist()
    with evidence.stage("determinism"):
        evidence.summary["determinism"] = run_determinism(
            evidence, args.determinism_repeats, cfg, text, pipe, pid, exe, t3, s3, args.language, values, tokenizer_py, ckpt
        )
        evidence.persist()
    with evidence.stage("output"):
        final_duration = wav_duration_s(evidence.out)
        evidence.summary["metrics"]["duration_s"] = final_duration
        evidence.summary["output"] = file_identity(evidence.out)
        evidence.summary["status"] = "audio_complete"
        evidence.emit("output_verified", "output", identity=evidence.summary["output"],
                      engine_identity=evidence.summary["engine_output"], metrics=evidence.summary["metrics"], output_path=str(evidence.out))
        evidence.persist()
    with evidence.stage("analysis"):
        analyze_run(evidence, args.reference, args.analysis)
        artifacts = [
            "events.parquet", "text_tokens.parquet", "t3_tokens.parquet", "s3_tokens.parquet", "features.parquet",
            "acoustic_frames.parquet", "analysis_metrics.json", "asr_parakeet.json", "normalization_alignment.json",
            "librosa.jsonl", "parselmouth.jsonl", "pyworld.jsonl", "torchaudio.jsonl", "torchcrepe.jsonl",
            "pyloudnorm.jsonl", "scipy.jsonl", "resemblyzer.jsonl", "editdistance.jsonl", "waveform_tokens.png",
            "spectrogram_tokens.png", "f0_estimators.png", "energy_spectral.png", "report.html", "analysis_packages.txt",
            "determinism.json",
        ]
        evidence.summary["analysis_artifacts"] = {
            name: file_identity(evidence.directory / name) for name in artifacts if (evidence.directory / name).is_file()
        }
        dashboard = evidence.output_root / "tts_dashboard.html"
        if dashboard.is_file():
            evidence.summary["dashboard"] = file_identity(dashboard)
        evidence.persist()
    print(
        f"text_tokens={result.text_tokens} predicted={result.predicted_count} eos={result.eos} "
        f"units={result.units} max_unit_predicted={result.max_unit_predicted} duration_s={final_duration:.3f} "
        f"wall_s={result.wall_s:.3f} request_wall_rtf={result.wall_s / engine_duration:.3f} "
        f"synthesis_host_wall_rtf={synth['host_wall_s'] / engine_duration:.3f}", file=sys.stderr, flush=True,
    )
    print(evidence.out, flush=True)
    print(evidence.directory, file=sys.stderr, flush=True)

def launch_variant(cfg: Variant, argv: list[str]):
    global ACTIVE_RUN
    args = parse_variant_args(cfg, argv)
    ACTIVE_RUN = RunEvidence(cfg, argv)
    evidence = ACTIVE_RUN
    print(f"Run evidence: {evidence.directory}", file=sys.stderr, flush=True)
    try:
        run_variant(cfg, args)
        evidence.summary.update(status="execution_complete", exit_code=0, launcher_wall_s=time.perf_counter() - evidence.start,
                                active_stage="complete")
        evidence.emit("request_complete", "request", status=evidence.summary["status"])
    except BaseException as exc:
        code = exc.code if isinstance(exc, SystemExit) and isinstance(exc.code, int) else 1
        evidence.summary.update(status="failed", error=str(exc), error_type=type(exc).__name__, exit_code=code,
                                launcher_wall_s=time.perf_counter() - evidence.start)
        for path, key in ((evidence.work_out, "unacknowledged_work_output"), (evidence.out, "unacknowledged_output")):
            if path.exists():
                evidence.summary[key] = file_identity(path)
        evidence.emit("request_failed", evidence.summary.get("active_stage", "parse"),
                      error=str(exc), error_type=type(exc).__name__, exit_code=code)
        raise
    finally:
        try:
            evidence.persist()
        finally:
            evidence.log.close()
            ACTIVE_RUN = None
