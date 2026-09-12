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


def utterances(text: str) -> list[str]:
    parts = [p.strip() for p in text.split("|||")]
    out = [p for p in parts if p]
    if not out:
        raise SystemExit("empty text")
    return out


def wav_duration_s(path: Path) -> float:
    n = path.stat().st_size
    if n <= 44:
        raise RuntimeError("WAV Length")
    return (n - 44) / 2.0 / 24000.0


def play_wav(path: Path, duration_s: float):
    sec = max(1, int(duration_s) + 1)
    cmd = (
        "$ErrorActionPreference = 'Stop'; $p = Start-Process -FilePath "
        + json.dumps(str(path))
        + " -PassThru; Start-Sleep -Seconds "
        + str(sec)
        + "; if ($null -ne $p) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", cmd], check=True)


def speak(pipe: str, pid: Path, out: Path, text: str) -> float:
    for _ in range(120):
        if not running(pid):
            raise RuntimeError("daemon")
        if K32.WaitNamedPipeW(pipe, 1000):
            break
        time.sleep(1)
    else:
        raise RuntimeError("daemon timeout")
    line = text.replace("\r", " ").replace("\n", " ")
    if "|||" in line:
        raise RuntimeError("delimiter")
    t0 = time.perf_counter()
    with open(pipe, "r+b", buffering=0) as f:
        f.write(f"{out}\n{line}\n".encode("utf-8"))
        ack = f.readline()
    if ack != b"ok\n":
        raise RuntimeError("synthesize")
    return time.perf_counter() - t0


NANO_HELP = """
Feed speakable English as one argv string. You are the chunker for quality.
This launcher is not a sentence splitter and the C++ engine is not a chunker.
Do not invent a character cap. Do not copy Gradio 300. Do not copy a V3
text_pos budget onto Nano.

Native bounds, not characters. Convert writes chatterbox.n_ctx from
tfmr.wpe.weight rows. Discover live wpe shape and chatterbox.cond_prompt_length
on the T3 GGUF. This tree: wpe 768 by 8196, cond_prompt_length 375.
t3_nano.cpp sets hp.n_ctx from wpe then clamps to N_CTX 2024 in nano.h.
That clamp is the live KV. Do not shrink the wpe table. prompt_len is 1
plus cond_prompt_len plus n_text_tokens plus 1. Engine throws T3 prompt
exceeds context if prompt_len is greater than n_ctx. Generation also
stops when n_past plus 1 is greater than n_ctx. N_PREDICT 1000 caps
output speech tokens, about 40 seconds at 960 samples per token. Split
with ||| only to speak separate utterances, not to fake a length limit.

Delimiter: join pieces with a line that is only |||. utterances() splits on
that, strips, skips empty, then speak() each piece. speak() fails if a piece
still contains |||. The C++ engine must never see ||| and must not chunk on
punctuation. The named pipe receives one clean utterance: official tags plus
raw text.

Official tags only, including brackets and the space in [clear throat]. They
are added_tokens.json ids 50257 through 50275. gpt2_bpe matches id >= 50257 as
literal substrings. A tagged synth dump text line must show those ids. If the
dump splits [laugh] into normal BPE, convert or BPE is wrong.

Event tags: [clear throat] [sigh] [shush] [cough] [groan] [sniff] [gasp]
[chuckle] [laugh]
Style tags: [angry] [fear] [surprised] [whispering] [advertisement] [dramatic]
[narration] [crying] [happy] [sarcastic]
Put tags mid-sentence. Example shape: Oh, that's hilarious! [chuckle] Um
anyway, we do have a new model. No [pause]. No extra tags. Emotion is the tag
plus baked reference.wav.

Empty models/nano.knobs values mean header defaults: SEED 42, N_PREDICT 1000,
TOP_K 1000, TOP_P 0.95, TEMPERATURE 0.8, REPEAT_PENALTY 1.2, REPEAT_LAST_N 1000,
CFM_STEPS 2, SILENCE_TOKEN 4299, SILENCE_COUNT 3. CFM_STEPS 2 matches turbo
n_cfm_timesteps=2. Do not add MIN_P. Do not invent C++ argv knobs.

Run from Trident with sibling chatterbox.cpp. Speaker is operator
reference.wav, 16-bit PCM mono 24000 Hz. After each successful speak this
launcher prints wall_s duration_s rtf on stderr, plays the WAV with the Windows
associated player, waits duration plus one second, then prints the WAV path.
Success is WAV Length greater than 44. Synth RTF is speak wall-clock after the
pipe is ready, divided by WAV duration.
"""

