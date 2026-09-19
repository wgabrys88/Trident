import argparse
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
ENGINE_PIN = "9e90f4f3db71cd06fdb864c0ca29443592bec14c"
DETACH = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
K32 = ctypes.WinDLL("kernel32", use_last_error=True)
K32.WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
K32.WaitNamedPipeW.restype = ctypes.c_int
K32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
K32.OpenProcess.restype = ctypes.c_void_p
K32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
K32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
K32.CloseHandle.argtypes = [ctypes.c_void_p]


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


def read_json(path: Path):
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def file_stamp(path: Path) -> dict:
    stat = path.stat()
    return {"path": str(path.resolve()), "bytes": stat.st_size, "mtime": stat.st_mtime_ns}


def ensure_venv() -> Path:
    directory = ROOT / ".venv"
    py = directory / "Scripts/python.exe"
    stamp = directory / ".requirements.stamp"
    wanted = (ROOT / "requirements.txt").read_text(encoding="utf-8") + json.dumps({
        "torch": list(PYTHON_ENV_BOOTSTRAP["torch"]), "torch_index": PYTORCH_CPU_INDEX,
    }, sort_keys=True, separators=(",", ":"))
    current = stamp.read_text(encoding="utf-8") if stamp.is_file() else None
    if not py.is_file() or current != wanted:
        if directory.exists():
            shutil.rmtree(directory)
        run([sys.executable, "-m", "venv", str(directory)])
        pip = [str(py), "-m", "pip", "install", "--disable-pip-version-check"]
        run([*pip, *PYTHON_ENV_BOOTSTRAP["torch"], "--index-url", PYTORCH_CPU_INDEX])
        run([*pip, "-r", str(ROOT / "requirements.txt")])
        stamp.write_text(wanted, encoding="utf-8")
    return py


def download(url: str, dest: Path):
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url) as resp, open(tmp, "wb") as out:
        shutil.copyfileobj(resp, out)


    tmp.replace(dest)


def paths(cfg: Variant, args: LaunchArgs):
    t3 = MODELS / f"chatterbox-t3-{cfg.name}-precision1-{args.t3_weight_type}.gguf"
    s3 = MODELS / f"chatterbox-s3gen-{FAMILY[cfg.build_name]['s3_family']}-precision1-{args.s3_weight_type}.gguf"
    build = CHATTERBOX / "build" / cfg.build_name
    bin_dir = build / "bin"
    return t3, s3, MODELS / f"{cfg.name}.pid", build, bin_dir / "chatterbox-server.exe", bin_dir / "chatterbox-bake.exe", rf"\\.\pipe\chatterbox-{cfg.name}"


def kill(pid: Path):
    record = read_json(pid)
    if record:
        handle = K32.OpenProcess(0x00100001, False, record["pid"])
        if handle:
            K32.TerminateProcess(handle, 1)
            K32.WaitForSingleObject(handle, 15000)
            K32.CloseHandle(handle)
        pid.unlink()


def ensure_server(cfg, exe, t3, s3, pipe, pid, language, knobs, py, ckpt, voice):
    flags = dict(knobs)
    if cfg.needs_language:
        flags.update({
            "language": language,
            "tokenizer-python": str(py),
            "tokenizer-script": str(CHATTERBOX / "scripts/mtl-tokenize-runtime.py"),
            "tokenizer-source": str(ckpt / "official_mtl_tokenizer.py"),
            "tokenizer-tts-source": str(ckpt / "official_mtl_tts.py"),
            "tokenizer-json": str(ckpt / "grapheme_mtl_merged_expanded_v1.json"),
            "cangjie-json": str(ckpt / "Cangjie5_TC.json"),
            "dicta-model": str(ckpt / "dicta-1.0.int8.onnx"),
        })
    command = [str(exe), str(t3), str(s3), pipe]
    command += [value for name, raw in flags.items() for value in (f"--{name}", raw)]
    wanted = {"command": command, "voice": voice}
    prior = read_json(pid)
    if prior and prior["contract"] == wanted:
        if not K32.WaitNamedPipeW(pipe, 30000):
            raise ctypes.WinError(ctypes.get_last_error())
        return
    kill(pid)
    with open(MODELS / f"{cfg.name}.server.log", "ab", buffering=0) as log:
        proc = subprocess.Popen(command, cwd=exe.parent, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=log, creationflags=DETACH)
    write_json(pid, {"pid": proc.pid, "contract": wanted})
    deadline = time.monotonic() + 30
    while not K32.WaitNamedPipeW(pipe, 1000):
        if proc.poll() is not None:
            raise RuntimeError(f"server exited: {proc.returncode}")
        if time.monotonic() >= deadline:
            raise TimeoutError("server startup")
        time.sleep(0.05)


