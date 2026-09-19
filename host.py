import argparse
import ctypes
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from settings import (
    ARCHITECTURES, CMAKE_ARCH, CMAKE_FLAGS, CMAKE_GENERATOR, CONVERSION_DEFAULTS,
    PYTHON_ENV_BOOTSTRAP, PYTORCH_CPU_INDEX, RUNTIME_DEFAULTS, WEIGHT_TYPES,
)

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
REF = ROOT / "reference.wav"
DETACH = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
K32 = ctypes.WinDLL("kernel32", use_last_error=True)
K32.WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
K32.WaitNamedPipeW.restype = ctypes.c_int
K32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
K32.OpenProcess.restype = ctypes.c_void_p
K32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
K32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
K32.CloseHandle.argtypes = [ctypes.c_void_p]


@dataclass(frozen=True)
class Variant:
    name: str
    hf: str
    assets: tuple[str, ...]
    t3_ckpt: str
    architecture: str
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


class Contract:
    def __init__(self, payload: dict, *outputs: Path):
        self.payload = payload
        self.outputs = outputs

    def matches(self, path: Path) -> bool:
        return (path.is_file()
                and json.loads(path.read_text(encoding="utf-8")) == self.payload
                and all(item.is_file() for item in self.outputs))

    def write(self, path: Path) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.payload, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)


def run(cmd, **kw):
    subprocess.run(cmd, check=True, **kw)


def git_out(args, repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def file_stamp(path: Path) -> dict:
    stat = path.stat()
    return {"path": str(path.resolve()), "bytes": stat.st_size, "mtime": stat.st_mtime_ns}


def download(url: str, dest: Path):
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url) as resp, open(tmp, "wb") as out:
        shutil.copyfileobj(resp, out)
    tmp.replace(dest)


def kill(pid: Path):
    if not pid.is_file():
        return
    record = json.loads(pid.read_text(encoding="utf-8"))
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(record["pid"])],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    handle = K32.OpenProcess(0x00100001, False, record["pid"])
    if handle:
        K32.TerminateProcess(handle, 1)
        if K32.WaitForSingleObject(handle, 0xFFFFFFFF) != 0:
            K32.CloseHandle(handle)
            raise RuntimeError(f"server pid {record['pid']} did not exit")
        K32.CloseHandle(handle)
    pid.unlink()


class Venv:
    def ensure(self) -> Path:
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


class GgmlPin:
    REV = "7840aaba1989c6deeefede1d77d5aaf8f52b947e"

    def ensure(self):
        ggml = ROOT / "ggml"
        if not (ggml / ".git").exists():
            run(["git", "clone", "--filter=blob:none", "https://github.com/ggml-org/ggml.git", str(ggml)])
        if git_out(["rev-parse", "HEAD"], ggml) != self.REV:
            run(["git", "-C", str(ggml), "fetch", "origin", self.REV, "--depth", "1"])
            run(["git", "-C", str(ggml), "checkout", "--detach", self.REV])


class EngineBuild:
    TARGETS = ("chatterbox-server-gpt2", "chatterbox-bake-gpt2",
               "chatterbox-server-llama", "chatterbox-bake-llama")

    def ensure(self, architecture: str) -> tuple[Path, Path]:
        bin_dir = ROOT / "build" / "bin"
        family = ARCHITECTURES[architecture]
        server, bake = bin_dir / family["server"], bin_dir / family["bake"]
        outputs = tuple(bin_dir / name for spec in ARCHITECTURES.values() for name in (spec["server"], spec["bake"]))
        wanted = {"ggml": GgmlPin.REV, "generator": CMAKE_GENERATOR, "architecture": CMAKE_ARCH, "flags": CMAKE_FLAGS}
        stamp = MODELS / "build-contract.json"
        if Contract(wanted, *outputs).matches(stamp):
            return server, bake
        GgmlPin().ensure()
        for spec in ARCHITECTURES.values():
            for sibling in spec["siblings"]:
                kill(MODELS / f"{sibling}.pid")
        vulkan = max(Path("C:/VulkanSDK").glob("*/Bin/glslc.exe"),
                     key=lambda path: tuple(map(int, re.findall(r"\d+", path.parts[-3])))).parents[1]
        definitions = {**CMAKE_FLAGS,
                       "Vulkan_INCLUDE_DIR": str(vulkan / "Include"),
                       "Vulkan_LIBRARY": str(vulkan / "Lib/vulkan-1.lib"),
                       "Vulkan_GLSLC_EXECUTABLE": str(vulkan / "Bin/glslc.exe")}
        build = ROOT / "build"
        run(["cmake", "-S", str(ROOT), "-B", str(build), "-G", CMAKE_GENERATOR, "-A", CMAKE_ARCH,
             *(f"-D{name}={value}" for name, value in definitions.items())])
        run(["cmake", "--build", str(build), "--config", "Release", "--target", *self.TARGETS, "--parallel", "2"])
        for child in list(build.iterdir()):
            if child.resolve() != bin_dir.resolve():
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        umbrella = bin_dir / "ggml.dll"
        if umbrella.is_file():
            umbrella.unlink()
        Contract(wanted, *outputs).write(stamp)
        return server, bake


