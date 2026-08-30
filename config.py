from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS, DATA, THIRD_PARTY, TOOLS, TTS = (ROOT / n for n in ("models", "data", "third_party", "tools", "tts"))
CHATTERBOX = THIRD_PARTY / "chatterbox.cpp"
GGML, RUNTIMES, CONVERTER = CHATTERBOX / "ggml", TOOLS / "runtime", TOOLS / "convert"
CHATTERBOX_URL, CHATTERBOX_REV = "https://github.com/wgabrys88/chatterbox.cpp", "892b020698205f884f6d198d2344a34e5d05a86e"
GGML_GIT = ("https://github.com/ggml-org/ggml.git", "58c3805840b516b2a88ff867ccf7bb41dba79951")
ASR_RATE, TTS_RATE, VAD_FRAME = 16000, 24000, 512
T3_FILE, PARAKEET_FILE, GEMMA_FILE = "chatterbox-t3-nano-q4_0.gguf", "tdt-0.6b-v3-q4_k.gguf", "gemma-4-E2B_q4_0-it.gguf"
PARAKEET_URL = "https://huggingface.co/mudler/parakeet-cpp-gguf/resolve/bf0af9f425fa01809cadec671b3cb672709d13e9/" + PARAKEET_FILE
GEMMA_URL = "https://huggingface.co/google/gemma-4-E2B-it-qat-q4_0-gguf/resolve/675cff42a74c774d6cb76f76d8eacb49b48c9b93/" + GEMMA_FILE
SMART_TURN_FILE = "smart-turn-v3.2-cpu.onnx"
SMART_TURN_URL = "https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/f766f81d3cfdf7737ac64aad813d91bbfd56bf93/" + SMART_TURN_FILE
SMART_TURN_SIZE = 8679182
SMART_TURN_SHA256 = "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f"
PARAKEET_ZIP = ("https://github.com/mudler/parakeet.cpp/releases/download/v0.5.0/parakeet-v0.5.0-bin-win-vulkan-x64.zip", "parakeet-v0.5.0-bin-win-vulkan-x64.zip")
LLAMA_ZIP = ("https://github.com/ggml-org/llama.cpp/releases/download/b10453/llama-b10453-bin-win-vulkan-x64.zip", "llama-b10453-bin-win-vulkan-x64.zip")
VOICES = {"trump": ("audio/donald-trump.wav", "ref-trump.wav"), "obama": ("audio/barack-obama.wav", "ref-obama.wav"), "kamala": ("audio/kamala_harris.wav", "ref-kamala.wav")}
VOICE_HF = "https://huggingface.co/datasets/sdialog/voices-celebrities/resolve/57746b866d470be717097b87ba0428f8dd73e4f4/"
PORTS = {"parakeet": 17931, "gemma": 17932, "chatterbox": 17933}
PROMPT = ("You are the mind of this spoken conversation. Remember what was already said and use it. If the user has not finished a request or thought, or ASR is an incomplete fragment, output nothing. "
          "When a spoken reply is needed now, answer as a capable partner: useful, direct, specific. Do not narrate that you are thinking or preparing speech. Speak English. If the user used another language, keep their meaning and answer in English. "
          "Output only words to be read aloud. Short sentences, each ending with a period, question mark, or exclamation mark. "
          "No markdown, lists, code, URLs, emoji, stage directions, or square-bracket tags. Expand numbers and abbreviations. Do not mention transcription, models, prompts, or reasoning.")
TTS_KNOBS = {
    "gpu_layers": 99,
    "context": 2048,
    "threads": 4,
    "fastconv": 1,
    "seed": 42,
    "max_tokens": 1000,
    "top_k": 1000,
    "top_p": .95,
    "min_p": .05,
    "temperature": .8,
    "repeat_penalty": 1.2,
    "cfm_steps": 1,
    "cfg_weight": .5,
    "exaggeration": .5,
    "chars": 180,
}
TTS_PROFILES = {
    "nano": TTS_KNOBS,
    "turbo": TTS_KNOBS,
    "v3": {**TTS_KNOBS, "top_k": 0, "top_p": 1., "cfm_steps": 0},
}
V3_LANGUAGES = ("ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it", "ja", "ko", "ms", "nl", "no", "pl", "pt", "ru", "sv", "sw", "tr", "zh")
GEMMA_GEN = {"temperature": 1., "top_p": .95, "top_k": 64, "min_p": 0., "repeat_penalty": 1., "seed": 42, "max_tokens": 1024}
CONSOLE = False
LOG_FILE: Path | None = None
RUN_DIR: Path | None = None
RUN_PREFIX = ""
_log_lock = threading.Lock()
_log_sequence = 0
_started_ns = time.perf_counter_ns()
_failures: queue.SimpleQueue = queue.SimpleQueue()
_failed = threading.Event()

