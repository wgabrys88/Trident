import ctypes
import json
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tts_settings import (
    CMAKE_ARCH, CMAKE_FLAGS, CMAKE_GENERATOR, CONVERSION_DEFAULTS,
    PYTHON_ENV_BOOTSTRAP, PYTORCH_CPU_INDEX, RUNTIME_DEFAULTS, WEIGHT_TYPES,
)

ROOT = Path(__file__).resolve().parent
CHATTERBOX = ROOT.parent / "chatterbox.cpp"
MODELS = ROOT / "models"
REF = ROOT / "reference.wav"
GGML_REV = "7840aaba1989c6deeefede1d77d5aaf8f52b947e"
VULKAN: Path | None = None
CMAKE: Path | None = None
VCVARS: Path | None = None
ENGINE_PIN = "92f04db4c49a367451d68a85a630b881c7153ad7"
DETACH = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
K32 = ctypes.WinDLL("kernel32", use_last_error=True)
K32.WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
K32.WaitNamedPipeW.restype = ctypes.c_int
K32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
K32.OpenProcess.restype = ctypes.c_void_p
K32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
K32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
K32.CloseHandle.argtypes = [ctypes.c_void_p]
K32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint)]
K32.QueryFullProcessImageNameW.restype = ctypes.c_int
K32.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
K32.GetProcessTimes.restype = ctypes.c_int
K32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
K32.GetExitCodeProcess.restype = ctypes.c_int
PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_FILE_NOT_FOUND = 2
STATS_KEYS = ("predicted", "dropped", "eos", "n_past", "units", "text_tokens", "max_unit_predicted")
FAMILY = {
    "gpt2": {
        "t3_script": "convert-t3-gpt2-to-gguf.py",
        "s3_script": "convert-s3gen-to-gguf.py",
        "ckpt": ".ckpt",
        "s3_family": "meanflow",
        "siblings": ("nano", "turbo"),
    },
    "v3": {
        "t3_script": "convert-t3-v3-to-gguf.py",
        "s3_script": "convert-s3gen-v3-to-gguf.py",
        "ckpt": ".ckpt-v3",
        "s3_family": "v3",
        "siblings": ("v3",),
    },
}


@dataclass(frozen=True)
class Variant:
    name: str
    hf: str
    assets: tuple[str, ...]
    t3_ckpt: str
    build_name: str
    needs_language: bool = False
    external_assets: tuple[tuple[str, str], ...] = ()


@dataclass
class LaunchArgs:
    text: str
    language: str | None
    knobs: dict[str, str]
    t3_weight_type: str
    s3_weight_type: str
    reference: Path


def run(cmd, **kw):
    subprocess.run(cmd, check=True, **kw)


def git_out(args, repo=CHATTERBOX):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def json_text(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"


def read_json(path: Path):
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json_text(obj), encoding="utf-8")
    tmp.replace(path)


def file_stamp(path: Path) -> dict:
    stat = path.stat()
    return {"path": str(path.resolve()), "bytes": stat.st_size, "mtime": stat.st_mtime_ns}


