from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from journal import Journal, WorkerSupervisor, git_identity

ROOT = Path(__file__).resolve().parent
MODELS, DATA, THIRD_PARTY, TOOLS, TTS = (ROOT / n for n in ("models", "data", "third_party", "tools", "tts"))
CHATTERBOX = THIRD_PARTY / "chatterbox.cpp"
GGML, RUNTIMES, CONVERTER = CHATTERBOX / "ggml", TOOLS / "runtime", TOOLS / "convert"
CHATTERBOX_URL, CHATTERBOX_REV = "https://github.com/wgabrys88/chatterbox.cpp", "ffd78fb72b7943199d1ea6ea5b43d808c1a2f4a6"
GGML_GIT = ("https://github.com/ggml-org/ggml.git", "58c3805840b516b2a88ff867ccf7bb41dba79951")
ASR_RATE, TTS_RATE, VAD_FRAME = 16000, 24000, 512
CABLE_RATE, CABLE_CHANNELS = 48000, 2
CABLE_DEVICES = {"input": "CABLE Output (VB-Audio Virtual Cable)", "output": "CABLE Input (VB-Audio Virtual Cable)"}
T3_FILE, PARAKEET_FILE, GEMMA_FILE = "chatterbox-t3-nano-q4_0.gguf", "tdt-0.6b-v3-q4_k.gguf", "gemma-4-E2B_q4_0-it.gguf"
PARAKEET_URL = "https://huggingface.co/mudler/parakeet-cpp-gguf/resolve/bf0af9f425fa01809cadec671b3cb672709d13e9/" + PARAKEET_FILE
PARAKEET_SIZE, PARAKEET_SHA256 = 675200864, "993d73feb4206dadda865ab25bd64b50c48dc4d013c3bf6126a721f28b1d5ee8"
GEMMA_URL = "https://huggingface.co/google/gemma-4-E2B-it-qat-q4_0-gguf/resolve/675cff42a74c774d6cb76f76d8eacb49b48c9b93/" + GEMMA_FILE
GEMMA_SIZE, GEMMA_SHA256 = 3349516256, "fa401b55b07ee70a54c6dae3903c783a6e65064312529ea57175cb5f8dec6634"
SMART_TURN_FILE = "smart-turn-v3.2-cpu.onnx"
SMART_TURN_URL = "https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/f766f81d3cfdf7737ac64aad813d91bbfd56bf93/" + SMART_TURN_FILE
SMART_TURN_SIZE = 8679182
SMART_TURN_SHA256 = "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f"
PARAKEET_ZIP = ("https://github.com/mudler/parakeet.cpp/releases/download/v0.5.0/parakeet-v0.5.0-bin-win-vulkan-x64.zip", "parakeet-v0.5.0-bin-win-vulkan-x64.zip", 0, "717c416fab299755e8140137e3a0115121ce1acb6379d13c60f2f0613f6c13a3")
LLAMA_ZIP = ("https://github.com/ggml-org/llama.cpp/releases/download/b10621/llama-b10621-bin-win-vulkan-x64.zip", "llama-b10621-bin-win-vulkan-x64.zip", 0, "2672d85bf87c8280d94dee01eb6a86280046878f70a07d786a93637fa9081163")
VOICES = {
    "trump": ("audio/donald-trump.wav", "ref-trump.wav", 4210766, "9d8b44d73192e9c04dd241f16177e4c5753bcefadde69e6e24b45e278b821f8c"),
    "obama": ("audio/barack-obama.wav", "ref-obama.wav", 8454222, "42ba473919a79233690b60b3de56bb3eb0e6587173908a4b83841d30c18cdfc8"),
    "kamala": ("audio/kamala_harris.wav", "ref-kamala.wav", 7487566, "5dbec60bd5be09cb31436ca6652241aa97a05c8187efbfd02df0c45f5c7aa7ea"),
}
VOICE_HF = "https://huggingface.co/datasets/sdialog/voices-celebrities/resolve/57746b866d470be717097b87ba0428f8dd73e4f4/"
PORTS = {"parakeet": 17931, "gemma": 17932, "chatterbox": 17933}
PROMPT = ("You are the mind of this spoken conversation. Remember what was already said and use it. If the user has not finished a request or thought, or ASR is an incomplete fragment, output nothing. "
          "When a spoken reply is needed now, answer as a capable partner: useful, direct, specific. Do not narrate that you are thinking or preparing speech. "
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
}
TTS_PROFILES = {
    "nano": TTS_KNOBS,
    "turbo": TTS_KNOBS,
    "v3": {**TTS_KNOBS, "top_k": 0, "top_p": 1., "cfm_steps": 0},
}
V3_LANGUAGES = ("ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it", "ja", "ko", "ms", "nl", "no", "pl", "pt", "ru", "sv", "sw", "tr", "zh")
GEMMA_CONTEXT = 4096
GEMMA_GEN = {"temperature": 1., "top_p": .95, "top_k": 64, "min_p": 0., "repeat_penalty": 1., "seed": 42, "max_tokens": 1024}


