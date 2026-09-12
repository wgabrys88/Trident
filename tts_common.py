import ctypes
import hashlib
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
    branch: str
    chatterbox_rev: str
    hf: str
    assets: tuple[str, ...]
    t3_name: str
    s3_name: str
    stamp_name: str
    rev_name: str
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


def run(cmd, **kw):
    subprocess.run(cmd, check=True, **kw)


def git_out(args, repo=CHATTERBOX):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def ensure_pin(cfg: Variant):
    if not CHATTERBOX.is_dir():
        raise SystemExit(f"missing chatterbox.cpp sibling at {CHATTERBOX}")
    run(["git", "-C", str(CHATTERBOX), "checkout", cfg.branch])
    sha = git_out(["rev-parse", "HEAD"])
    if sha != cfg.chatterbox_rev:
        raise SystemExit(f"HEAD {sha} != {cfg.chatterbox_rev}")


def download(url: str, dest: Path):
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url) as resp, open(tmp, "wb") as out:
            data = resp.read()
            if not data:
                raise OSError("empty download")
            out.write(data)
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def paths(cfg: Variant):
    t3 = MODELS / cfg.t3_name
    s3 = MODELS / cfg.s3_name
    stamp = MODELS / cfg.stamp_name
    rev = MODELS / cfg.rev_name
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
    return t3, s3, stamp, rev, pid, build, bin_dir, exe, bake, pipe


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


KNOB_ENV = {
    "repeat-penalty": ("CHATTERBOX_REPEAT_PENALTY", "f+"),
    "temperature": ("CHATTERBOX_TEMPERATURE", "f"),
    "top-k": ("CHATTERBOX_TOP_K", "i"),
    "top-p": ("CHATTERBOX_TOP_P", "f"),
    "repeat-last-n": ("CHATTERBOX_REPEAT_LAST_N", "i"),
    "seed": ("CHATTERBOX_SEED", "i"),
    "n-predict": ("CHATTERBOX_N_PREDICT", "i"),
    "cfm-steps": ("CHATTERBOX_CFM_STEPS", "i"),
    "silence-token": ("CHATTERBOX_SILENCE_TOKEN", "i"),
    "silence-count": ("CHATTERBOX_SILENCE_COUNT", "i"),
    "min-p": ("CHATTERBOX_MIN_P", "f"),
    "cfg-weight": ("CHATTERBOX_CFG_WEIGHT", "f"),
    "cfm-cfg": ("CHATTERBOX_CFM_CFG", "f"),
}
SHARED_KNOBS = (
    "repeat-penalty",
    "temperature",
    "top-k",
    "top-p",
    "repeat-last-n",
    "seed",
    "n-predict",
    "cfm-steps",
    "silence-token",
)
GPT2_KNOBS = SHARED_KNOBS + ("silence-count",)
V3_KNOBS = SHARED_KNOBS + ("min-p", "cfg-weight", "cfm-cfg")