def vs_installation() -> Path:
    vswhere = Path("C:/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe")
    if not vswhere.is_file():
        raise SystemExit("vswhere.exe not found")
    proc = subprocess.run([str(vswhere), "-latest", "-products", "*", "-requires",
                           "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                          check=True, capture_output=True, text=True)
    value = proc.stdout.strip()
    if not value:
        raise SystemExit("Visual Studio Build Tools installation not found")
    return Path(value)


def resolve_windows_tools() -> Path:
    global CMAKE, VULKAN, VCVARS
    found = shutil.which("cmake")
    if not found:
        raise SystemExit("cmake not on PATH")
    CMAKE = Path(found)
    candidates = sorted(
        Path("C:/VulkanSDK").glob("*/Bin/glslc.exe"),
        key=lambda x: tuple(int(v) for v in re.findall(r"\d+", x.parts[-3])), reverse=True,
    )
    if not candidates:
        raise SystemExit("Vulkan SDK installation not found")
    VULKAN = candidates[0].parents[1]
    VCVARS = vs_installation() / "VC/Auxiliary/Build/vcvars64.bat"
    if not VCVARS.is_file():
        raise SystemExit(f"vcvars64.bat not found: {VCVARS}")
    return VCVARS


def python311() -> Path:
    if sys.version_info[:2] != (3, 11):
        raise SystemExit("need Python 3.11")
    return Path(sys.executable).resolve()


def _venv_stamp() -> str:
    return (ROOT / "requirements.txt").read_text(encoding="utf-8") + json.dumps({
        "torch": list(PYTHON_ENV_BOOTSTRAP["torch"]),
        "torch_index": PYTORCH_CPU_INDEX,
    }, sort_keys=True, separators=(",", ":"))


def ensure_venv() -> Path:
    directory = ROOT / ".venv"
    py = directory / "Scripts/python.exe"
    stamp = directory / ".requirements.stamp"
    wanted = _venv_stamp()
    current = stamp.read_text(encoding="utf-8") if stamp.is_file() else None
    if not py.is_file() or current != wanted:
        if directory.exists():
            shutil.rmtree(directory)
        run([str(python311()), "-m", "venv", str(directory)])
        pip = [str(py), "-m", "pip", "install", "--disable-pip-version-check"]
        run([*pip, *PYTHON_ENV_BOOTSTRAP["torch"], "--index-url", PYTORCH_CPU_INDEX])
        run([*pip, "-r", str(ROOT / "requirements.txt")])
        stamp.write_text(wanted, encoding="utf-8")
    return py


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
    with urllib.request.urlopen(url) as resp, open(tmp, "wb") as out:
        while True:
            block = resp.read(1024 * 1024)
            if not block:
                break
            out.write(block)
    if not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise OSError("empty download")
    tmp.replace(dest)


def pid_path(cfg: Variant) -> Path:
    return MODELS / f"{cfg.name}.pid"


def pipe_name(cfg: Variant) -> str:
    return rf"\\.\pipe\chatterbox-{cfg.name}"


def paths(cfg: Variant, args: LaunchArgs):
    t3 = MODELS / f"chatterbox-t3-{cfg.name}-precision1-{args.t3_weight_type}.gguf"
    s3 = MODELS / f"chatterbox-s3gen-{FAMILY[cfg.build_name]['s3_family']}-precision1-{args.s3_weight_type}.gguf"
    build = CHATTERBOX / "build" / cfg.build_name
    bin_dir = build / "bin"
    return t3, s3, pid_path(cfg), build, bin_dir / "chatterbox-server.exe", bin_dir / "chatterbox-bake.exe", pipe_name(cfg)


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
    record = json.loads(pid.read_text(encoding="ascii").strip())
    number = record["pid"]
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
          knobs: dict[str, str], tokenizer_py: Path | None, ckpt: Path | None, contract: dict):
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
    log_path = MODELS / f"{cfg.name}.server.log"
    log = open(log_path, "ab", buffering=0)
    try:
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
    write_json(pid, {"pid": proc.pid, **identity, "contract": contract})


def server_contract(cfg: Variant, exe: Path, t3: Path, s3: Path, language: str | None, knobs: dict[str, str],
                    tokenizer_py: Path | None, ckpt: Path | None) -> dict:
    contract = {
        "variant": cfg.name,
        "server": str(exe.resolve()),
        "t3": str(t3.resolve()),
        "s3": str(s3.resolve()),
        "language": language,
        "knobs": dict(knobs),
    }
    if cfg.needs_language:
        if tokenizer_py is None or ckpt is None:
            raise RuntimeError("missing tokenizer runtime")
        contract["tokenizer"] = {
            "python": str(tokenizer_py.resolve()),
            "adapter": str((CHATTERBOX / "scripts/mtl-tokenize-runtime.py").resolve()),
            "source": str((ckpt / "official_mtl_tokenizer.py").resolve()),
            "tts_source": str((ckpt / "official_mtl_tts.py").resolve()),
            "json": str((ckpt / "grapheme_mtl_merged_expanded_v1.json").resolve()),
            "cangjie": str((ckpt / "Cangjie5_TC.json").resolve()),
            "dicta": str((ckpt / "dicta-1.0.int8.onnx").resolve()),
        }
    return contract


def ensure_server(cfg: Variant, exe: Path, t3: Path, s3: Path, pipe: str, pid: Path, language: str | None,
                  knobs: dict[str, str], tokenizer_py: Path | None, ckpt: Path | None) -> dict:
    wanted = server_contract(cfg, exe, t3, s3, language, knobs, tokenizer_py, ckpt)
    prior = pid_record(pid)
    if prior and prior.get("contract") == wanted and running(pid):
        for _ in range(30):
            if pipe_ready(pipe, 1000):
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
            return pid_record(pid)
        time.sleep(0.25)
    raise RuntimeError("server pipe startup timeout")


def wav_duration_s(path: Path) -> float:
    with wave.open(str(path), "rb") as f:
        return f.getnframes() / f.getframerate()


def parse_synth_stats(line: bytes) -> dict:
    text = line.decode("ascii", errors="replace").strip()
    if text.startswith("err "):
        raise RuntimeError(text[4:])
    if text.startswith("ok "):
        text = text[2:].lstrip()
    out = {}
    for part in text.split():
        key, sep, raw = part.partition("=")
        if sep:
            out[key] = int(raw, 10)
    if any(key not in out for key in STATS_KEYS):
        raise RuntimeError("missing engine stats")
    return out


def synthesize_pipe(pipe: str, pid: Path, out: Path, text: str) -> dict:
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
    with open(pipe, "r+b", buffering=0) as f:
        message = memoryview(f"{out}\n{len(payload)}\n".encode("utf-8") + payload)
        while message:
            sent = f.write(message)
            if not sent:
                raise RuntimeError("pipe write incomplete")
            message = message[sent:]
        ack = f.readline(32769)
    if len(ack) > 32768 or not ack.endswith(b"\n"):
        raise RuntimeError("invalid or truncated acknowledgement")
    wall = time.perf_counter() - t0
    if not ack.startswith(b"ok "):
        if ack.startswith(b"err "):
            raise RuntimeError(ack[4:].decode("utf-8", errors="replace").strip())
        raise RuntimeError("synthesize")
    stats = parse_synth_stats(ack)
    stats["wall_s"] = wall
    return stats


def usage(cfg: Variant):
    runtime = " ".join(f"[--{n} <v>]" for n in runtime_names(cfg))
    weight_types = "|".join(WEIGHT_TYPES)
    controls = f"[--t3-weight-type {weight_types}] [--s3-weight-type {weight_types}] [--reference <wav>]"
    tail = "<text> <language>" if cfg.needs_language else "<text>"
    return f"usage: python tts.py {cfg.name} {runtime} {controls} {tail}"


def parse_args(cfg: Variant, argv: list[str]) -> LaunchArgs:
    args = argv[1:]
    runtime = dict(RUNTIME_DEFAULTS[cfg.build_name])
    conversion = dict(CONVERSION_DEFAULTS)
    reference = REF
    seen = set()
    i = 0
    allowed_runtime = set(runtime)
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help", "-?"):
            print(usage(cfg))
            raise SystemExit(0)
        if not a.startswith("--"):
            break
        if i + 1 >= len(args):
            raise SystemExit(usage(cfg))
        name, raw = a[2:], args[i + 1]
        if name in seen:
            raise SystemExit(f"duplicate flag: --{name}")
        seen.add(name)
        if name in allowed_runtime:
            runtime[name] = raw
        elif name == "t3-weight-type":
            if raw not in WEIGHT_TYPES:
                raise SystemExit(f"invalid t3-weight-type: {raw}")
            conversion[name] = raw
        elif name == "s3-weight-type":
            if raw not in WEIGHT_TYPES:
                raise SystemExit(f"invalid s3-weight-type: {raw}")
            conversion[name] = raw
        elif name == "reference":
            reference = Path(raw).expanduser().resolve()
        else:
            raise SystemExit(usage(cfg))
        i += 2
    rest = args[i:]
    if cfg.needs_language:
        if len(rest) != 2:
            raise SystemExit(usage(cfg))
        text, language = rest[0], rest[1]
    else:
        if len(rest) != 1:
            raise SystemExit(usage(cfg))
        text, language = rest[0], None
    return LaunchArgs(text, language, runtime, conversion["t3-weight-type"], conversion["s3-weight-type"], reference)


def cmake_definitions(cfg: Variant) -> dict[str, str]:
    if VULKAN is None:
        raise RuntimeError("Vulkan SDK was not resolved")
    return {
        **CMAKE_FLAGS,
        "TTS_FAMILY": cfg.build_name,
        "Vulkan_INCLUDE_DIR": str((VULKAN / "Include").resolve()),
        "Vulkan_LIBRARY": str((VULKAN / "Lib/vulkan-1.lib").resolve()),
        "Vulkan_GLSLC_EXECUTABLE": str((VULKAN / "Bin/glslc.exe").resolve()),
    }


def build_contract(cfg: Variant):
    return {
        "engine": ENGINE_PIN,
        "ggml": GGML_REV,
        "family": cfg.build_name,
        "generator": CMAKE_GENERATOR,
        "architecture": CMAKE_ARCH,
        "cmake_definitions": cmake_definitions(cfg),
    }


def ensure_ggml():
    ggml = CHATTERBOX / "ggml"
    if not (ggml / ".git").exists():
        run(["git", "clone", "--filter=blob:none", "https://github.com/ggml-org/ggml.git", str(ggml)])
    dirty = subprocess.run(["git", "-C", str(ggml), "status", "--porcelain"], check=True, capture_output=True, text=True).stdout.strip()
    if dirty:
        raise SystemExit("ggml checkout is dirty")
    actual = git_out(["rev-parse", "HEAD"], ggml)
    if actual != GGML_REV:
        run(["git", "-C", str(ggml), "fetch", "origin", GGML_REV, "--depth", "1"])
        run(["git", "-C", str(ggml), "checkout", "--detach", GGML_REV])
        actual = git_out(["rev-parse", "HEAD"], ggml)
    if actual != GGML_REV:
        raise SystemExit(f"ggml {actual} != required {GGML_REV}")


def prune_build(build: Path, bin_dir: Path):
    for child in list(build.iterdir()):
        if child.resolve() == bin_dir.resolve():
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    umbrella = bin_dir / "ggml.dll"
    if umbrella.is_file():
        umbrella.unlink()


def ensure_build(cfg: Variant, pid: Path, build: Path, exe: Path, bake: Path):
    ensure_ggml()
    stamp = MODELS / f"{cfg.build_name}.build-contract.json"
    wanted = build_contract(cfg)
    if read_json(stamp) == wanted and exe.is_file() and bake.is_file():
        prune_build(build, exe.parent)
        return
    kill(pid)
    for sibling in FAMILY[cfg.build_name]["siblings"]:
        if sibling != cfg.name:
            kill(MODELS / f"{sibling}.pid")
    if CMAKE is None:
        raise RuntimeError("cmake was not resolved")
    definitions = cmake_definitions(cfg)
    run([
        CMAKE, "-S", str(CHATTERBOX), "-B", str(build), "-G", CMAKE_GENERATOR, "-A", CMAKE_ARCH,
        *(f"-D{name}={value}" for name, value in definitions.items()),
    ])
    run([CMAKE, "--build", str(build), "--config", "Release", "--target", "chatterbox-server", "--parallel", "2"])
    run([CMAKE, "--build", str(build), "--config", "Release", "--target", "chatterbox-bake", "--parallel", "2"])
    if not exe.is_file() or not bake.is_file():
        raise RuntimeError("native build did not produce required executables")
    prune_build(build, exe.parent)
    write_json(stamp, wanted)


def ensure_assets(cfg: Variant) -> Path:
    ckpt = ROOT / FAMILY[cfg.build_name]["ckpt"]
    ckpt.mkdir(parents=True, exist_ok=True)
    for name in cfg.assets:
        dest = ckpt / name
        if not dest.is_file():
            download(f"{cfg.hf}/{name}", dest)
    for name, source in cfg.external_assets:
        dest = ckpt / name
        if not dest.is_file():
            download(source, dest)
    return ckpt


def conversion_contract(cfg: Variant, kind: str, weight_type: str):
    contract = {
        "engine": ENGINE_PIN,
        "kind": kind,
        "weight_type": weight_type,
    }
    if kind == "t3":
        contract["t3_ckpt"] = cfg.t3_ckpt
    return contract


def convert_t3(cfg: Variant, py: Path, ckpt: Path, t3: Path, contract, weight_type: str):
    tmp = t3.with_suffix(".gguf.converting")
    tmp.unlink(missing_ok=True)
    run([str(py), str(CHATTERBOX / "scripts" / FAMILY[cfg.build_name]["t3_script"]), str(ckpt), str(tmp), cfg.t3_ckpt, "--matrix-type", weight_type])
    tmp.replace(t3)
    write_json(MODELS / f"{t3.stem}.convert.json", contract)


def convert_s3(cfg: Variant, py: Path, ckpt: Path, s3: Path, contract, weight_type: str):
    tmp = s3.with_suffix(".gguf.converting")
    tmp.unlink(missing_ok=True)
    run([str(py), str(CHATTERBOX / "scripts" / FAMILY[cfg.build_name]["s3_script"]), str(ckpt), str(tmp), "--weight-type", weight_type])
    tmp.replace(s3)
    write_json(MODELS / f"{s3.stem}.convert.json", contract)


def ensure_converted(cfg: Variant, py: Path, ckpt: Path, t3: Path, s3: Path, args: LaunchArgs):
    t3_contract = conversion_contract(cfg, "t3", args.t3_weight_type)
    s3_contract = conversion_contract(cfg, "s3", args.s3_weight_type)
    if (not t3.is_file()) or read_json(MODELS / f"{t3.stem}.convert.json") != t3_contract:
        convert_t3(cfg, py, ckpt, t3, t3_contract, args.t3_weight_type)
    if (not s3.is_file()) or read_json(MODELS / f"{s3.stem}.convert.json") != s3_contract:
        convert_s3(cfg, py, ckpt, s3, s3_contract, args.s3_weight_type)
    return t3_contract, s3_contract


def ensure_baked(cfg: Variant, reference: Path, base_t3: Path, base_s3: Path, bake: Path, t3_contract: dict, s3_contract: dict):
    if not reference.is_file():
        raise FileNotFoundError(str(reference))
    wanted = {
        "engine": ENGINE_PIN,
        "variant": cfg.name,
        "t3_conversion": t3_contract,
        "s3_conversion": s3_contract,
        "reference": file_stamp(reference),
    }
    voice_dir = MODELS / "voices" / cfg.name
    t3 = voice_dir / "t3.gguf"
    s3 = voice_dir / "s3.gguf"
    stamp = voice_dir / "bake.json"
    if read_json(stamp) == wanted and t3.is_file() and s3.is_file():
        return t3, s3
    tmp = voice_dir.with_name(voice_dir.name + ".baking")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=False)
    tmp_t3, tmp_s3 = tmp / "t3.gguf", tmp / "s3.gguf"
    shutil.copy2(base_t3, tmp_t3)
    shutil.copy2(base_s3, tmp_s3)
    try:
        run([str(bake), str(tmp_t3), str(tmp_s3), str(reference)], cwd=str(ROOT))
        write_json(tmp / "bake.json", wanted)
        voice_dir.parent.mkdir(parents=True, exist_ok=True)
        if voice_dir.exists():
            shutil.rmtree(voice_dir)
        tmp.replace(voice_dir)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return t3, s3


