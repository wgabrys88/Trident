import ctypes
import hashlib
import json
import os
import shutil
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
ENGINE_REV = "aaafdb6e1d83ecb8fd963ac2e76bc5e5718074e5"
BASE_TRIDENT_REV = "38e0c4947d236e239ffd8e2cd2b98a8c39848efc"
RELEASE_ID = "trident-best-evidence-2026-09-13-v1"
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
    pid_name: str
    build_name: str
    venv_name: str
    ckpt_name: str
    pipe_tag: bytes
    other_pids: tuple[str, ...]
    t3_script: str
    s3_script: str
    knobs: tuple[str, ...]
    t3_convert_flags: tuple[str, ...] = ()
    needs_language: bool = False
    count_roof: int = 0
    count_chunk_short: int = 0
    count_chunk_long: int = 0
    prose_roof: int = 280
    policy: str = ""


def run(cmd, **kw):
    subprocess.run(cmd, check=True, **kw)


def git_out(args, repo=CHATTERBOX):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_text(obj) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def read_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json_text(obj), encoding="utf-8")
    tmp.replace(path)


def contract_id(obj) -> str:
    return sha256_bytes(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def ensure_pin(cfg: Variant):
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
        raise SystemExit("chatterbox.cpp has uncommitted changes; use the exact clean engine checkout")
    sha = git_out(["rev-parse", "HEAD"])
    if sha != cfg.chatterbox_rev:
        raise SystemExit(
            f"chatterbox.cpp HEAD {sha} != required {cfg.chatterbox_rev}. "
            "Checkout the exact detached commit; branch name is intentionally irrelevant."
        )


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


def spawn(cfg: Variant, exe: Path, t3: Path, s3: Path, pipe: str, pid: Path, language=None, knobs=None):
    args = [str(exe), str(t3), str(s3), pipe]
    if cfg.needs_language:
        args.append(language or "en")
    env = os.environ.copy()
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


def utterances(text: str) -> list[str]:
    parts = [p.strip() for p in text.split("|||")]
    out = [p for p in parts if p]
    if not out:
        raise SystemExit("empty text")
    return out


EN_ONES = [
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
]
EN_TENS = ["", "", "twenty", "thirty", "forty", "fifty"]


def en_phrase(n: int) -> str:
    if n < 20:
        return EN_ONES[n - 1]
    tens, ones = divmod(n, 10)
    if tens >= len(EN_TENS):
        raise ValueError("counting helper supports 1..59")
    if ones == 0:
        return EN_TENS[tens]
    return f"{EN_TENS[tens]}-{EN_ONES[ones - 1]}"


def en_list(lo: int, hi: int) -> str:
    parts = [en_phrase(i) for i in range(lo, hi + 1)]
    parts[0] = parts[0].capitalize()
    return ", ".join(parts) + "."


def _norm_word(s: str) -> str:
    return s.lower().replace("-", " ")


def list_hi(text: str) -> int | None:
    t = text.strip()
    if not t.endswith("."):
        return None
    parts = [p.strip() for p in t[:-1].split(",") if p.strip()]
    if not parts or len(parts) > 59:
        return None
    for i, p in enumerate(parts, 1):
        w = en_phrase(i)
        if i == 1:
            w = w.capitalize()
        if _norm_word(p) != _norm_word(w):
            return None
    return len(parts)


def list_chunks(cfg: Variant, n: int) -> list[str]:
    if n <= cfg.count_roof:
        return [en_list(1, n)]
    sz = cfg.count_chunk_long if n > 30 else cfg.count_chunk_short
    sz = max(1, sz)
    out = []
    lo = 1
    while lo <= n:
        hi = min(lo + sz - 1, n)
        out.append(en_list(lo, hi))
        lo = hi + 1
    return out


def eld_mode() -> bool:
    v = os.environ.get("CHATTERBOX_ELD", "").strip().lower()
    return v in ("1", "true", "yes")


def prose_chunks(text: str, max_chars: int) -> list[str]:
    t = text.strip()
    if len(t) <= max_chars:
        return [t]
    sentences = []
    buf = ""
    for ch in t:
        buf += ch
        if ch in ".!?" and len(buf.strip()) >= 10:
            sentences.append(buf.strip())
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())
    if not sentences:
        return [t]
    out = []
    cur = ""
    for sentence in sentences:
        if not cur:
            cur = sentence
        elif len(cur) + 1 + len(sentence) <= max_chars:
            cur = f"{cur} {sentence}"
        else:
            out.append(cur)
            cur = sentence
    if cur:
        out.append(cur)
    return out or [t]