TURBO_HELP = """
Feed speakable English as one argv string. You are the chunker for quality.
This launcher is not a sentence splitter and the C++ engine is not a chunker.
Do not invent a character cap. Do not copy Gradio 300. Do not copy Nano's
2024 clamp. Do not copy a V3 text_pos budget onto Turbo.

Native bounds, not characters. Convert writes chatterbox.n_ctx from
tfmr.wpe.weight rows. Discover live wpe shape and chatterbox.cond_prompt_length
on the T3 GGUF. This tree: wpe 1024 by 8196, cond_prompt_length 375,
n_embd 1024, n_head 16, n_layer 24, text vocab 50276. t3_nano.cpp on turbo
sets hp.n_ctx from wpe with no header clamp. turbo.h has no N_CTX constant.
Do not shrink the wpe table. prompt_len is 1 plus cond_prompt_len plus
n_text_tokens plus 1. Engine throws T3 prompt exceeds context if prompt_len
is greater than n_ctx. Generation also stops when n_past plus 1 is greater
than n_ctx. N_PREDICT 1000 caps output speech tokens, about 40 seconds at
960 samples per token. Vendor speech_cond_prompt_len is 375. emotion_adv is
false. Dump path is turbo_t3_dump.txt beside the T3 GGUF. Split with |||
only to speak separate utterances, not to fake a length limit.

Delimiter: join pieces with a line that is only |||. utterances() splits on
that, strips, skips empty, then speak() each piece. speak() fails if a piece
still contains |||. The C++ engine must never see ||| and must not chunk on
punctuation. The named pipe receives one clean utterance: official tags plus
raw text.

Official tags only, including brackets and the space in [clear throat]. They
are added_tokens.json ids 50257 through 50275. gpt2_bpe matches id >= 50257 as
literal substrings. A tagged synth dump text line must show those ids. If the
dump splits [laugh] into normal BPE, convert or BPE is wrong.

Event tags: [clear throat] [sigh] [shush] [cough] [groan] [sniff] [gasp]
[chuckle] [laugh]
Style tags: [angry] [fear] [surprised] [whispering] [advertisement] [dramatic]
[narration] [crying] [happy] [sarcastic]
Put tags mid-sentence. Example shape: Oh, that's hilarious! [chuckle] Um
anyway, we do have a new model. No [pause]. No [whisper]. No [breath]. Emotion
is the tag plus baked reference.wav.

Empty models/turbo.knobs values mean header defaults that match official
Turbo generate and inference_turbo: SEED 42, N_PREDICT 1000, TOP_K 1000,
TOP_P 0.95, TEMPERATURE 0.8, REPEAT_PENALTY 1.2, REPEAT_LAST_N 1000,
CFM_STEPS 2, SILENCE_TOKEN 4299, SILENCE_COUNT 3. CFM_STEPS 2 matches
n_cfm_timesteps=2 and meanflow, not the one-step slogan. Silence count 3
matches three S3GEN_SIL tokens. Launcher flags wrap getenv only:
--temperature --top-p --top-k --repeat-penalty --n-predict --seed
--repeat-last-n --cfm-steps --silence-token --silence-count. Vendor Python
accepts then ignores cfg_weight, min_p, and exaggeration. turbo.h has none
of them. Do not add MIN_P. Do not add CFG. Do not add an exaggeration env
knob. Do not invent C++ argv knobs.

Run from Trident with sibling chatterbox.cpp: python tts_turbo.py "<text>".
GGUF names chatterbox-t3-turbo-q8_0.gguf and chatterbox-s3gen-turbo-q4_0.gguf.
Kill via models/turbo.pid. Pipe is \\\\.\\pipe\\chatterbox-turbo-<tag>.
Speaker is operator reference.wav, 16-bit PCM mono 24000 Hz. After each
successful speak this launcher prints wall_s duration_s rtf on stderr, plays
the WAV with the Windows associated player, waits duration plus one second,
then prints the WAV path. Success is WAV Length greater than 44. Synth RTF
is speak wall-clock after the pipe is ready, divided by WAV duration.
"""

