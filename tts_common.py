import ctypes
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
ENGINE_REV = "6eadc2ccd4b00a1a609976abf83beed30b2ba1df"
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
    split_tokens: int
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


def run(cmd, **kw):
    subprocess.run(cmd, check=True, **kw)


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
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


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


def kill(pid: Path):
    if not pid.is_file():
        return
    try:
        proc = int(pid.read_text(encoding="ascii").strip())
    except ValueError:
        pid.unlink(missing_ok=True)
        return
    h = K32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, proc)
    if h:
        K32.TerminateProcess(h, 1)
        K32.WaitForSingleObject(h, 15000)
        K32.CloseHandle(h)
    pid.unlink(missing_ok=True)


def running(pid: Path):
    if not pid.is_file():
        return False
    try:
        proc = int(pid.read_text(encoding="ascii").strip())
    except ValueError:
        return False
    h = K32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, proc)
    if not h:
        return False
    K32.CloseHandle(h)
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
    "split-tokens": "i",
    "min-p": "f",
    "cfg-weight": "f",
}
GPT2_KNOBS = (
    "repeat-penalty", "temperature", "top-k", "top-p", "seed",
    "n-predict", "split-tokens",
)
V3_KNOBS = (
    "repeat-penalty", "temperature", "top-p", "seed", "n-predict",
    "split-tokens", "min-p", "cfg-weight",
)


def spawn(cfg: Variant, exe: Path, t3: Path, s3: Path, pipe: str, pid: Path, language: str | None, knobs: dict[str, str]):
    args = [str(exe), str(t3), str(s3), pipe]
    if cfg.needs_language:
        if not language:
            raise SystemExit("language is required")
        args += ["--language", language]
    if "split-tokens" not in knobs:
        args += ["--split-tokens", str(cfg.split_tokens)]
    for name in cfg.knobs:
        val = knobs.get(name, "")
        if val:
            args += [f"--{name}", val]
    log_path = MODELS / f"{cfg.name}.server.log"
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
    pid.write_text(str(proc.pid), encoding="ascii")


def utterances(text: str) -> list[str]:
    parts = [p.strip() for p in text.split("|||")]
    out = [p for p in parts if p]
    if not out:
        raise SystemExit("empty text")
    return out


def wav_out_path(cfg: Variant, index: int, total: int) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}-{cfg.name}" if total == 1 else f"{stamp}-{cfg.name}-{index:02d}"
    out = ROOT / f"{base}.wav"
    n = 1
    while out.exists():
        out = ROOT / f"{base}-{n}.wav"
        n += 1
    return out


def wav_duration_s(path: Path) -> float:
    n = path.stat().st_size
    if n <= 44:
        raise RuntimeError("WAV length")
    return (n - 44) / 2.0 / 24000.0


def parse_synth_stats(line: bytes) -> dict:
    text = line.decode("ascii", errors="replace").strip()
    if text.startswith("err "):
        raise RuntimeError(text[4:])
    if text.startswith("ok"):
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
    if "|||" in body:
        raise RuntimeError("delimiter")
    payload = body.encode("utf-8")
    t0 = time.perf_counter()
    with open(pipe, "r+b", buffering=0) as f:
        f.write(f"{out}\n{len(payload)}\n".encode("utf-8") + payload)
        ack = f.readline()
    wall = time.perf_counter() - t0
    if not ack.startswith(b"ok"):
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
    if kind == "i":
        try:
            return str(int(str(raw).strip(), 10))
        except ValueError:
            raise SystemExit(f"{name} must be an int")
    try:
        value = float(raw)
    except ValueError:
        raise SystemExit(f"{name} must be a float")
    if kind == "f+" and value <= 0:
        raise SystemExit(f"{name} must be a positive float")
    return str(value)


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
        if not dest.is_file():
            download(f"{cfg.hf}/{name}", dest)
    return ckpt