def speak_pieces(cfg: Variant, text: str, *, chunk: bool = True) -> list[str]:
    if not chunk or eld_mode():
        return utterances(text)
    out = []
    for piece in utterances(text):
        n = list_hi(piece)
        if n:
            out.extend(list_chunks(cfg, n))
        else:
            out.extend(prose_chunks(piece, cfg.prose_roof))
    if not out:
        raise SystemExit("empty text")
    return out


def wav_duration_s(path: Path) -> float:
    n = path.stat().st_size
    if n <= 44:
        raise RuntimeError("WAV length")
    return (n - 44) / 2.0 / 24000.0


def speak(pipe: str, pid: Path, out: Path, text: str) -> float:
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
    if ack != b"ok\n":
        raise RuntimeError("synthesize")
    return time.perf_counter() - t0


def usage(cfg: Variant):
    flags = " ".join(f"[--{n} <v>]" for n in cfg.knobs)
    if cfg.needs_language:
        return f"usage: python tts_{cfg.name}.py [-h] [--no-chunk] {flags} <text> <language>"
    return f"usage: python tts_{cfg.name}.py [-h] [--no-chunk] {flags} <text>"


def parse_variant_args(cfg: Variant, argv: list[str]):
    args = argv[1:]
    cli = {}
    no_chunk = False
    i = 0
    allowed = set(cfg.knobs)
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help", "-?"):
            print(usage(cfg))
            raise SystemExit(0)
        if a == "--no-chunk":
            no_chunk = True
            i += 1
            continue
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
        return rest[0], rest[1].lower(), cli, no_chunk
    if len(rest) != 1:
        raise SystemExit(usage(cfg))
    return rest[0], None, cli, no_chunk


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


def build_contract(cfg: Variant):
    return {
        "engine_rev": cfg.chatterbox_rev,
        "ggml_rev": GGML_REV,
        "family": cfg.name,
        "generator": "Visual Studio 17 2022 x64",
        "cmake_flags": {
            "GGML_VULKAN": "ON",
            "GGML_CUDA": "OFF",
            "GGML_CPU": "OFF",
            "GGML_OPENMP": "OFF",
            "BUILD_SHARED_LIBS": "ON",
            "TTS_CPP_BUILD_EXECUTABLES": "ON",
            "GGML_BUILD_TESTS": "OFF",
            "GGML_BUILD_EXAMPLES": "OFF",
            "TTS_FAMILY": cfg.name,
            "VulkanSDK": str(VULKAN),
        },
    }


def ensure_build(cfg: Variant, pid: Path, build: Path, exe: Path, bake: Path):
    ggml = CHATTERBOX / "ggml"
    stamp = MODELS / f"{cfg.name}.build-contract.json"
    wanted = build_contract(cfg)
    if exe.is_file() and bake.is_file() and read_json(stamp) == wanted:
        return
    kill(pid)
    if not (ggml / "CMakeLists.txt").is_file():
        run(["git", "clone", "--filter=blob:none", "https://github.com/ggml-org/ggml.git", str(ggml)])
        run(["git", "-C", str(ggml), "checkout", "--detach", GGML_REV])
    else:
        actual = git_out(["rev-parse", "HEAD"], ggml)
        if actual != GGML_REV:
            raise SystemExit(f"ggml {actual} != required {GGML_REV}")
        dirty = subprocess.run(
            ["git", "-C", str(ggml), "status", "--porcelain"], check=True, capture_output=True, text=True
        ).stdout.strip()
        if dirty:
            raise SystemExit("ggml checkout is dirty")
    if build.exists():
        shutil.rmtree(build)
    run([
        CMAKE, "-S", str(CHATTERBOX), "-B", str(build), "-G", "Visual Studio 17 2022", "-A", "x64",
        "-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_CPU=OFF", "-DGGML_OPENMP=OFF",
        "-DBUILD_SHARED_LIBS=ON", "-DTTS_CPP_BUILD_EXECUTABLES=ON", "-DGGML_BUILD_TESTS=OFF",
        "-DGGML_BUILD_EXAMPLES=OFF", f"-DTTS_FAMILY={cfg.name}",
        f"-DVulkan_INCLUDE_DIR={VULKAN / 'Include'}", f"-DVulkan_LIBRARY={VULKAN / 'Lib/vulkan-1.lib'}",
        f"-DVulkan_GLSLC_EXECUTABLE={VULKAN / 'Bin/glslc.exe'}",
    ])
    run([CMAKE, "--build", str(build), "--config", "Release", "--target", "chatterbox-server", "--target", "chatterbox-bake", "--parallel"])
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