def write_log(path: Path, body: str):
    path.write_text(body, encoding="utf-8")


def launch(cfg: Variant, argv: list[str]):
    args = parse_args(cfg, argv)
    run_id = datetime.now().strftime("%S-%M-%H-%d-%m-%y") + f"_{cfg.name}"
    wav = ROOT / f"{run_id}.wav"
    log = ROOT / f"{run_id}.log"
    if wav.exists() or log.exists():
        raise SystemExit(f"output collision: {run_id}")
    started = time.perf_counter()
    try:
        MODELS.mkdir(parents=True, exist_ok=True)
        if not args.reference.is_file():
            raise FileNotFoundError(str(args.reference))
        engine = ensure_engine()
        resolve_windows_tools()
        base_t3, base_s3, pid, build, exe, bake, pipe = paths(cfg, args)
        ensure_build(cfg, pid, build, exe, bake)
        py = ensure_venv()
        ckpt = ensure_assets(cfg)
        tokenizer_py = py if cfg.needs_language else None
        t3_contract, s3_contract = ensure_converted(cfg, py, ckpt, base_t3, base_s3, args)
        t3, s3 = ensure_baked(cfg, args.reference, base_t3, base_s3, bake, t3_contract, s3_contract)
        ensure_server(cfg, exe, t3, s3, pipe, pid, args.language, args.knobs, tokenizer_py, ckpt)
        stats = synthesize_pipe(pipe, pid, wav, args.text)
        duration = wav_duration_s(wav)
        write_log(log, "\n".join((
            f"status: ok",
            f"variant: {cfg.name}",
            f"engine: {engine}",
            f"language: {args.language or 'en'}",
            f"t3-weight-type: {args.t3_weight_type}",
            f"s3-weight-type: {args.s3_weight_type}",
            f"reference: {args.reference}",
            f"knobs: {json.dumps(args.knobs, sort_keys=True)}",
            f"text: {args.text}",
            f"wav: {wav}",
            f"duration_s: {duration:.3f}",
            f"wall_s: {stats['wall_s']:.3f}",
            f"stats: predicted={stats['predicted']} dropped={stats['dropped']} eos={stats['eos']} n_past={stats['n_past']} units={stats['units']} text_tokens={stats['text_tokens']} max_unit_predicted={stats['max_unit_predicted']}",
            f"host: {platform.platform()}",
            f"elapsed_s: {time.perf_counter() - started:.3f}",
        )) + "\n")
        print(wav, flush=True)
    except BaseException as exc:
        write_log(log, "\n".join((
            f"status: failed",
            f"variant: {cfg.name}",
            f"language: {args.language or 'en'}",
            f"t3-weight-type: {args.t3_weight_type}",
            f"s3-weight-type: {args.s3_weight_type}",
            f"error: {exc}",
        )) + "\n")
        wav.unlink(missing_ok=True)
        raise