class Checkpoints:
    def ensure(self, cfg: Variant) -> Path:
        ckpt = ROOT / ARCHITECTURES[cfg.architecture]["ckpt"]
        ckpt.mkdir(parents=True, exist_ok=True)
        for name, source in [(asset, f"{cfg.hf}/{asset}") for asset in cfg.assets] + list(cfg.external_assets):
            dest = ckpt / name
            if not dest.is_file():
                download(source, dest)
        return ckpt


class Converter:
    def ensure(self, cfg: Variant, py: Path, ckpt: Path, t3: Path, s3: Path, args: LaunchArgs) -> tuple[dict, dict]:
        family = ARCHITECTURES[cfg.architecture]
        scripts = ROOT / "scripts"
        t3_payload = {"kind": "t3", "weight_type": args.t3_weight_type, "t3_ckpt": cfg.t3_ckpt}
        s3_payload = {"kind": "s3", "weight_type": args.s3_weight_type, "checkpoint": family["s3_checkpoint"]}
        t3_stamp, s3_stamp = MODELS / f"{t3.stem}.convert.json", MODELS / f"{s3.stem}.convert.json"
        t3_contract, s3_contract = Contract(t3_payload, t3), Contract(s3_payload, s3)
        if not t3_contract.matches(t3_stamp):
            tmp = t3.with_suffix(".gguf.converting")
            run([str(py), str(scripts / family["t3_script"]), str(ckpt), str(tmp), cfg.t3_ckpt, "--matrix-type", args.t3_weight_type])
            tmp.replace(t3)
            t3_contract.write(t3_stamp)
        if not s3_contract.matches(s3_stamp):
            tmp = s3.with_suffix(".gguf.converting")
            run([str(py), str(scripts / "convert_s3.py"), str(ckpt), str(tmp),
                 "--checkpoint", family["s3_checkpoint"], "--weight-type", args.s3_weight_type])
            tmp.replace(s3)
            s3_contract.write(s3_stamp)
        return t3_payload, s3_payload


class VoiceBake:
    def ensure(self, cfg: Variant, reference: Path, base_t3: Path, base_s3: Path, bake: Path,
               t3_contract: dict, s3_contract: dict) -> tuple[Path, Path, dict]:
        wanted = {"variant": cfg.name, "t3_conversion": t3_contract, "s3_conversion": s3_contract,
                  "reference": file_stamp(reference)}
        voice_dir = MODELS / "voices" / cfg.name
        t3, s3 = voice_dir / "t3.gguf", voice_dir / "s3.gguf"
        stamp = voice_dir / "bake.json"
        if Contract(wanted, t3, s3).matches(stamp):
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
        Contract(wanted, tmp_t3, tmp_s3).write(tmp / "bake.json")
        voice_dir.parent.mkdir(parents=True, exist_ok=True)
        if voice_dir.exists():
            shutil.rmtree(voice_dir)
        tmp.replace(voice_dir)
        return t3, s3, wanted