def asset_fingerprint(cfg: Variant, ckpt: Path):
    cache_path = ckpt / ".asset-sha256.json"
    cache = read_json(cache_path) or {}
    changed = False
    out = {}
    for name in cfg.assets:
        p = ckpt / name
        st = p.stat()
        row = cache.get(name) or {}
        if row.get("size") == st.st_size and row.get("mtime_ns") == st.st_mtime_ns and row.get("sha256"):
            digest = row["sha256"]
        else:
            digest = sha256_file(p)
            cache[name] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": digest}
            changed = True
        out[name] = digest
    if changed or not cache_path.is_file():
        write_json(cache_path, cache)
    return out


def conversion_contract(cfg: Variant, ckpt: Path, script: Path, kind: str):
    return {
        "family": cfg.name,
        "kind": kind,
        "engine_rev": cfg.chatterbox_rev,
        "hf_base": cfg.hf,
        "assets_sha256": asset_fingerprint(cfg, ckpt),
        "script": script.name,
        "script_sha256": sha256_file(script),
        "flags": list(cfg.t3_convert_flags) if kind == "t3" else [],
    }


def convert_t3(cfg: Variant, py: Path, ckpt: Path, t3: Path, contract):
    t3.unlink(missing_ok=True)
    run([str(py), str(CHATTERBOX / "scripts" / cfg.t3_script), str(ckpt), str(t3), *cfg.t3_convert_flags])
    write_json(MODELS / f"{cfg.name}.t3-convert.json", contract)


def convert_s3(cfg: Variant, py: Path, ckpt: Path, s3: Path, contract):
    s3.unlink(missing_ok=True)
    run([str(py), str(CHATTERBOX / "scripts" / cfg.s3_script), str(ckpt), str(s3)])
    write_json(MODELS / f"{cfg.name}.s3-convert.json", contract)


def ensure_converted(cfg: Variant, py: Path, ckpt: Path, t3: Path, s3: Path):
    t3_script = CHATTERBOX / "scripts" / cfg.t3_script
    s3_script = CHATTERBOX / "scripts" / cfg.s3_script
    t3_contract = conversion_contract(cfg, ckpt, t3_script, "t3")
    s3_contract = conversion_contract(cfg, ckpt, s3_script, "s3")
    t3_stamp = MODELS / f"{cfg.name}.t3-convert.json"
    s3_stamp = MODELS / f"{cfg.name}.s3-convert.json"
    t3_changed = (not t3.is_file()) or read_json(t3_stamp) != t3_contract
    s3_changed = (not s3.is_file()) or read_json(s3_stamp) != s3_contract
    changed = t3_changed or s3_changed
    if changed:
        # Keep T3 and S3 as one clean conversion pair. If either contract changes,
        # rebuild both before voice conditioning is baked into them.
        convert_t3(cfg, py, ckpt, t3, t3_contract)
        convert_s3(cfg, py, ckpt, s3, s3_contract)
    return t3_contract, s3_contract, changed


def ensure_baked(cfg: Variant, py: Path, ckpt: Path, t3: Path, s3: Path, bake: Path, bin_dir: Path, pid: Path, t3_contract, s3_contract, converted: bool):
    ref_sha = sha256_file(REF)
    bake_contract = {
        "family": cfg.name,
        "engine_rev": cfg.chatterbox_rev,
        "reference_sha256": ref_sha,
        "t3_conversion_id": contract_id(t3_contract),
        "s3_conversion_id": contract_id(s3_contract),
        "bake_exe_sha256": sha256_file(bake),
    }
    stamp = MODELS / f"{cfg.name}.bake-contract.json"
    if not converted and read_json(stamp) == bake_contract:
        return bake_contract
    kill(pid)
    if not converted:
        # Recreate pristine GGUFs before changing baked conditioning. This avoids relying on in-place rebake semantics.
        convert_t3(cfg, py, ckpt, t3, t3_contract)
        convert_s3(cfg, py, ckpt, s3, s3_contract)
    run([str(bake), str(t3), str(s3), str(REF)], cwd=str(bin_dir))
    write_json(stamp, bake_contract)
    return bake_contract