CONVERT_DEPS = ("quant_policy.py",)
BAKE_SOURCES = (
    "src/bake.cpp", "src/bake_native.h", "src/main.cpp", "src/voice_features.cpp", "src/voice_features.h",
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
        "nbytes=0\n"
        "for t in r.tensors:\n"
        "    c[t.tensor_type.name]+=1\n"
        "    nbytes+=int(t.n_bytes)\n"
        "print(json.dumps({'n_tensors':len(r.tensors),'nbytes':nbytes,"
        "'tensor_types':dict(sorted(c.items()))}))\n"
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
    t3.unlink(missing_ok=True)
    run([str(py), str(CHATTERBOX / "scripts" / cfg.t3_script), str(ckpt), str(t3), cfg.t3_ckpt])
    write_json(MODELS / f"{t3.stem}.convert.json", contract)


def convert_s3(cfg: Variant, py: Path, ckpt: Path, s3: Path, contract):
    if s3.exists():
        raise SystemExit(f"S3 reconversion is forbidden: {s3}")
    run([str(py), str(CHATTERBOX / "scripts" / cfg.s3_script), str(ckpt), str(s3)])
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
    st = REF.stat()
    bake_contract = {
        "family": cfg.name,
        "bake_blob": [blob_rev(p) for p in BAKE_SOURCES],
        "reference": {"size": st.st_size, "mtime_ns": st.st_mtime_ns},
        "t3_conversion": t3_contract,
        "s3_conversion": s3_contract,
    }
    stamp = MODELS / f"{cfg.name}.bake-contract.json"
    if not converted and read_json(stamp) == bake_contract:
        return bake_contract
    kill(pid)
    run([str(bake), str(t3), str(s3), str(REF)], cwd=str(bin_dir))
    write_json(stamp, bake_contract)
    return bake_contract


def provenance(cfg: Variant, engine_rev: str, out: Path, original_text: str, piece: str, piece_index: int, piece_count: int, language: str, cli_knobs: dict[str, str], t3: Path, s3: Path, t3_types, s3_types, cmake: dict, metrics: dict):
    obj = {
        "engine": engine_rev,
        "ggml": GGML_REV,
        "family": cfg.name,
        "hf_base": cfg.hf,
        "t3_file": t3.name,
        "s3_file": s3.name,
        "t3_tensor_types": t3_types,
        "s3_tensor_types": s3_types,
        "cmake": cmake,
        "language": language or None,
        "knobs": metrics.get("knobs"),
        "knobs_cli": cli_knobs,
        "original_text": original_text,
        "piece_index": piece_index,
        "piece_count": piece_count,
        "piece_text": piece,
        "wav": out.name,
        "generated_local_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "wall_s": metrics.get("wall_s"),
        "duration_s": metrics.get("duration_s"),
        "rtf": metrics.get("rtf"),
        "predicted_count": metrics.get("predicted_count"),
        "dropped_count": metrics.get("dropped_count"),
        "eos": metrics.get("eos"),
        "n_past": metrics.get("n_past"),
        "units": metrics.get("units"),
        "text_tokens": metrics.get("text_tokens"),
        "max_unit_predicted": metrics.get("max_unit_predicted"),
        "server_log": f"{cfg.name}.server.log",
    }
    write_json(out.with_suffix(out.suffix + ".provenance.json"), obj)


def run_variant(cfg: Variant, args: LaunchArgs):
    if not REF.is_file():
        raise FileNotFoundError(str(REF))
    engine_rev = ensure_engine()
    MODELS.mkdir(parents=True, exist_ok=True)
    t3, s3, pid, build, bin_dir, exe, bake, pipe = paths(cfg)
    ensure_build(cfg, pid, build, exe, bake)
    py = ensure_converter_venv(cfg)
    ckpt = ensure_assets(cfg)
    t3_contract, s3_contract, converted, t3_types, s3_types = ensure_converted(cfg, engine_rev, py, ckpt, t3, s3)
    ensure_baked(cfg, engine_rev, py, ckpt, t3, s3, bake, bin_dir, pid, t3_contract, s3_contract, converted)
    if cfg.name == "nano":
        if not t3.is_file():
            raise SystemExit(f"missing replacement Nano T3: {t3}")
        (MODELS / "chatterbox-t3-nano-f16-mixed.gguf").unlink(missing_ok=True)
        (MODELS / "nano.t3-convert.json").unlink(missing_ok=True)
    cmake = build_contract(cfg)
    values = wanted_knobs(cfg, args.knobs)
    if cfg.needs_language and not args.language:
        raise SystemExit("language is required")
    lang = args.language.lower() if cfg.needs_language else ""
    print(
        f"engine={engine_rev}\nggml={GGML_REV}\nfamily={cfg.name}\n"
        f"t3={t3.name} {t3_types.get('tensor_types')}\n"
        f"s3={s3.name} {s3_types.get('tensor_types')}\n"
        f"knobs_cli={values or '(none; C++ header defaults)'}\n",
        file=sys.stderr,
        flush=True,
    )
    wait_pipe_absent(pipe)
    spawn(cfg, exe, t3, s3, pipe, pid, lang or None, values)
    text = args.text
    # Splitting into utterances is the engine's job (--split-tokens); the
    # client only honours explicit ||| segments as separate dest WAVs.
    pieces = utterances(text)
    n = len(pieces)

    def synth_piece(i: int, piece: str):
        out = wav_out_path(cfg, i, n)
        result = speak_batch(pipe, pid, out, piece)
        dur = wav_duration_s(out)
        rtf = result.wall_s / dur if dur > 0 else 0.0
        provenance(
            cfg, engine_rev, out, text, piece, i, n, lang, values, t3, s3, t3_types, s3_types, cmake,
            {
                "wall_s": result.wall_s,
                "duration_s": dur,
                "rtf": rtf,
                "predicted_count": result.predicted_count,
                "dropped_count": result.dropped_count,
                "eos": result.eos,
                "n_past": result.n_past,
                "units": result.units,
                "text_tokens": result.text_tokens,
                "max_unit_predicted": result.max_unit_predicted,
                "knobs": result.knobs,
            },
        )
        print(
            f"wall_s={result.wall_s:.3f} duration_s={dur:.3f} rtf={rtf:.3f} "
            f"predicted={result.predicted_count} eos={result.eos} n_past={result.n_past} "
            f"units={result.units} text_tokens={result.text_tokens} max_unit_predicted={result.max_unit_predicted}",
            file=sys.stderr, flush=True,
        )
        if result.knobs:
            print("knobs " + " ".join(f"{k}={v}" for k, v in result.knobs.items()), file=sys.stderr, flush=True)
        print(out, flush=True)
        return out, result.wall_s, dur

    wav_paths = []
    ready = synth_piece(0, pieces[0])
    for i, piece in enumerate(pieces):
        out, wall, dur = ready
        wav_paths.append(out)
        if i + 1 < n:
            ready = synth_piece(i + 1, pieces[i + 1])
    if n > 1:
        manifest = ROOT / f"{time.strftime('%Y%m%d-%H%M%S')}-{cfg.name}-manifest.json"
        write_json(manifest, {"family": cfg.name, "files": [str(p) for p in wav_paths]})
        print("manifest " + str(manifest), flush=True)