class PipeServer:
    def ensure(self, cfg: Variant, exe: Path, t3: Path, s3: Path, language: str | None,
               knobs: dict[str, str], py: Path, ckpt: Path, voice: dict) -> str:
        pipe = rf"\\.\pipe\chatterbox-{cfg.name}"
        pid = MODELS / f"{cfg.name}.pid"
        flags = dict(knobs)
        if cfg.needs_language:
            flags.update({
                "language": language,
                "tokenizer-python": str(py),
                "tokenizer-script": str(ROOT / "scripts/mtl_tokenize_runtime.py"),
                "tokenizer-source": str(ckpt / "official_mtl_tokenizer.py"),
                "tokenizer-tts-source": str(ckpt / "official_mtl_tts.py"),
                "tokenizer-json": str(ckpt / "grapheme_mtl_merged_expanded_v1.json"),
                "cangjie-json": str(ckpt / "Cangjie5_TC.json"),
                "dicta-model": str(ckpt / "dicta-1.0.int8.onnx"),
            })
        command = [str(exe), str(t3), str(s3), pipe]
        command += [value for name, raw in flags.items() for value in (f"--{name}", raw)]
        wanted = {"command": command, "voice": voice}
        if pid.is_file() and json.loads(pid.read_text(encoding="utf-8"))["contract"] == wanted:
            if K32.WaitNamedPipeW(pipe, 30000):
                return pipe
        kill(pid)
        proc = subprocess.Popen(command, cwd=exe.parent, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=DETACH)
        Contract({"pid": proc.pid, "contract": wanted}).write(pid)
        deadline = time.monotonic() + 30
        while not K32.WaitNamedPipeW(pipe, 1000):
            if proc.poll() is not None:
                raise RuntimeError(f"server exited: {proc.returncode}")
            if time.monotonic() >= deadline:
                raise TimeoutError("server startup")
            time.sleep(0.05)
        return pipe

    def synthesize(self, pipe: str, out: Path, text: str):
        payload = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        with open(pipe, "r+b", buffering=0) as stream:
            message = memoryview(f"{out}\n{len(payload)}\n".encode("utf-8") + payload)
            while message:
                sent = stream.write(message)
                if not sent:
                    raise BrokenPipeError(pipe)
                message = message[sent:]
            ack = stream.readline().decode("utf-8").strip()
        status, body = ack.split(" ", 1)
        if status != "ok":
            raise RuntimeError(body)


class Host:
    def parse(self, cfg: Variant, argv: list[str]) -> LaunchArgs:
        parser = argparse.ArgumentParser(prog=f"python tts.py {cfg.name}")
        parser.add_argument("-?", action="help")
        for name, value in RUNTIME_DEFAULTS[cfg.architecture].items():
            parser.add_argument(f"--{name}", default=value)
        for name, value in CONVERSION_DEFAULTS.items():
            parser.add_argument(f"--{name}", default=value, choices=WEIGHT_TYPES)
        parser.add_argument("--reference", type=lambda value: Path(value).expanduser().resolve(), default=REF)
        parser.add_argument("text")
        if cfg.needs_language:
            parser.add_argument("language")
        values = vars(parser.parse_args(argv[1:]))
        return LaunchArgs(values["text"], values.get("language"),
                          {name: values[name.replace("-", "_")] for name in RUNTIME_DEFAULTS[cfg.architecture]},
                          values["t3_weight_type"], values["s3_weight_type"], values["reference"])

    def run(self, variant: Variant, argv: list[str]) -> Path:
        args = self.parse(variant, argv)
        wav = ROOT / f"{datetime.now().strftime('%S-%M-%H-%d-%m-%y')}_{variant.name}.wav"
        family = ARCHITECTURES[variant.architecture]
        try:
            MODELS.mkdir(parents=True, exist_ok=True)
            server, bake = EngineBuild().ensure(variant.architecture)
            py = Venv().ensure()
            ckpt = Checkpoints().ensure(variant)
            base_t3 = MODELS / f"chatterbox-t3-{variant.name}-precision1-{args.t3_weight_type}.gguf"
            base_s3 = MODELS / f"chatterbox-s3gen-{family['s3_family']}-precision1-{args.s3_weight_type}.gguf"
            t3_contract, s3_contract = Converter().ensure(variant, py, ckpt, base_t3, base_s3, args)
            t3, s3, voice = VoiceBake().ensure(variant, args.reference, base_t3, base_s3, bake, t3_contract, s3_contract)
            pipes = PipeServer()
            pipe = pipes.ensure(variant, server, t3, s3, args.language, args.knobs, py, ckpt, voice)
            pipes.synthesize(pipe, wav, args.text)
            return wav
        except BaseException:
            wav.unlink(missing_ok=True)
            raise