def provenance(cfg: Variant, out: Path, original_text: str, piece: str, piece_index: int, piece_count: int, language: str, values: dict[str, str], t3: Path, s3: Path, exe: Path, bake: Path, t3_contract, s3_contract, bake_contract, no_chunk: bool):
    obj = {
        "release_id": RELEASE_ID,
        "base_trident_revision": BASE_TRIDENT_REV,
        "engine_revision": cfg.chatterbox_rev,
        "ggml_revision": GGML_REV,
        "family": cfg.name,
        "family_policy": cfg.policy,
        "hf_base": cfg.hf,
        "t3_file": t3.name,
        "t3_sha256": sha256_file(t3),
        "s3_file": s3.name,
        "s3_sha256": sha256_file(s3),
        "reference_sha256": bake_contract["reference_sha256"],
        "server_exe_sha256": sha256_file(exe),
        "bake_exe_sha256": sha256_file(bake),
        "t3_conversion": t3_contract,
        "s3_conversion": s3_contract,
        "language": language or None,
        "knob_overrides": values,
        "header_defaults_used_where_blank": True,
        "chunking_disabled": bool(no_chunk or eld_mode()),
        "count_policy": {
            "roof": cfg.count_roof,
            "chunk_21_to_30": cfg.count_chunk_short,
            "chunk_over_30": cfg.count_chunk_long,
            "prose_chars": cfg.prose_roof,
        },
        "original_text": original_text,
        "original_text_sha256": sha256_bytes(original_text.encode("utf-8")),
        "piece_index": piece_index,
        "piece_count": piece_count,
        "piece_text": piece,
        "piece_text_sha256": sha256_bytes(piece.encode("utf-8")),
        "wav": out.name,
        "wav_sha256": sha256_file(out),
        "generated_local_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    write_json(out.with_suffix(out.suffix + ".provenance.json"), obj)


def run_variant(cfg: Variant, text: str, language=None, knobs=None, no_chunk: bool = False):
    if not REF.is_file():
        raise FileNotFoundError(str(REF))
    if cfg.chatterbox_rev != ENGINE_REV:
        raise SystemExit(f"launcher engine pin {cfg.chatterbox_rev} != release engine pin {ENGINE_REV}")
    ensure_pin(cfg)
    MODELS.mkdir(parents=True, exist_ok=True)
    t3, s3, pid, build, bin_dir, exe, bake, pipe = paths(cfg)
    for name in cfg.other_pids:
        kill(MODELS / name)
    ensure_build(cfg, pid, build, exe, bake)
    py = ensure_converter_venv(cfg)
    ckpt = ensure_assets(cfg)
    t3_contract, s3_contract, converted = ensure_converted(cfg, py, ckpt, t3, s3)
    bake_contract = ensure_baked(cfg, py, ckpt, t3, s3, bake, bin_dir, pid, t3_contract, s3_contract, converted)

    lang = (language or "en").lower() if cfg.needs_language else ""
    language_stamp = MODELS / f"{cfg.name}.language"
    if cfg.needs_language:
        previous = language_stamp.read_text(encoding="ascii").strip() if language_stamp.is_file() else ""
        if previous != lang:
            kill(pid)
            language_stamp.write_text(lang, encoding="ascii")

    values = wanted_knobs(cfg, knobs or {})
    blob = knobs_blob(cfg, values)
    knob_stamp = MODELS / f"{cfg.name}.knobs"
    previous_blob = knob_stamp.read_text(encoding="ascii") if knob_stamp.is_file() else ""
    if previous_blob != blob:
        kill(pid)
        knob_stamp.write_text(blob, encoding="ascii")

    pipe_stamp = MODELS / f"{cfg.name}.pipeproto"
    if not pipe_stamp.is_file() or pipe_stamp.read_text(encoding="ascii").strip() != "byte-length-v1":
        kill(pid)
        pipe_stamp.write_text("byte-length-v1", encoding="ascii")

    if not running(pid):
        wait_pipe_absent(pipe)
        spawn(cfg, exe, t3, s3, pipe, pid, lang or None, values)

    pieces = speak_pieces(cfg, text, chunk=not no_chunk)
    wav_paths = []
    for i, piece in enumerate(pieces):
        stamp_t = time.strftime("%Y%m%d-%H%M%S")
        name = f"{stamp_t}-{cfg.name}.wav" if len(pieces) == 1 else f"{stamp_t}-{cfg.name}-{i}.wav"
        out = ROOT / name
        wall = speak(pipe, pid, out, piece)
        dur = wav_duration_s(out)
        rtf = wall / dur if dur > 0 else 0.0
        provenance(cfg, out, text, piece, i, len(pieces), lang, values, t3, s3, exe, bake, t3_contract, s3_contract, bake_contract, no_chunk)
        print(f"wall_s={wall:.3f} duration_s={dur:.3f} rtf={rtf:.3f}", file=sys.stderr, flush=True)
        print(out, flush=True)
        wav_paths.append(out)
    if len(wav_paths) > 1:
        manifest = ROOT / f"{time.strftime('%Y%m%d-%H%M%S')}-{cfg.name}-manifest.json"
        write_json(manifest, {"family": cfg.name, "release_id": RELEASE_ID, "files": [str(p) for p in wav_paths]})
        print("manifest " + str(manifest), flush=True)