def wav_duration_s(path: Path) -> float:
    with wave.open(str(path), "rb") as f:
        return f.getnframes() / f.getframerate()


def synthesize_pipe(pipe: str, out: Path, text: str) -> dict:
    payload = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    t0 = time.perf_counter()
    with open(pipe, "r+b", buffering=0) as f:
        message = memoryview(f"{out}\n{len(payload)}\n".encode("utf-8") + payload)
        while message:
            sent = f.write(message)
            if not sent:
                raise BrokenPipeError(pipe)
            message = message[sent:]
        ack = f.readline().decode("utf-8").strip()
    status, body = ack.split(" ", 1)
    if status != "ok":
        raise RuntimeError(body)
    stats = {key: int(value) for key, value in (part.split("=") for part in body.split())}
    stats["wall_s"] = time.perf_counter() - t0
    return stats


def parse_args(cfg: Variant, argv: list[str]) -> LaunchArgs:
    parser = argparse.ArgumentParser(prog=f"python tts.py {cfg.name}")
    parser.add_argument("-?", action="help")
    for name, value in RUNTIME_DEFAULTS[cfg.build_name].items():
        parser.add_argument(f"--{name}", default=value)
    for name, value in CONVERSION_DEFAULTS.items():
        parser.add_argument(f"--{name}", default=value, choices=WEIGHT_TYPES)
    parser.add_argument("--reference", type=lambda value: Path(value).expanduser().resolve(), default=REF)
    parser.add_argument("text")
    if cfg.needs_language:
        parser.add_argument("language")
    values = vars(parser.parse_args(argv[1:]))
    return LaunchArgs(values["text"], values.get("language"),
                      {name: values[name.replace("-", "_")] for name in RUNTIME_DEFAULTS[cfg.build_name]},
                      values["t3_weight_type"], values["s3_weight_type"], values["reference"])


def ensure_ggml():
    ggml = CHATTERBOX / "ggml"
    if not (ggml / ".git").exists():
        run(["git", "clone", "--filter=blob:none", "https://github.com/ggml-org/ggml.git", str(ggml)])
    actual = git_out(["rev-parse", "HEAD"], ggml)
    if actual != GGML_REV:
        run(["git", "-C", str(ggml), "fetch", "origin", GGML_REV, "--depth", "1"])
        run(["git", "-C", str(ggml), "checkout", "--detach", GGML_REV])


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
    stamp = MODELS / f"{cfg.build_name}.build-contract.json"
    wanted = {"engine": ENGINE_PIN, "ggml": GGML_REV, "family": cfg.build_name,
              "generator": CMAKE_GENERATOR, "architecture": CMAKE_ARCH, "flags": CMAKE_FLAGS}
    if read_json(stamp) == wanted and exe.is_file() and bake.is_file():
        return
    ensure_ggml()
    for sibling in FAMILY[cfg.build_name]["siblings"]:
        kill(MODELS / f"{sibling}.pid")
    vulkan = max(Path("C:/VulkanSDK").glob("*/Bin/glslc.exe"),
                 key=lambda path: tuple(map(int, re.findall(r"\d+", path.parts[-3])))).parents[1]
    definitions = {**CMAKE_FLAGS, "TTS_FAMILY": cfg.build_name,
                   "Vulkan_INCLUDE_DIR": str(vulkan / "Include"),
                   "Vulkan_LIBRARY": str(vulkan / "Lib/vulkan-1.lib"),
                   "Vulkan_GLSLC_EXECUTABLE": str(vulkan / "Bin/glslc.exe")}
    run(["cmake", "-S", str(CHATTERBOX), "-B", str(build), "-G", CMAKE_GENERATOR, "-A", CMAKE_ARCH,
         *(f"-D{name}={value}" for name, value in definitions.items())])
    run(["cmake", "--build", str(build), "--config", "Release", "--target",
         "chatterbox-server", "chatterbox-bake", "--parallel", "2"])
    prune_build(build, exe.parent)
    write_json(stamp, wanted)


