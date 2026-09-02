import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from journal import Journal, WorkerSupervisor

ROOT = Path(__file__).resolve().parent
MODELS, DATA, THIRD_PARTY, TOOLS = (ROOT / n for n in ("models", "data", "third_party", "tools"))
CHATTERBOX = THIRD_PARTY / "chatterbox.cpp"
PARAKEET = THIRD_PARTY / "parakeet.cpp"
GGML, CONVERTER = CHATTERBOX / "ggml", TOOLS / "convert"
CHATTERBOX_URL, CHATTERBOX_REV = "https://github.com/wgabrys88/chatterbox.cpp", "e61ff8f09029df2f7a721bfaf972409778c6f00d"
PARAKEET_GIT_URL, PARAKEET_REV = "https://github.com/mudler/parakeet.cpp", "1bfbebfaaf493866f49597cd3b7901959d395c60"
GGML_GIT = ("https://github.com/ggml-org/ggml.git", "58c3805840b516b2a88ff867ccf7bb41dba79951")
ASR_RATE, TTS_RATE, VAD_FRAME = 16000, 24000, 512
SKIP_DEVICES = {"input": "CABLE Output (VB-Audio Virtual Cable)", "output": "CABLE Input (VB-Audio Virtual Cable)"}
T3_FILE, PARAKEET_FILE, GEMMA_FILE = "chatterbox-t3-nano-q4_0.gguf", "nemotron-3.5-asr-streaming-0.6b-q4_k.gguf", "gemma-4-E2B_q4_0-it.gguf"
PARAKEET_URL = "https://huggingface.co/mudler/parakeet-cpp-gguf/resolve/bf0af9f425fa01809cadec671b3cb672709d13e9/" + PARAKEET_FILE
GEMMA_URL = "https://huggingface.co/google/gemma-4-E2B-it-qat-q4_0-gguf/resolve/675cff42a74c774d6cb76f76d8eacb49b48c9b93/" + GEMMA_FILE
SMART_TURN_FILE = "smart-turn-v3.2-cpu.onnx"
SMART_TURN_URL = "https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/f766f81d3cfdf7737ac64aad813d91bbfd56bf93/" + SMART_TURN_FILE
LLAMA_ZIP = ("https://github.com/ggml-org/llama.cpp/releases/download/b10741/llama-b10741-bin-win-vulkan-x64.zip", "llama-b10741-bin-win-vulkan-x64.zip")
VOICES = {
    "trump": ("audio/donald-trump.wav", "ref-trump.wav"),
    "obama": ("audio/barack-obama.wav", "ref-obama.wav"),
    "kamala": ("audio/kamala_harris.wav", "ref-kamala.wav"),
}
VOICE_HF = "https://huggingface.co/datasets/sdialog/voices-celebrities/resolve/57746b866d470be717097b87ba0428f8dd73e4f4/"
PORTS = {"gemma": 17932, "chatterbox": 17933}
PROMPT = "You are a capable general-purpose assistant. Answer the user's request directly, accurately, and naturally. Use the available conversation context when it is relevant."
SPOKEN_PROMPT = "This is a spoken conversation. Prefer concise natural sentences and plain text unless the user explicitly asks for formatting, code, links, or another non-spoken form."
TTS_KNOBS = {
    "gpu_layers": 99, "context": 2048, "threads": 4, "fastconv": 1, "seed": 42, "max_tokens": 1000,
    "top_k": 1000, "top_p": .95, "min_p": .05, "temperature": .8, "repeat_penalty": 1.2,
    "cfm_steps": 2, "cfg_weight": .5, "exaggeration": .5,
}
TTS_PROFILES = {"nano": dict(TTS_KNOBS), "turbo": dict(TTS_KNOBS), "v3": {**TTS_KNOBS, "top_k": 0, "top_p": 1., "cfm_steps": 5}}
LANGUAGE_NAMES = {"ar":"Arabic","da":"Danish","de":"German","el":"Greek","en":"English","es":"Spanish","fi":"Finnish","fr":"French","he":"Hebrew","hi":"Hindi","it":"Italian","ja":"Japanese","ko":"Korean","ms":"Malay","nl":"Dutch","no":"Norwegian","pl":"Polish","pt":"Portuguese","ru":"Russian","sv":"Swedish","sw":"Swahili","tr":"Turkish","zh":"Chinese"}
V3_LANGUAGES = tuple(LANGUAGE_NAMES)

