from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "workspace"
DONE = WORK / "done"
TURNS = WORK / "turns.jsonl"
LIVE = WORK / "live.txt"
MEMORY = WORK / "memory.md"
SAID = WORK / "said.txt"
STOP = WORK / "stop"
INBOX = WORK / "inbox"
CHUNK = 40
IDLE = 1800
MAX_LIVE = 32000
MAX_MEMORY = 8000
VULKAN_DEVICE = None

def bus() -> Path:
    WORK.mkdir(parents=True, exist_ok=True)
    DONE.mkdir(exist_ok=True)
    INBOX.mkdir(exist_ok=True)
    if not MEMORY.exists():
        MEMORY.write_text("", encoding="utf-8")
    return WORK

def put(path: Path, text: str) -> Path:
    bus()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path

def retire(path: Path, kind: str) -> Path:
    bus()
    folder = DONE / kind
    folder.mkdir(exist_ok=True)
    dest = folder / path.name
    n = 1
    while dest.exists():
        dest = folder / f"{path.stem}-{n}{path.suffix}"
        n += 1
    path.replace(dest)
    return dest

def take(path: Path, kind: str) -> str:
    text = path.read_text(encoding="utf-8-sig")
    retire(path, kind)
    return text

def next_path(prefix: str, suffix: str = ".txt") -> Path:
    n = 1
    folder = DONE / prefix
    while True:
        name = f"{prefix}-{n}{suffix}"
        if not (bus() / name).exists() and not (folder / name).exists():
            return bus() / name
        n += 1

def waiting(prefix: str) -> list[Path]:
    return sorted(bus().glob(f"{prefix}-*.txt"), key=lambda p: int(p.stem.split("-")[-1]))

def parts(text: str) -> list[str]:
    words = text.split()
    if len(words) <= CHUNK:
        return [text] if text.strip() else []
    out, start = [], 0
    while start < len(words):
        end = min(start + CHUNK, len(words))
        if end < len(words):
            for cut in range(end, start, -1):
                if words[cut - 1][-1:] in ".?!":
                    end = cut
                    break
        out.append(" ".join(words[start:end]))
        start = end
    return out

def _tail(path: Path, kind: str, cap: int) -> str:
    text = path.read_text(encoding="utf-8")
    if len(text) <= cap:
        return text
    head, tail = text[:len(text) - cap], text[-cap:]
    src = next_path(kind)
    put(src, head)
    retire(src, kind)
    put(path, tail)
    return tail

def read_live() -> str:
    bus()
    return _tail(LIVE, "live", MAX_LIVE)

def read_memory() -> str:
    bus()
    return _tail(MEMORY, "memory", MAX_MEMORY)

def append_live(text: str) -> None:
    bus()
    with LIVE.open("a", encoding="utf-8") as handle:
        handle.write(text.rstrip() + "\n")

def slot(path: Path, text: str) -> None:
    kind = {LIVE: "live", MEMORY: "memory", SAID: "said"}[path]
    if path.is_file() and path.stat().st_size:
        retire(path, kind)
    put(path, text)

def clear_live() -> None:
    slot(LIVE, "")

def ready(name: str) -> None:
    bus()
    folder = WORK / "ready"
    folder.mkdir(exist_ok=True)
    print("ready", flush=True)
    put(folder / name, "")

