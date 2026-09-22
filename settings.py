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
    {'name': 'repeat-penalty', 'default': {'gpt2': '1.2', 'llama': '2.0'}, 'group': 'server', 'architecture': 'both'},
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
SPEAK = (
    "You are an assistant. A person is talking with you. Their words reach you as text. "
    "Answer them as you would answer someone who typed those words. "
    "You do not hear sound and you do not make sound. "
    "say is how they hear your answer. listen is how their next words reach you. quit is how you leave. "
    "note is the short text you keep. When you have kept one, it is the first paragraph and their new words are the last."
)
TOOLS = [
    {"type": "function", "function": {
        "name": "say",
        "description": "Speak your answer aloud. One call is one language and the whole of what they should hear in that language. text is that answer split where the meaning splits, in order. language is en or pl. A different language is another call.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "array", "items": {"type": "string"}, "description": "One meaning part. The parts of this call are spoken in order."},
            "language": {"type": "string", "description": "en or pl."}},
            "required": ["text", "language"]}}},
    {"type": "function", "function": {
        "name": "listen",
        "description": "Wait. The person's next words arrive as the next turn.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "quit",
        "description": "Leave the conversation.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "note",
        "description": "Keep a short note. The next turn begins with it.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "The note."}},
            "required": ["text"]}}},
]
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
}
WAV = "wav"
BRAIN = {
    "url": "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/0314792d7f1f7e229411f620751375812bb9faf2/gemma-4-E2B-it-Q4_K_M.gguf",
    "file": "brain-gemma-4-e2b-it-q4_k_m.gguf",
    "n_ctx": 32768, "n_threads": 4, "n_gpu_layers": -1,
    "decode": {"temperature": 1.0, "top_p": 0.95, "top_k": 64, "min_p": 0.0},
}