def ensure_assets(cfg: Variant) -> Path:
    ckpt = ROOT / FAMILY[cfg.build_name]["ckpt"]
    ckpt.mkdir(parents=True, exist_ok=True)
    sources = [(name, f"{cfg.hf}/{name}") for name in cfg.assets] + list(cfg.external_assets)
    for name, source in sources:
        dest = ckpt / name
        if not dest.is_file():
            download(source, dest)
    return ckpt


def ensure_converted(cfg, py, ckpt, t3, s3, args):
    contracts = []
    for kind, path, weight in (("t3", t3, args.t3_weight_type), ("s3", s3, args.s3_weight_type)):
        contract = {"engine": ENGINE_PIN, "kind": kind, "weight_type": weight}
        options = ["--weight-type", weight]
        if kind == "t3":
            contract["t3_ckpt"] = cfg.t3_ckpt
            options = [cfg.t3_ckpt, "--matrix-type", weight]
        stamp = MODELS / f"{path.stem}.convert.json"
        if not path.is_file() or read_json(stamp) != contract:
            tmp = path.with_suffix(".gguf.converting")
            run([str(py), str(CHATTERBOX / "scripts" / FAMILY[cfg.build_name][f"{kind}_script"]),
                 str(ckpt), str(tmp), *options])
            tmp.replace(path)
            write_json(stamp, contract)
        contracts.append(contract)
    return contracts


def ensure_baked(cfg: Variant, reference: Path, base_t3: Path, base_s3: Path, bake: Path, t3_contract: dict, s3_contract: dict):
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
        return t3, s3, wanted
    kill(MODELS / f"{cfg.name}.pid")
    tmp = voice_dir.with_name(voice_dir.name + ".baking")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=False)
    tmp_t3, tmp_s3 = tmp / "t3.gguf", tmp / "s3.gguf"
    shutil.copy2(base_t3, tmp_t3)
    shutil.copy2(base_s3, tmp_s3)
    run([str(bake), str(tmp_t3), str(tmp_s3), str(reference)], cwd=str(ROOT))
    write_json(tmp / "bake.json", wanted)
    voice_dir.parent.mkdir(parents=True, exist_ok=True)
    if voice_dir.exists():
        shutil.rmtree(voice_dir)
    tmp.replace(voice_dir)
    return t3, s3, wanted


def launch(cfg: Variant, argv: list[str]):
    args = parse_args(cfg, argv)
    run_id = datetime.now().strftime("%S-%M-%H-%d-%m-%y") + f"_{cfg.name}"
    wav = ROOT / f"{run_id}.wav"
    log = ROOT / f"{run_id}.log"
    started = time.perf_counter()
    fields = {"status": "failed", "variant": cfg.name, "language": args.language or "en",
              "t3-weight-type": args.t3_weight_type, "s3-weight-type": args.s3_weight_type}
    try:
        MODELS.mkdir(parents=True, exist_ok=True)
        engine = git_out(["rev-parse", "HEAD"])
        base_t3, base_s3, pid, build, exe, bake, pipe = paths(cfg, args)
        ensure_build(cfg, pid, build, exe, bake)
        py = ensure_venv()
        ckpt = ensure_assets(cfg)
        tokenizer_py = py if cfg.needs_language else None
        t3_contract, s3_contract = ensure_converted(cfg, py, ckpt, base_t3, base_s3, args)
        t3, s3, voice = ensure_baked(cfg, args.reference, base_t3, base_s3, bake, t3_contract, s3_contract)
        ensure_server(cfg, exe, t3, s3, pipe, pid, args.language, args.knobs, tokenizer_py, ckpt, voice)
        stats = synthesize_pipe(pipe, wav, args.text)
        duration = wav_duration_s(wav)
        fields.update({
            "status": "ok", "engine": engine, "reference": args.reference,
            "knobs": json.dumps(args.knobs, sort_keys=True), "text": args.text, "wav": wav,
            "duration_s": f"{duration:.3f}", "wall_s": f"{stats['wall_s']:.3f}",
            "stats": " ".join(f"{key}={stats[key]}" for key in STATS_KEYS),
            "host": platform.platform(), "elapsed_s": f"{time.perf_counter() - started:.3f}",
        })
        print(wav, flush=True)
    except BaseException as exc:
        fields["error"] = str(exc)
        wav.unlink(missing_ok=True)
        raise
    finally:
        log.write_text("".join(f"{key}: {value}\n" for key, value in fields.items()), encoding="utf-8")