def spawn(
    cfg: Variant,
    exe: Path,
    t3: Path,
    s3: Path,
    pipe: str,
    pid: Path,
    language: str | None = None,
    knobs: dict[str, str] | None = None,
):
    args = [str(exe), str(t3), str(s3), pipe]
    if cfg.needs_language:
        args.append(language or "en")
    env = os.environ.copy()
    env.setdefault("CHATTERBOX_SAMPLER_LOG", "1")
    wanted = knobs or {}
    for name, (var, _) in KNOB_ENV.items():
        val = wanted.get(name, "") if name in cfg.knobs else ""
        if val:
            env[var] = val
        else:
            env.pop(var, None)
    pid.write_text(
        str(
            subprocess.Popen(
                args,
                cwd=str(exe.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=DETACH,
                env=env,
            ).pid
        ),
        encoding="ascii",
    )


def speak(pipe: str, pid: Path, out: Path, text: str):
    for _ in range(120):
        if not running(pid):
            raise RuntimeError("daemon")
        if K32.WaitNamedPipeW(pipe, 1000):
            break
        time.sleep(1)
    else:
        raise RuntimeError("daemon timeout")
    line = text.replace("\r", " ").replace("\n", " ")
    with open(pipe, "r+b", buffering=0) as f:
        f.write(f"{out}\n{line}\n".encode("utf-8"))
        ack = f.readline()
    if ack != b"ok\n":
        raise RuntimeError("synthesize")


def usage(cfg: Variant):
    flags = " ".join(f"[--{n} <v>]" for n in cfg.knobs)
    if cfg.needs_language:
        return f"usage: python tts_{cfg.name}.py {flags} <text> <language>"
    return f"usage: python tts_{cfg.name}.py {flags} <text>"


def normalize_knob(name: str, raw: str | None) -> str:
    if raw is None or not str(raw).strip():
        return ""
    kind = KNOB_ENV[name][1]
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
    out = {}
    for name in cfg.knobs:
        if name in cli:
            out[name] = normalize_knob(name, cli[name])
        else:
            out[name] = normalize_knob(name, os.environ.get(KNOB_ENV[name][0]))
    return out


def knobs_blob(cfg: Variant, values: dict[str, str]) -> str:
    return "".join(f"{n}={values.get(n, '')}\n" for n in cfg.knobs)


def parse_variant_args(cfg: Variant, argv: list[str]) -> tuple[str, str | None, dict[str, str]]:
    args = argv[1:]
    cli: dict[str, str] = {}
    i = 0
    allowed = set(cfg.knobs)
    while i < len(args):
        a = args[i]
        if not a.startswith("--"):
            break
        name = a[2:]
        if name not in allowed or i + 1 >= len(args):
            raise SystemExit(usage(cfg))
        cli[name] = args[i + 1]
        i += 2
    rest = args[i:]
    if cfg.needs_language:
        if len(rest) != 2:
            raise SystemExit(usage(cfg))
        return rest[0], rest[1].lower(), cli
    if len(rest) != 1:
        raise SystemExit(usage(cfg))
    return rest[0], None, cli


def run_variant(
    cfg: Variant,
    text: str,
    language: str | None = None,
    knobs: dict[str, str] | None = None,
):
    if not REF.is_file():
        raise FileNotFoundError(str(REF))
    ensure_pin(cfg)
    MODELS.mkdir(parents=True, exist_ok=True)
    t3, s3, stamp, rev, pid, build, bin_dir, exe, bake, pipe = paths(cfg)
    for name in cfg.other_pids:
        kill(MODELS / name)
    ggml = CHATTERBOX / "ggml"
    if (
        not exe.is_file()
        or not bake.is_file()
        or not rev.is_file()
        or rev.read_text(encoding="ascii").strip() != cfg.chatterbox_rev
    ):
        kill(pid)
        if not (ggml / "CMakeLists.txt").is_file():
            run(["git", "clone", "--filter=blob:none", "https://github.com/ggml-org/ggml.git", str(ggml)])
            run(["git", "-C", str(ggml), "checkout", GGML_REV])
        elif git_out(["rev-parse", "HEAD"], ggml) != GGML_REV:
            raise SystemExit(f"ggml {git_out(['rev-parse', 'HEAD'], ggml)} != {GGML_REV}")
        run(
            [
                CMAKE,
                "-S",
                str(CHATTERBOX),
                "-B",
                str(build),
                "-G",
                "Visual Studio 17 2022",
                "-A",
                "x64",
                "-DGGML_VULKAN=ON",
                "-DGGML_CUDA=OFF",
                "-DGGML_CPU=OFF",
                "-DGGML_OPENMP=OFF",
                "-DBUILD_SHARED_LIBS=ON",
                "-DTTS_CPP_BUILD_EXECUTABLES=ON",
                "-DGGML_BUILD_TESTS=OFF",
                "-DGGML_BUILD_EXAMPLES=OFF",
                f"-DVulkan_INCLUDE_DIR={VULKAN / 'Include'}",
                f"-DVulkan_LIBRARY={VULKAN / 'Lib/vulkan-1.lib'}",
                f"-DVulkan_GLSLC_EXECUTABLE={VULKAN / 'Bin/glslc.exe'}",
            ]
        )
        run(
            [
                CMAKE,
                "--build",
                str(build),
                "--config",
                "Release",
                "--target",
                "chatterbox-server",
                "--target",
                "chatterbox-bake",
                "--parallel",
            ]
        )
        rev.write_text(cfg.chatterbox_rev, encoding="ascii")
    py = ROOT / cfg.venv_name / "Scripts" / "python.exe"
    if not py.is_file():
        venv.EnvBuilder(with_pip=True).create(ROOT / cfg.venv_name)
        pip = [str(py), "-m", "pip", "install", "--disable-pip-version-check"]
        run([*pip, "torch==2.6.0", "--index-url", "https://download.pytorch.org/whl/cpu"])
        run([*pip, "numpy==1.26.4", "gguf==0.19.0", "safetensors==0.5.3", "scipy==1.15.3", "librosa==0.11.0"])
    ckpt = ROOT / cfg.ckpt_name
    ckpt.mkdir(parents=True, exist_ok=True)
    for name in cfg.assets:
        dest = ckpt / name
        if not dest.is_file():
            download(f"{cfg.hf}/{name}", dest)
    converted = False
    for dst, script in ((t3, cfg.t3_script), (s3, cfg.s3_script)):
        if not dst.is_file():
            run([str(py), str(CHATTERBOX / "scripts" / script), str(ckpt), str(dst)])
            converted = True
    voice = hashlib.sha256(REF.read_bytes()).hexdigest()
    voice_stamp = stamp.read_text(encoding="ascii").strip() if stamp.is_file() else ""
    lang_stamp_path = MODELS / f"{cfg.name}.language" if cfg.needs_language else None
    lang_stamp = ""
    if lang_stamp_path and lang_stamp_path.is_file():
        lang_stamp = lang_stamp_path.read_text(encoding="ascii").strip()
    out = ROOT / time.strftime(f"%Y%m%d-%H%M%S-{cfg.name}.wav")
    lang = (language or "en").lower() if cfg.needs_language else ""
    if converted or voice_stamp != voice:
        kill(pid)
        run([str(bake), str(t3), str(s3), str(REF)], cwd=str(bin_dir))
        stamp.write_text(voice, encoding="ascii")
    if cfg.needs_language and lang_stamp != lang:
        kill(pid)
        if lang_stamp_path:
            lang_stamp_path.write_text(lang, encoding="ascii")
    values = wanted_knobs(cfg, knobs or {})
    blob = knobs_blob(cfg, values)
    knob_stamp = MODELS / f"{cfg.name}.knobs"
    prev = knob_stamp.read_text(encoding="ascii") if knob_stamp.is_file() else ""
    if prev != blob:
        kill(pid)
        knob_stamp.write_text(blob, encoding="ascii")
    if not running(pid):
        wait_pipe_absent(pipe)
        spawn(cfg, exe, t3, s3, pipe, pid, lang or None, values)
    speak(pipe, pid, out, text)
    print(out)