def detect_hardware() -> tuple[str, str | None, str]:
    if not sys.platform.startswith("win"):
        raise RuntimeError("Trident requires Windows")
    try:
        rows = subprocess.check_output(["nvidia-smi", "--query-gpu=name,compute_cap", "--format=csv,noheader,nounits"], text=True, encoding="utf-8", errors="replace", timeout=15).splitlines()
        for row in rows:
            name, _, cc = row.rpartition(",")
            cc = cc.strip()
            if cc in {"6.0", "6.1", "6.2"}:
                return "pascal", cc.replace(".", ""), name.strip()
    except Exception:
        pass
    gpu = subprocess.check_output(["powershell.exe", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name -join ';'"], text=True, encoding="utf-8", errors="replace", timeout=15).strip()
    lower = gpu.casefold()
    if any(n in lower for n in ("tesla p100", "quadro gp100")):
        return "pascal", "60", gpu
    if any(n in lower for n in ("gtx 1050", "gtx 1060", "gtx 1070", "gtx 1080", "titan x (pascal)", "titan xp", "quadro p", "tesla p4", "tesla p40")):
        return "pascal", "61", gpu
    if "iris" in lower and "xe" in lower:
        return "irisxe", None, gpu
    raise RuntimeError(f"unsupported GPU: {gpu}")

HARDWARE, CUDA_ARCH, GPU_NAME = detect_hardware()
TTS_BACKEND = "vulkan"
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
    return str(git_identity(path).get("sha") or "")

def find_exe(root: Path, name: str) -> Path | None:
    return next((p for p in root.rglob(name) if p.is_file()), None) if root.is_dir() else None

LANGUAGE_NAMES = {"ar":"Arabic","da":"Danish","de":"German","el":"Greek","en":"English","es":"Spanish","fi":"Finnish","fr":"French","he":"Hebrew","hi":"Hindi","it":"Italian","ja":"Japanese","ko":"Korean","ms":"Malay","nl":"Dutch","no":"Norwegian","pl":"Polish","pt":"Portuguese","ru":"Russian","sv":"Swedish","sw":"Swahili","tr":"Turkish","zh":"Chinese"}

def system_prompt(language: str, base: str | None = None) -> str:
    language = language.strip().lower()
    name = LANGUAGE_NAMES.get(language, language)
    return (base or PROMPT).rstrip() + f" Speak and answer in {name}. Preserve the user's meaning if recognition contains another language."

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

def cable_device(kind: str) -> tuple[int, dict, dict]:
    import sounddevice as sd
    expected = CABLE_DEVICES[kind]
    hostapis = sd.query_hostapis(); devices = sd.query_devices()
    host_index = next((i for i, api in enumerate(hostapis) if "wasapi" in str(api["name"]).casefold()), None)
    if host_index is None: raise RuntimeError("Windows WASAPI host API is unavailable")
    channel_key = "max_input_channels" if kind == "input" else "max_output_channels"
    match = next(((i, d) for i, d in enumerate(devices)
                  if int(d["hostapi"]) == host_index and str(d["name"]) == expected), None)
    if match is None:
        raise RuntimeError(f"required WASAPI endpoint is missing: {expected}")
    index, device = match
    if int(device[channel_key]) < CABLE_CHANNELS:
        raise RuntimeError(f"{expected} does not expose {CABLE_CHANNELS} {kind} channels")
    return index, dict(device), dict(hostapis[host_index])


class Paths:
    def __init__(self, models_dir=None, data_dir=None, command="install", family="nano", language="en", console=False) -> None:
        self.models_dir, self.data_dir = Path(models_dir or MODELS).resolve(), Path(data_dir or DATA).resolve()
        self.command, self.family, self.language = command, family.strip().lower(), language.strip().lower()
        self.voice = str(load_settings(self.data_dir).get("tts_voice") or "trump")
        self.stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        bits = [self.stamp, command, HARDWARE]
        if command in ("talk", "tts"):
            bits += [self.family, self.language, self.voice]
        self.run_dir = self.data_dir / "runs" / "-".join(bits)
        self.run_dir.mkdir(parents=True)
        self.journal = Journal(self.run_dir, console)
        self.supervisor = WorkerSupervisor(self.journal)
        print(f"trident.run {self.run_dir}", flush=True)

    def close(self) -> None:
        self.journal.close()
