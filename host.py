import argparse
import ctypes
import json
import hashlib
import os
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

from settings import ARCHITECTURES, CMAKE_ARCH, CMAKE_GENERATOR, FLAGS, PYTHON_ENV_BOOTSTRAP, PYTORCH_CPU_INDEX, Variant

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"


def write_wav(pcm: bytes, stem: str) -> Path:
    wav = ROOT / f"{datetime.now().strftime('%S-%M-%H-%d-%m-%y')}_{stem}.wav"
    with wave.open(str(wav), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(24000)
        out.writeframes(pcm)
    return wav


DETACH = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
K32 = ctypes.WinDLL("kernel32", use_last_error=True)
K32.WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
K32.WaitNamedPipeW.restype = ctypes.c_int
K32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
K32.OpenProcess.restype = ctypes.c_void_p
K32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
K32.CloseHandle.argtypes = [ctypes.c_void_p]


@dataclass
class LaunchArgs:
    text: str
    language: str | None
    knobs: dict[str, str]
    t3_weight_type: str
    s3_weight_type: str
    reference: Path
    t3_quant_policy: Path
    s3_quant_policy: Path
    listen: bool
    session: dict[str, str]

    def policy(self, kind):
        return {"default": getattr(self, kind + "_weight_type"),
                "rules": json.loads(getattr(self, kind + "_quant_policy").read_text())["rules"]}

    def gguf(self, kind, family):
        policy = self.policy("s3" if kind == "s3gen" else kind)
        digest = hashlib.sha256(json.dumps(policy["rules"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:8]
        return MODELS / f"chatterbox-{kind}-{family}-{policy['default']}-{digest}.gguf"


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


def digest(*paths) -> str:
    hasher = hashlib.sha256()
    files = []
    for item in paths:
        item = Path(item)
        if item.is_file():
            files.append(item)
        else:
            files.extend(path for path in item.rglob("*") if path.is_file())
    for path in sorted(files, key=lambda p: p.relative_to(ROOT).as_posix()):
        hasher.update(path.relative_to(ROOT).as_posix().encode())
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def download(url: str, dest: Path):
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url) as resp, open(tmp, "wb") as out:
        shutil.copyfileobj(resp, out)
    tmp.replace(dest)


def alive(pid: int) -> bool:
    handle = K32.OpenProcess(0x00100000, False, pid)
    if not handle:
        return False
    status = K32.WaitForSingleObject(handle, 0)
    K32.CloseHandle(handle)
    if status == 0xFFFFFFFF:
        raise ctypes.WinError(ctypes.get_last_error())
    return status == 258


def kill(pid: Path):
    if not pid.is_file():
        return
    record = json.loads(pid.read_text(encoding="utf-8"))
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(record["pid"])],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    handle = K32.OpenProcess(0x00100000, False, record["pid"])
    if handle:
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
            "llama_cpp": "vulkan",
        }, sort_keys=True, separators=(",", ":"))
        current = stamp.read_text(encoding="utf-8") if stamp.is_file() else None
        if not py.is_file() or current != wanted:
            if directory.exists():
                shutil.rmtree(directory)
            run([sys.executable, "-m", "venv", str(directory)])
            pip = [str(py), "-m", "pip", "install", "--disable-pip-version-check"]
            run([*pip, *PYTHON_ENV_BOOTSTRAP["torch"], "--index-url", PYTORCH_CPU_INDEX])
            env = os.environ.copy()
            env["CMAKE_ARGS"] = "-DGGML_VULKAN=ON"
            env["FORCE_CMAKE"] = "1"
            run([*pip, "-r", str(ROOT / "requirements.txt"), "--no-binary", "llama-cpp-python"], env=env)
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
    TARGETS = ("chatterbox-server", "chatterbox-bake")

    def wanted(self) -> dict:
        return {"ggml": GgmlPin.REV, "generator": CMAKE_GENERATOR, "architecture": CMAKE_ARCH,
                "source": digest(ROOT / "CMakeLists.txt", ROOT / "src")}

    def ensure(self) -> tuple[Path, Path]:
        bin_dir = ROOT / "build" / "bin"
        outputs = tuple(bin_dir / f"{name}.exe" for name in self.TARGETS)
        wanted = self.wanted()
        stamp = MODELS / "build-contract.json"
        if Contract(wanted, *outputs).matches(stamp):
            return outputs
        GgmlPin().ensure()
        kill(MODELS / "server.pid")
        vulkan = max(Path("C:/VulkanSDK").glob("*/Bin/glslc.exe"),
                     key=lambda path: tuple(map(int, re.findall(r"\d+", path.parts[-3])))).parents[1]
        definitions = {
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
        return outputs


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
        t3_payload = {"kind": "t3", "policy": args.policy("t3"), "t3_ckpt": cfg.t3_ckpt,
                      "converter": digest(scripts / "convert_t3.py", scripts / "quant.py")}
        s3_payload = {"kind": "s3", "policy": args.policy("s3"), "checkpoint": family["s3_checkpoint"],
                      "converter": digest(scripts / "convert_s3.py", scripts / "quant.py")}
        t3_stamp, s3_stamp = MODELS / f"{t3.stem}.convert.json", MODELS / f"{s3.stem}.convert.json"
        t3_contract, s3_contract = Contract(t3_payload, t3), Contract(s3_payload, s3)
        if not t3_contract.matches(t3_stamp):
            tmp = t3.with_suffix(".gguf.converting")
            run([str(py), str(scripts / "convert_t3.py"), str(ckpt), str(tmp), cfg.t3_ckpt,
                 "--matrix-type", args.t3_weight_type, "--quant-policy", str(args.t3_quant_policy),
                 "--s3-checkpoint", family["s3_checkpoint"]])
            tmp.replace(t3)
            t3_contract.write(t3_stamp)
        if not s3_contract.matches(s3_stamp):
            tmp = s3.with_suffix(".gguf.converting")
            run([str(py), str(scripts / "convert_s3.py"), str(ckpt), str(tmp),
                 "--checkpoint", family["s3_checkpoint"], "--weight-type", args.s3_weight_type, "--quant-policy", str(args.s3_quant_policy)])
            tmp.replace(s3)
            s3_contract.write(s3_stamp)
        return t3_payload, s3_payload


class VoiceBake:
    def ensure(self, cfg: Variant, reference: Path, base_t3: Path, base_s3: Path, bake: Path,
               t3_contract: dict, s3_contract: dict, build: dict) -> tuple[Path, Path, dict]:
        wanted = {"variant": cfg.name, "t3_conversion": t3_contract, "s3_conversion": s3_contract,
                  "reference": file_stamp(reference), "build": build}
        voice_dir = MODELS / "voices" / cfg.name
        t3, s3 = voice_dir / "t3.gguf", voice_dir / "s3.gguf"
        stamp = voice_dir / "bake.json"
        if Contract(wanted, t3, s3).matches(stamp):
            return t3, s3, wanted
        kill(MODELS / "server.pid")
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
    def ensure(self, cfg: Variant, exe: Path, t3: Path, s3: Path, knobs: dict[str, str], py: Path, ckpt: Path, voice: dict) -> str:
        pipe = rf"\\.\pipe\chatterbox-{cfg.name}"
        pid = MODELS / "server.pid"
        flags = dict(knobs)
        if cfg.architecture == 'llama':
            flags.update({
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
        for legacy in MODELS.glob("*.pid"):
            if legacy != pid:
                kill(legacy)
        if pid.is_file():
            record = json.loads(pid.read_text(encoding="utf-8"))
            if record["contract"] == wanted and alive(record["pid"]):
                if not K32.WaitNamedPipeW(pipe, 0xFFFFFFFF):
                    raise ctypes.WinError(ctypes.get_last_error())
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

    def synthesize(self, pipe: str, language: str, text: str) -> bytes:
        if not K32.WaitNamedPipeW(pipe, 0xFFFFFFFF):
            raise ctypes.WinError(ctypes.get_last_error())
        payload = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        with open(pipe, "r+b", buffering=0) as stream:
            message = memoryview(f"{language}\n{len(payload)}\n".encode("utf-8") + payload)
            while message:
                sent = stream.write(message)
                if not sent:
                    raise BrokenPipeError(pipe)
                message = message[sent:]
            ack = stream.readline().decode("utf-8").strip()
            if not ack.startswith("ok "):
                raise RuntimeError(ack or "server closed the pipe")
            pcm = bytearray()
            remaining = int(ack[3:]) * 2
            while remaining:
                chunk = stream.read(remaining)
                if not chunk:
                    raise BrokenPipeError(pipe)
                pcm += chunk
                remaining -= len(chunk)
        return bytes(pcm)


class Host:
    def parser(self, cfg=None):
        parser = argparse.ArgumentParser(prog="python tts.py" + (f" {cfg.name}" if cfg else ""),
                                         formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        groups = {name: parser.add_argument_group(title, next(row["help"] for row in FLAGS if row["name"] == "variant") if name == "model" else None) for name, title in
                  (("model", "Model"), ("conversion", "GGUF conversion"), ("server", "Server"), ("session", "Voice session"))} if cfg else {"model": parser}
        for row in FLAGS:
            name = row["name"]
            if (cfg is None) != (name == "variant"):
                continue
            if cfg and row["architecture"] not in ("both", cfg.architecture):
                continue
            default = row["default"]
            if isinstance(default, dict):
                default = default[cfg.architecture]
            options = {"help": row["help"], "default": default}
            for key in ("choices", "metavar", "nargs", "action"):
                if key in row:
                    options[key] = row[key]
            groups[row["group"]].add_argument(name if row.get("positional") else f"--{name}", **options)
        return parser

    def bind_quant_types(self, py: Path) -> None:
        site_packages = py.resolve().parent.parent / "Lib" / "site-packages"
        if not site_packages.is_dir():
            raise FileNotFoundError(site_packages)
        path = str(site_packages)
        if sys.path[:1] != [path]:
            sys.path.insert(0, path)
        from scripts.quant import TYPES
        listing = ", ".join(TYPES)
        for row in FLAGS:
            if row["name"] in ("t3-weight-type", "s3-weight-type"):
                row["choices"] = TYPES
                row["help"] = row["help"].split(" Types:")[0].rstrip(".") + ". Types: " + listing + "."

    def parse(self, cfg: Variant, argv: list[str]) -> LaunchArgs:
        values = vars(self.parser(cfg).parse_args(argv[1:]))
        return LaunchArgs(values["text"], values.get("language"),
                          {row["name"]: str(values[row["name"].replace("-", "_")]) for row in FLAGS
                           if row["group"] == "server" and row["architecture"] in ("both", cfg.architecture)},
                          values["t3_weight_type"], values["s3_weight_type"],
                          Path(values["reference"]).expanduser().resolve(),
                          (ROOT / values["t3_quant_policy"]).resolve(), (ROOT / values["s3_quant_policy"]).resolve(),
                          values["listen"],
                          {row["name"]: str(values[row["name"].replace("-", "_")]) for row in FLAGS if row["group"] == "session"})

    def run(self, variant: Variant, argv: list[str]) -> Path:
        py = Venv().ensure()
        self.bind_quant_types(py)
        args = self.parse(variant, argv)
        if not args.listen and args.text is None:
            raise SystemExit("TEXT is required without --listen")
        family = ARCHITECTURES[variant.architecture]
        MODELS.mkdir(parents=True, exist_ok=True)
        engine = EngineBuild()
        server, bake = engine.ensure()
        ckpt = Checkpoints().ensure(variant)
        base_t3 = args.gguf("t3", variant.name)
        base_s3 = args.gguf("s3gen", family["s3_family"])
        t3_contract, s3_contract = Converter().ensure(variant, py, ckpt, base_t3, base_s3, args)
        t3, s3, voice = VoiceBake().ensure(variant, args.reference, base_t3, base_s3, bake, t3_contract, s3_contract, engine.wanted())
        pipe = PipeServer().ensure(variant, server, t3, s3, args.knobs, py, ckpt, voice)
        if args.listen:
            from listen import Session
            Session(pipe, t3, args, py).run()
            raise SystemExit
        pcm = PipeServer().synthesize(pipe, args.language or "", args.text)
        return write_wav(pcm, variant.name)