ASR_LOCALES = {
    "ar":"ar-AR", "da":"da-DK", "de":"de-DE", "el":"el-GR", "en":"en-US", "es":"es-ES",
    "fi":"fi-FI", "fr":"fr-FR", "he":"he-IL", "hi":"hi-IN", "it":"it-IT", "ja":"ja-JP",
    "ko":"ko-KR", "nl":"nl-NL", "no":"nb-NO", "pl":"pl-PL", "pt":"pt-PT", "ru":"ru-RU",
    "sv":"sv-SE", "tr":"tr-TR", "zh":"zh-CN",
}
GEMMA_CONTEXT = 16384
GEMMA_GEN = {"temperature": 1., "top_p": .95, "top_k": 64, "min_p": 0., "repeat_penalty": 1., "seed": 42, "max_tokens": 2048}
GEMMA_RUNTIME = {"device": "Vulkan0", "gpu_layers": "all", "context": GEMMA_CONTEXT, "parallel": 1, "threads": 2, "threads_batch": 2,
                 "cache_type_k": "f16", "cache_type_v": "f16", "cache_ram": 512, "ctx_checkpoints": 8,
                 "checkpoint_min_step": 256, "batch_size": 2048, "ubatch_size": 512}
SERVICES = {"talk": ("gemma", "chatterbox"), "tts": ("chatterbox",), "asr": (), "generation": ("gemma",)}


def ensure_venv(script=None) -> None:
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    here = Path(script or sys.argv[0]).resolve()
    if sys.platform.startswith("win") and venv.is_file() and Path(sys.executable).resolve() != venv.resolve():
        os.execv(str(venv), [str(venv), str(here), *sys.argv[1:]])