def detect_hardware() -> str:
    if not sys.platform.startswith("win"):
        raise RuntimeError("Trident requires Windows")
    gpu = subprocess.check_output(["powershell.exe", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name -join ';'"], text=True, encoding="utf-8", errors="replace", timeout=15).lower()
    if any(n in gpu for n in ("gtx 1050", "gtx 1060", "gtx 1070", "gtx 1080", "titan x (pascal)", "titan xp", "quadro p")):
        return "pascal"
    if "iris" in gpu and "xe" in gpu:
        return "irisxe"
    raise RuntimeError(f"unsupported GPU: {gpu.strip()}")

HARDWARE = detect_hardware()
VULKAN_ENV = {"GGML_VK_DISABLE_F16": "1"} if HARDWARE == "pascal" else {}
FLASH_ATTN = "on" if HARDWARE == "pascal" else "off"
CODEC_QUANT, CODEC_FILE = (("q4_0", "chatterbox-s3gen-nano-irisxe-q4_0-rawf32-v1.gguf") if HARDWARE == "irisxe" else ("f16", "chatterbox-s3gen-nano-f16.gguf"))
TTS_MODELS = {
    "nano": (T3_FILE, CODEC_FILE),
    "turbo": ("chatterbox-t3-turbo-q4_0.gguf", f"chatterbox-s3gen-turbo-{CODEC_QUANT}.gguf"),
    "v3": ("chatterbox-t3-mtl-v3-cangjie-q4_0.gguf", f"chatterbox-s3gen-mtl-v3-{CODEC_QUANT}.gguf"),
}
TTS_WEIGHTS = {
    "nano": {"repo": "ResembleAI/chatterbox-nano", "rev": "71ccd1d0081b430592cea481f4307e764e07bc64", "ckpt": "ckpt", "t3": "convert-t3-turbo-to-gguf.py", "model": "nano", "s3": "turbo",
             "files": ("t3_nano_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt", "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json")},
    "turbo": {"repo": "ResembleAI/chatterbox-turbo", "rev": "749d1c1a46eb10492095d68fbcf55691ccf137cd", "ckpt": "ckpt-turbo", "t3": "convert-t3-turbo-to-gguf.py", "model": "turbo", "s3": "turbo",
              "files": ("t3_turbo_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt", "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json")},
    "v3": {"repo": "ResembleAI/chatterbox", "rev": "ef85ce7bef2f3f1a74d0d837d379d2fcb68203cd", "ckpt": "ckpt-v3", "t3": "convert-t3-mtl-to-gguf.py", "s3": "mtl",
           "files": ("t3_mtl23ls_v3.safetensors", "s3gen.pt", "ve.pt", "conds.pt", "grapheme_mtl_merged_expanded_v1.json", "Cangjie5_TC.json")},
}

def git_sha(path: Path) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, timeout=15).strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""

def run_file(role: str, ext: str = "log") -> Path:
    if RUN_DIR is None:
        raise RuntimeError("run log is not initialized")
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in role)
    return RUN_DIR / f"{RUN_PREFIX}-{safe}.{ext}"

def emit(event: str, **fields) -> None:
    global _log_sequence
    with _log_lock:
        _log_sequence += 1
        line = json.dumps({"sequence": _log_sequence, "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"), "mono_ms": round((time.perf_counter_ns() - _started_ns) / 1e6, 3), "event": event, **fields}, ensure_ascii=False, separators=(",", ":"), default=str)
        if CONSOLE:
            print(line, flush=True)
        if LOG_FILE is not None:
            with LOG_FILE.open("a", encoding="utf-8", newline="\n") as h:
                h.write(line + "\n")

def transcript(role: str, text: str) -> None:
    with _log_lock, run_file(f"transcript-{role}", "txt").open("a", encoding="utf-8", newline="\n") as h: h.write(text); h.flush()

def _thread_failed(args) -> None:
    _failures.put(args)
    _failed.set()
    emit("failure", producer=args.thread.name, type=args.exc_type.__name__, error=str(args.exc_value))

threading.excepthook = _thread_failed

def raise_worker_failure() -> None:
    if _failed.is_set():
        args = _failures.get()
        raise args.exc_value.with_traceback(args.exc_traceback)

def wait_workers(seconds: float) -> None:
    _failed.wait(seconds)
    raise_worker_failure()

def sidecar(role: str, ext: str = "log") -> Path:
    return run_file(role, ext)

def find_exe(root: Path, name: str) -> Path | None:
    return next((p for p in root.rglob(name) if p.is_file()), None) if root.is_dir() else None

def load_settings(data_dir: Path) -> dict:
    path = Path(data_dir) / "live-settings.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"system_prompt": PROMPT, "tts_voice": "trump", "candidate_silence_ms": 600, "completion_threshold": .5, "acoustic_context_seconds": 8}

def voice_wav(data_dir: Path, value: str | None = None) -> Path:
    raw = (value or "trump").strip() or "trump"
    key = raw.lower()
    path = Path(data_dir) / (VOICES[key][1] if key in VOICES else f"voices/{key}.wav")
    if path.is_file():
        return path.resolve()
    path = Path(raw).expanduser()
    if path.is_file():
        return path.resolve()
    raise RuntimeError(f"unknown voice {raw!r}")

class Paths:
    def __init__(self, models_dir=None, data_dir=None, command="install", family="nano", language="en", console=False) -> None:
        global LOG_FILE, RUN_DIR, RUN_PREFIX, CONSOLE, _log_sequence, _started_ns
        self.models_dir, self.data_dir = Path(models_dir or MODELS).resolve(), Path(data_dir or DATA).resolve()
        self.command, self.family, self.language = command, family.strip().lower(), language.strip().lower()
        self.voice = str(load_settings(self.data_dir).get("tts_voice") or "trump")
        self.stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        bits = [self.stamp, command, HARDWARE]
        if command == "talk":
            bits += [self.family, self.language, self.voice]
        RUN_PREFIX = "-".join(bits)
        self.run_dir = self.data_dir / "runs" / RUN_PREFIX
        self.run_dir.mkdir(parents=True)
        RUN_DIR, CONSOLE = self.run_dir, bool(console)
        self.log = run_file("events", "jsonl")
        LOG_FILE = self.log
        _log_sequence = 0
        _started_ns = time.perf_counter_ns()
        run_file("run", "json").write_text(json.dumps({"stamp": self.stamp, "command": command, "hardware": HARDWARE, "family": self.family, "language": self.language, "voice": self.voice, "console": CONSOLE, "trident_sha": git_sha(ROOT), "chatterbox_rev": CHATTERBOX_REV}, indent=2) + "\n", encoding="utf-8")
        print(f"trident.run {self.run_dir}", flush=True)