SPEAK = (
    "You are Jarvis. Your reply is the text to speak, in the language of the user line. "
    "Use a tool when the task needs it. Do not copy a leading time or language tag. "
    "If no reply is needed, write nothing."
)
TOOLS = [
    {"type": "function", "function": {
        "name": "python",
        "description": "Run one Python script in the workspace folder and return its exit code and output.",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "description": "One complete Python script."},
        }, "required": ["code"]}}},
    {"type": "function", "function": {
        "name": "remember",
        "description": "Append one fact to memory that should survive a restart.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "One fact."},
        }, "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "wake",
        "description": "Wake once after this many seconds, from 5 to 86400.",
        "parameters": {"type": "object", "properties": {
            "seconds": {"type": "string", "description": "Seconds until the wake."},
        }, "required": ["seconds"]}}},
    {"type": "function", "function": {
        "name": "stop",
        "description": "End the process after the spoken words have played.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
]

FLAGS = [
    {'name': 'reference', 'default': 'reference.wav', 'group': 'conversion', 'architecture': 'both'},
    {'name': 't3-weight-type', 'default': 'q4_0', 'group': 'conversion', 'architecture': 'both'},
    {'name': 's3-weight-type', 'default': 'q4_0', 'group': 'conversion', 'architecture': 'both'},
    {'name': 't3-quant-policy', 'default': 'scripts/quant_t3.json', 'group': 'conversion', 'architecture': 'both'},
    {'name': 's3-quant-policy', 'default': 'scripts/quant_s3.json', 'group': 'conversion', 'architecture': 'both'},
    {'name': 'seed', 'default': '42', 'group': 'server', 'architecture': 'both'},
    {'name': 'temperature', 'default': '0.8', 'group': 'server', 'architecture': 'both'},
    {'name': 'top-k', 'default': '1000', 'group': 'server', 'architecture': 'gpt2'},
    {'name': 'top-p', 'default': {'gpt2': '0.95', 'llama': '1.0'}, 'group': 'server', 'architecture': 'both'},
    {'name': 'repeat-penalty', 'default': {'gpt2': '1.2', 'llama': '1.2'}, 'group': 'server', 'architecture': 'both'},
    {'name': 'n-predict', 'default': '1000', 'group': 'server', 'architecture': 'both'},
    {'name': 'cfm-steps', 'default': {'gpt2': '2', 'llama': '10'}, 'group': 'server', 'architecture': 'both'},
    {'name': 'trim-fade-samples', 'default': '480', 'group': 'server', 'architecture': 'both'},
    {'name': 'min-p', 'default': '0.05', 'group': 'server', 'architecture': 'llama'},
    {'name': 'cfg-weight', 'default': '0.5', 'group': 'server', 'architecture': 'llama'},
    {'name': 'exaggeration', 'default': '0.5', 'group': 'server', 'architecture': 'llama'},
    {'name': 'cfm-cfg', 'default': '0.7', 'group': 'server', 'architecture': 'llama'},
    {'name': 'gpu', 'default': '0', 'group': 'server', 'architecture': 'both'},
]
CMAKE_GENERATOR, CMAKE_ARCH = "Visual Studio 17 2022", "x64"
PYTORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
PYTHON_ENV_BOOTSTRAP = {"torch": ("torch==2.6.0",)}

@dataclass(frozen=True)
class Variant:
    name: str
    hf: str
    assets: tuple[str, ...]
    t3_ckpt: str
    architecture: str
    external_assets: tuple[tuple[str, str], ...] = ()

_GPT2 = ("s3gen_meanflow.safetensors", "conds.pt", "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json")
VARIANTS = {
    "nano": Variant("nano", "https://huggingface.co/ResembleAI/chatterbox-nano/resolve/71ccd1d0081b430592cea481f4307e764e07bc64",
                    ("t3_nano_v1.safetensors",) + _GPT2, "t3_nano_v1.safetensors", "gpt2"),
    "turbo": Variant("turbo", "https://huggingface.co/ResembleAI/chatterbox-turbo/resolve/749d1c1a46eb10492095d68fbcf55691ccf137cd",
                     ("t3_turbo_v1.safetensors",) + _GPT2, "t3_turbo_v1.safetensors", "gpt2"),
    "v3": Variant("v3", "https://huggingface.co/ResembleAI/chatterbox/resolve/5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
                  ("t3_mtl23ls_v3.safetensors", "s3gen.safetensors", "conds.pt", "ve.safetensors",
                   "grapheme_mtl_merged_expanded_v1.json", "Cangjie5_TC.json"), "t3_mtl23ls_v3.safetensors", "llama",
                  (("official_mtl_tokenizer.py", "https://raw.githubusercontent.com/resemble-ai/chatterbox/5de7a54aa4e5e2baadb0182dde554908b48b85c2/src/chatterbox/models/tokenizers/tokenizer.py"),
                   ("official_mtl_tts.py", "https://raw.githubusercontent.com/resemble-ai/chatterbox/5de7a54aa4e5e2baadb0182dde554908b48b85c2/src/chatterbox/mtl_tts.py"),
                   ("dicta-1.0.int8.onnx", "https://github.com/thewh1teagle/dicta-onnx/releases/download/model-files-v1.0/dicta-1.0.int8.onnx"))),
}
ARCHITECTURES = {
    "gpt2": {"ckpt": ".ckpt", "s3_checkpoint": "s3gen_meanflow.safetensors", "s3_family": "meanflow"},
    "llama": {"ckpt": ".ckpt-v3", "s3_checkpoint": "s3gen.safetensors", "s3_family": "v3"},
}
EAR = {
    "repo": "https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/resolve/ea30d66debe3740a08b573244286791d423d6b3e",
    "dir": "nemotron-3.5-asr-streaming-0.6b",
    "files": ("config.json", "generation_config.json", "processor_config.json", "tokenizer_config.json", "tokenizer.json", "model.safetensors"),
    "sample_rate": 16000, "threads": 4, "language": "auto", "lookahead": 13,
    "pause": 1.2, "level": 0.03,
}
WAV = "wav"
BRAIN = {
    "url": "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/0314792d7f1f7e229411f620751375812bb9faf2/gemma-4-E2B-it-Q4_K_M.gguf",
    "file": "brain-gemma-4-e2b-it-q4_k_m.gguf",
    "n_ctx": 8192, "n_batch": 1024, "n_ubatch": 1024, "n_threads": 4, "n_gpu_layers": -1, "swa_full": True,
    "decode": {"temperature": 1.0, "top_p": 0.95, "top_k": 64, "min_p": 0.0, "max_tokens": 1024},
}