def detect_hardware() -> tuple[str, str | None, str]:
    if not sys.platform.startswith("win"): raise RuntimeError("Trident requires Windows")
    run = lambda cmd: subprocess.check_output(cmd, text=True, encoding="utf-8", errors="replace", timeout=15)
    try:
        for row in run(["nvidia-smi", "--query-gpu=name,compute_cap", "--format=csv,noheader,nounits"]).splitlines():
            name, _, cc = row.rpartition(","); cc = cc.strip()
            if cc in {"6.0", "6.1", "6.2"}: return "pascal", cc.replace(".", ""), name.strip()
    except Exception: pass
    gpu = run(["powershell.exe", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name -join ';'"]).strip()
    lower = gpu.casefold()
    for tag, names in (("60", ("tesla p100", "quadro gp100")), ("61", ("gtx 1050", "gtx 1060", "gtx 1070", "gtx 1080", "titan x (pascal)", "titan xp", "quadro p", "tesla p4", "tesla p40"))):
        if any(n in lower for n in names): return "pascal", tag, gpu
    if "iris" in lower and "xe" in lower: return "irisxe", None, gpu
    raise RuntimeError(f"unsupported GPU: {gpu}")

HARDWARE, _, GPU_NAME = detect_hardware()
TTS_BACKEND = "vulkan"
VULKAN_ENV = {"GGML_VK_DISABLE_F16": "1"} if HARDWARE == "pascal" else {}
FLASH_ATTN = "on" if HARDWARE == "pascal" else "off"
if HARDWARE == "irisxe": TTS_PROFILES["nano"]["cfm_steps"] = 1
CODEC_QUANT = "q4_0"
CODEC_FILE = "chatterbox-s3gen-nano-irisxe-q4_0-rawf32-v1.gguf" if HARDWARE == "irisxe" else "chatterbox-s3gen-nano-q4_0.gguf"
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

def find_exe(root: Path, name: str) -> Path | None:
    return next((p for p in root.rglob(name) if p.is_file()), None) if root.is_dir() else None

def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()

def git_identity(path: Path) -> dict:
    try:
        run = lambda *a: subprocess.check_output(["git", "-C", str(path), *a], text=True, stderr=subprocess.DEVNULL, timeout=15).strip()
        return {"sha": run("rev-parse", "HEAD"), "branch": run("branch", "--show-current"),
                "dirty": bool(run("status", "--porcelain", "--untracked-files=no"))}
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return {"sha": "", "branch": "", "dirty": None}

def system_prompt(language: str, base: str | None = None, spoken: bool = False) -> str:
    language = language.strip().lower()
    prompt = (base or PROMPT).rstrip()
    if spoken: prompt += " " + SPOKEN_PROMPT
    return prompt + f" Answer in {LANGUAGE_NAMES.get(language, language)} unless the user explicitly requests another language."

DEFAULT_SETTINGS = {
    "system_prompt": PROMPT, "tts_voice": "trump", "candidate_silence_ms": 600, "completion_threshold": .5,
    "acoustic_context_seconds": 8, "asr_device": "", "prefill_min_words": 2, "history_mode": "conversation", "history_turns": 16,
    "tools_enabled": False, "thinking": "", "thinking_budget": -1,
}

def load_settings(data_dir: Path) -> dict:
    path = Path(data_dir) / "live-settings.json"
    if not path.is_file(): return dict(DEFAULT_SETTINGS)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict): raise RuntimeError(f"live settings must be a JSON object: {path}")
    return {**DEFAULT_SETTINGS, **loaded}

def voice_wav(models_dir: Path, value: str | None = None) -> Path:
    raw = (value or "trump").strip() or "trump"
    name = VOICES[raw.lower()][1] if raw.lower() in VOICES else f"{raw.lower()}.wav"
    for path in (Path(models_dir) / "voices" / name, Path(raw).expanduser()):
        if path.is_file(): return path.resolve()
    raise RuntimeError(f"unknown voice {raw!r}")

def wasapi_device(kind: str) -> tuple[int, dict, dict]:
    import sounddevice as sd
    skip, hostapis, devices = SKIP_DEVICES[kind], sd.query_hostapis(), sd.query_devices()
    host_index = next((i for i, api in enumerate(hostapis) if "wasapi" in str(api["name"]).casefold()), None)
    if host_index is None: raise RuntimeError("Windows WASAPI host API is unavailable")
    api, key = hostapis[host_index], "max_input_channels" if kind == "input" else "max_output_channels"
    matches = [(i, d) for i, d in enumerate(devices) if int(d["hostapi"]) == host_index and int(d[key]) >= 1]
    if not matches: raise RuntimeError(f"no WASAPI {kind} endpoint is available")
    pool = [(i, d) for i, d in matches if str(d["name"]) != skip]
    if not pool: raise RuntimeError(f"no WASAPI {kind} endpoint remains after skipping {skip}")
    want = int(api["default_input_device" if kind == "input" else "default_output_device"])
    index, device = next(((i, d) for i, d in pool if i == want), pool[0])
    return index, dict(device), dict(api)


class Paths:
    def __init__(self, models_dir=None, data_dir=None, command="install", family="nano", language="en", console=False) -> None:
        self.models_dir, self.data_dir = Path(models_dir or MODELS).resolve(), Path(data_dir or DATA).resolve()
        self.command, self.family, self.language = command, family.strip().lower(), language.strip().lower()
        self.voice = str(load_settings(self.data_dir).get("tts_voice") or "trump")
        self.tts_knobs = self.gemma_runtime = self.gemma_gen = None
        self.thinking = self.thinking_budget = self.history_mode = self.history_turns = None
        self.tools_enabled = self.system_prompt = self.asr_device = self.prefill_min_words = None
        bits = [datetime.now().strftime("%Y%m%d-%H%M%S-%f"), command, HARDWARE] + ([self.family, self.language, self.voice] if command != "install" else [])
        self.stamp, self.run_dir = bits[0], self.data_dir / "runs" / "-".join(bits)
        self.run_dir.mkdir(parents=True)
        self.journal = Journal(self.run_dir, console)
        self.supervisor = WorkerSupervisor(self.journal)
        print(f"trident.run {self.run_dir}", flush=True)

    def close(self) -> None:
        self.journal.close()


class Wasapi:
    kind, component, ready_event, stop_event, rate_key, peer_rate = "output", "playback", "sink.ready", "sink.stopped", "render_rate", TTS_RATE

    def __init__(self, paths: Paths) -> None:
        self.paths = paths; self.stream = self.error = None

    def check(self) -> None:
        if self.error is not None: raise self.error

    def close(self) -> None:
        if self.stream is not None:
            self.stream.stop(); self.stream.close(); self.stream = None
        self.paths.journal.emit(self.component, self.stop_event, type="wasapi")

    def open(self) -> None:
        import sounddevice as sd
        index, device, host = wasapi_device(self.kind)
        extra = sd.WasapiSettings(exclusive=False, auto_convert=True, explicit_sample_format=True)
        (sd.check_output_settings if self.kind == "output" else sd.check_input_settings)(
            device=index, channels=1, dtype="float32", samplerate=self.peer_rate, extra_settings=extra)
        cls = sd.RawOutputStream if self.kind == "output" else sd.RawInputStream
        self.stream = cls(samplerate=self.peer_rate, blocksize=0, device=index, channels=1, dtype="float32",
                          latency="low", extra_settings=extra, callback=self._callback)
        self.stream.start()
        self.paths.journal.emit(self.component, self.ready_event, type="wasapi", device=device["name"], host_api=host["name"],
            channels=1, native_rate=self.peer_rate, auto_convert=True, negotiated_latency=self.stream.latency,
            **{self.rate_key: self.peer_rate})