V3_HELP = """
Feed speakable text as one argv string plus a language code. You are the
chunker for quality. This launcher is not a sentence splitter and the C++
engine is not a chunker. Do not invent a character cap. Do not copy Gradio
300. Do not copy Nano or Turbo wpe budgets onto V3. V3 is Llama, not a
bigger Turbo.

Usage: python tts_v3.py [-h] [knobs] <text> <language>
Language is argv, not a tag in the text. One language per launch. Changing
language kills models/v3.pid and respawns the server. Mixed German, Polish,
and English means three launches: python tts_v3.py "<de text>" de then
python tts_v3.py "<pl text>" pl then python tts_v3.py "<en text>" en. Do not
put language into the named pipe. Do not put language into |||.

Live C++ language ids: ar da de el en es fi fr hi it ms nl no pl pt sv sw tr.
zh ja he ko ru throw language extras unread. Those need Cangjie, hiragana,
Hebrew, Korean, or Russian extras this C++ does not run. Do not invent them.

Native bounds, not characters. convert-t3-v3-to-gguf.py reads tensors:
perceiver_len from cond_enc.perceiver.pre_attention_query shape[1],
text_pos_len from text_pos_emb.emb.weight shape[0], then n_ctx equals 1
plus perceiver_len plus 1 plus text_pos_len plus 2 plus N_PREDICT.
N_PREDICT in that formula is 1000, matching v3.h and official generate()
max_new_tokens. Discover live GGUF keys and tensor rows. This tree:
chatterbox.n_ctx 3086, perceiver_len 32, cond_prompt_length 150,
text_pos_emb 1024 by 2050, speech_pos_emb 1024 by 4100. Official V3 is
Llama 520M: 30 layers, hidden 1024, text vocab 2454, speech vocab 8194,
perceiver on, emotion_adv on. v3.h has no N_CTX constant. mtl_bpe prepends
[lang] and [SPACE]. Engine wraps start_text plus BPE plus stop_text.
text_pos indices are 0 through n_text_tokens minus 1 into text_pos_emb,
so that vector must fit 2050 rows. prompt_len is 1 plus perceiver_len
plus 1 plus n_text_tokens plus 2. Engine throws T3 prompt exceeds context
if prompt_len is greater than n_ctx. Generation stops at N_PREDICT 1000
or when n_past plus 1 is greater than n_ctx. Official T3Config
max_text_tokens 2048 and max_speech_tokens 4096 are Python config. C++
does not store those keys. generate() does not use 4096. Do not fill
speech_pos 4100. Dump path is v3_t3_dump.txt beside the T3 GGUF. Split
with ||| only for same-language separate utterances, not to fake a
length limit.

Delimiter: join pieces of the SAME language with a line that is only |||.
utterances() splits on that, strips, skips empty, then speak() each piece.
speak() fails if a piece still contains |||. The C++ engine must never see
||| and must not chunk on punctuation. The named pipe receives one clean
utterance: raw text for that launch language.

No Nano or Turbo paralinguistic tags. No [laugh] [chuckle] [happy]. V3
emotion is baked builtin_emotion_adv plus live cfg-weight. Do not invent
an exaggeration env knob. Do not add SILENCE_COUNT. Engine drops invalid
tokens then trims the last speech token of PCM.

Empty models/v3.knobs values mean header defaults: SEED 42, N_PREDICT 1000,
TOP_K 0, TOP_P 1.0, MIN_P 0.05, TEMPERATURE 0.8, REPEAT_PENALTY 1.2,
REPEAT_LAST_N 1000, CFG_WEIGHT 0.5, CFM_STEPS 10, CFM_CFG 0.7,
SILENCE_TOKEN 4299. CFM_STEPS 10 is standard S3Gen, not turbo meanflow.
CFG_BATCH is 2. Launcher flags wrap getenv only: --repeat-penalty
--temperature --top-k --top-p --repeat-last-n --seed --n-predict --cfm-steps
--silence-token --min-p --cfg-weight --cfm-cfg. Do not invent C++ argv knobs.

Run from Trident with sibling chatterbox.cpp:
python tts_v3.py "<text>" <language>
GGUF names chatterbox-t3-v3-q8_0.gguf and chatterbox-s3gen-v3-q4_0.gguf.
Kill via models/v3.pid. Pipe is \\\\.\\pipe\\chatterbox-v3-<tag>. Server argv
is chatterbox-server.exe t3 s3 pipe language. argc less than 5 is fatal.
Speaker is operator reference.wav, 16-bit PCM mono 24000 Hz. After each
successful speak this launcher prints wall_s duration_s rtf on stderr, plays
the WAV with the Windows associated player, waits duration plus one second,
then prints the WAV path. Success is WAV Length greater than 44. Synth RTF
is speak wall-clock after the pipe is ready, divided by WAV duration.
"""


def usage(cfg: Variant):
    flags = " ".join(f"[--{n} <v>]" for n in cfg.knobs)
    if cfg.needs_language:
        head = f"usage: python tts_{cfg.name}.py [-h] {flags} <text> <language>"
    else:
        head = f"usage: python tts_{cfg.name}.py [-h] {flags} <text>"
    if cfg.name == "nano":
        return head + NANO_HELP
    if cfg.name == "turbo":
        return head + TURBO_HELP
    if cfg.name == "v3":
        return head + V3_HELP
    return head


def parse_variant_args(cfg: Variant, argv: list[str]) -> tuple[str, str | None, dict[str, str]]:
    args = argv[1:]
    cli: dict[str, str] = {}
    i = 0
    allowed = set(cfg.knobs)
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help", "-?"):
            print(usage(cfg))
            raise SystemExit(0)
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
    pieces = utterances(text)
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
    for i, piece in enumerate(pieces):
        stamp_t = time.strftime("%Y%m%d-%H%M%S")
        name = f"{stamp_t}-{cfg.name}.wav" if len(pieces) == 1 else f"{stamp_t}-{cfg.name}-{i}.wav"
        out = ROOT / name
        wall = speak(pipe, pid, out, piece)
        dur = wav_duration_s(out)
        rtf = wall / dur if dur > 0 else 0.0
        print(f"wall_s={wall:.3f} duration_s={dur:.3f} rtf={rtf:.3f}", file=sys.stderr)
        play_wav(out, dur)
        print(out)
