from dataclasses import dataclass

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
    {'name': 'repeat-penalty', 'default': '1.2', 'group': 'server', 'architecture': 'both'},
    {'name': 'n-predict', 'default': '1000', 'group': 'server', 'architecture': 'both'},
    {'name': 'cfm-steps', 'default': {'gpt2': '2', 'llama': '5'}, 'group': 'server', 'architecture': 'both'},
    {'name': 'trim-fade-samples', 'default': '480', 'group': 'server', 'architecture': 'both'},
    {'name': 'min-p', 'default': '0.05', 'group': 'server', 'architecture': 'llama'},
    {'name': 'cfg-weight', 'default': '0.5', 'group': 'server', 'architecture': 'llama'},
    {'name': 'exaggeration', 'default': '0.5', 'group': 'server', 'architecture': 'llama'},
    {'name': 'cfm-cfg', 'default': '0.7', 'group': 'server', 'architecture': 'llama'},
    {'name': 'gpu', 'default': '0', 'group': 'server', 'architecture': 'both'},
]
PIECE_LIMIT = 300
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
    "archive": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2",
    "dir": "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8", "files": ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"),
    "vad": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
    "sample_rate": 16000, "threads": 2, "provider": "cpu", "vad_threshold": 0.5, "min_silence": 0.25, "min_speech": 0.25, "max_speech": 20,
}
BRAIN = {
    "url": "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/0314792d7f1f7e229411f620751375812bb9faf2/gemma-4-E2B-it-Q4_K_M.gguf",
    "file": "brain-gemma-4-e2b-it-q4_k_m.gguf",
    "n_ctx": 32768, "n_threads": 4, "n_gpu_layers": -1,
    "decode": {"max_tokens": 4096, "temperature": 1.0, "top_p": 0.95, "top_k": 64, "min_p": 0.0},
    "flush": 3, "watchdog_arm": 10, "watchdog_repeat": 5,
}
TOOLS = [
    {"type": "function", "function": {
        "name": "say",
        "description": f"Speak words a person can hear. text is an array of strings, each at most {PIECE_LIMIT} characters. Split a long utterance into pieces of about 200 to {PIECE_LIMIT} characters. A short answer is one shorter piece. Describe code, paths, and other non-speech in words. If a request is missing a detail, ask.",
        "parameters": {"type": "object", "properties": {"text": {"type": "array", "items": {"type": "string"}, "description": f"Pieces to speak, in order. Each piece is at most {PIECE_LIMIT} characters."}}, "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "listen",
        "description": "The microphone is already open. Call listen to receive speech as user turns.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "quit",
        "description": "End the process. If the user text does not already say a quit was proposed, call quit and also call say to ask for confirmation. That call does not exit. On the next turn the user text says a quit was proposed. Call quit then only if this request agrees.",
        "parameters": {"type": "object", "properties": {}}}},
]
