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
GGML, RUNTIMES, CONVERTER = CHATTERBOX / "ggml", TOOLS / "runtime", TOOLS / "convert"
CHATTERBOX_URL, CHATTERBOX_REV = "https://github.com/wgabrys88/chatterbox.cpp", "77e0bfee1429a328cdbf2de7879f58182bbe115d"
GGML_GIT = ("https://github.com/ggml-org/ggml.git", "58c3805840b516b2a88ff867ccf7bb41dba79951")
TTS_RATE = 24000
T3_FILE = "chatterbox-t3-nano-q4_0.gguf"
TTS_KNOBS = {
    "gpu_layers": 99, "context": 2048, "threads": 4, "fastconv": 1, "seed": 42, "max_tokens": 1000,
    "top_k": 1000, "top_p": .95, "min_p": .05, "temperature": .8, "repeat_penalty": 1.2,
    "cfm_steps": 2, "cfg_weight": .5, "exaggeration": .5,
}
TTS_PROFILES = {"nano": TTS_KNOBS}
VOICE_DEFAULT = "trump"
VOICE_FILE = "ref-trump.wav"
VOICE_URL = "https://huggingface.co/datasets/sdialog/voices-celebrities/resolve/57746b866d470be717097b87ba0428f8dd73e4f4/audio/donald-trump.wav"
VOICE_SIZE, VOICE_SHA256 = 4210766, "9d8b44d73192e9c04dd241f16177e4c5753bcefadde69e6e24b45e278b821f8c"
PORTS = {"chatterbox": 17933}


def ensure_venv(script=None) -> None:
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    here = Path(script or sys.argv[0]).resolve()
    if sys.platform.startswith("win") and venv.is_file() and Path(sys.executable).resolve() != venv.resolve():
        os.execv(str(venv), [str(venv), str(here), *sys.argv[1:]])


def detect_hardware() -> tuple[str, str | None, str]:
    if not sys.platform.startswith("win"):
        raise RuntimeError("Trident requires Windows")
    run = lambda cmd: subprocess.check_output(cmd, text=True, encoding="utf-8", errors="replace", timeout=15)
    try:
        for row in run(["nvidia-smi", "--query-gpu=name,compute_cap", "--format=csv,noheader,nounits"]).splitlines():
            name, _, cc = row.rpartition(",")
            cc = cc.strip()
            if cc in {"6.0", "6.1", "6.2"}:
                return "pascal", cc.replace(".", ""), name.strip()
    except Exception:
        pass
    gpu = run(["powershell.exe", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name -join ';'"]).strip()
    lower = gpu.casefold()
    for tag, names in (("60", ("tesla p100", "quadro gp100")), ("61", ("gtx 1050", "gtx 1060", "gtx 1070", "gtx 1080", "titan x (pascal)", "titan xp", "quadro p", "tesla p4", "tesla p40"))):
        if any(n in lower for n in names):
            return "pascal", tag, gpu
    if "iris" in lower and "xe" in lower:
        return "irisxe", None, gpu
    raise RuntimeError(f"unsupported GPU: {gpu}")


HARDWARE, _, GPU_NAME = detect_hardware()
TTS_BACKEND = "vulkan"
VULKAN_ENV = {"GGML_VK_DISABLE_F16": "1"} if HARDWARE == "pascal" else {}
FLASH_ATTN = "on" if HARDWARE == "pascal" else "off"
CODEC_QUANT, CODEC_FILE = (("q4_0", "chatterbox-s3gen-nano-irisxe-q4_0-rawf32-v1.gguf") if HARDWARE == "irisxe" else ("f16", "chatterbox-s3gen-nano-f16.gguf"))
TTS_MODELS = {"nano": (T3_FILE, CODEC_FILE)}
TTS_NANO_SPEC = {"repo": "ResembleAI/chatterbox-nano", "rev": "71ccd1d0081b430592cea481f4307e764e07bc64",
                 "ckpt": "ckpt", "t3": "convert-t3-turbo-to-gguf.py", "model": "nano", "s3": "turbo",
                 "files": ("t3_nano_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt", "ve.safetensors",
                           "vocab.json", "merges.txt", "added_tokens.json")}


def find_exe(root: Path, name: str) -> Path | None:
    return next((p for p in root.rglob(name) if p.is_file()), None) if root.is_dir() else None


def voice_wav(data_dir: Path) -> Path:
    candidate = (Path(data_dir) / VOICE_FILE).resolve()
    if not candidate.is_file():
        raise RuntimeError(f"reference voice missing: {candidate}")
    return candidate


class Paths:
    def __init__(self, models_dir=None, data_dir=None, command="install", family="nano", language="en", console=False) -> None:
        self.models_dir = Path(models_dir or MODELS).resolve()
        self.data_dir = Path(data_dir or DATA).resolve()
        self.command, self.family, self.language = command, family.strip().lower(), language.strip().lower()
        self.voice = VOICE_DEFAULT
        bits = [datetime.now().strftime("%Y%m%d-%H%M%S-%f"), command, HARDWARE]
        if command != "install":
            bits += [self.family, self.language, self.voice]
        self.stamp, self.run_dir = bits[0], self.data_dir / "runs" / "-".join(bits)
        self.run_dir.mkdir(parents=True)
        self.journal = Journal(self.run_dir, console)
        self.supervisor = WorkerSupervisor(self.journal)
        print(f"trident.run {self.run_dir}", flush=True)

    def close(self) -> None:
        self.journal.close()
