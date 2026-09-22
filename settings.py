from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "workspace"
DONE = WORK / "done"
LIVE = WORK / "live.txt"
MEMORY = WORK / "memory.md"
SAID = WORK / "said.txt"
INBOX = WORK / "inbox"
CHUNK = 60
MAX_LIVE = 12000

def bus() -> Path:
    WORK.mkdir(parents=True, exist_ok=True)
    DONE.mkdir(exist_ok=True)
    INBOX.mkdir(exist_ok=True)
    if not LIVE.exists():
        LIVE.write_text("", encoding="utf-8")
    if not MEMORY.exists():
        MEMORY.write_text("", encoding="utf-8")
    return WORK

def put(path: Path, text: str) -> Path:
    bus()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path

def take(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig")
    dest = DONE / path.name
    n = 1
    while dest.exists():
        dest = DONE / f"{path.stem}-{n}{path.suffix}"
        n += 1
    path.replace(dest)
    return text

def next_path(prefix: str) -> Path:
    n = 1
    while True:
        name = f"{prefix}-{n}.txt"
        if not (bus() / name).exists() and not (DONE / name).exists():
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

def read_live() -> str:
    bus()
    text = LIVE.read_text(encoding="utf-8")
    if len(text) > MAX_LIVE:
        text = text[-MAX_LIVE:]
    return text

def append_live(text: str) -> None:
    bus()
    with LIVE.open("a", encoding="utf-8") as handle:
        handle.write(text.rstrip() + "\n")

def clear_live() -> None:
    bus()
    LIVE.write_text("", encoding="utf-8")

def sweep_queue() -> None:
    bus()
    for path in list(INBOX.glob("*.txt")) + list(WORK.glob("transcription-*.txt")) + list(WORK.glob("speech-*.txt")):
        path.unlink()
    SAID.write_text("", encoding="utf-8")
    clear_live()

def user_text() -> str:
    bus()
    memory = MEMORY.read_text(encoding="utf-8").strip()
    live = read_live()
    if memory and live:
        return memory + "\n\n" + live
    return memory or live

SPEAK = (
    "You are Jarvis. The user text is memory, then unread live text. "
    "Act only by calling tools. "
    "pass writes nothing: the live text is unfinished or needs no action. "
    "say speaks. note appends one fact and does not speak. distill replaces memory, clears the live text, and does not speak. "
    "run_python runs one script. Its result is the next lines, and those lines start with exit. "
    "When a line starts with exit, do not call run_python again. If the person asked to hear the result, say. "
    "quit ends you."
)
ALOUD = (
    "The user text is the words to speak. Call say. "
    "language is en, or pl when the words are Polish. Do not add words."
)
TOOLS = [
    {"type": "function", "function": {
        "name": "pass",
        "description": "Write nothing. The live text is unfinished or needs no action.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "say",
        "description": "Speak text. language is en or pl. Sixty words is one stretch. Longer text is split on sentence ends and played in order.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "Words to speak."},
            "language": {"type": "string", "description": "en or pl."}},
            "required": ["text", "language"]}}},
    {"type": "function", "function": {
        "name": "note",
        "description": "Append one fact to memory. A name, a decision, or a constraint. Does not speak.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "One fact."}},
            "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "distill",
        "description": "Replace memory with text. Keep names and decisions. Clears the live text. Does not speak.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "The new memory."}},
            "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "run_python",
        "description": "Run code once. The working directory is the workspace folder. Write pong.txt in that folder. The next look adds lines that start with exit. Do not call run_python after those lines. Say on that look when the person asked to hear the result.",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "description": "One complete Python script."}},
            "required": ["code"]}}},
    {"type": "function", "function": {
        "name": "quit",
        "description": "End the Jarvis process.",
        "parameters": {"type": "object", "properties": {}}}},
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
    "sample_rate": 16000, "threads": 4, "language": "auto", "lookahead": 3,
    "pause": 3.0, "level": 0.03,
}
WAV = "wav"
BRAIN = {
    "url": "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/0314792d7f1f7e229411f620751375812bb9faf2/gemma-4-E2B-it-Q4_K_M.gguf",
    "file": "brain-gemma-4-e2b-it-q4_k_m.gguf",
    "n_ctx": 4096, "n_batch": 512, "n_threads": 4, "n_gpu_layers": 0,
    "decode": {"temperature": 1.0, "top_p": 0.95, "top_k": 64, "min_p": 0.0, "max_tokens": 1024},
}
