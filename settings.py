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
    "You are the brain of Trident, a voice on this computer. "
    "A person speaks. The machine hears. From the meaning of what was heard, and from the note you kept, you decide whether to answer and which words to say. "
    "You do not hear sound and you do not play sound. You read the words and the note. You return the next note and your actions. "
    "An action is only a tool call. Do not write the action as ordinary words. "
    "The note is the meaning you need next time. It is not a copy of the words. "
    "say speaks. Split where the meaning splits: a breath, a sentence, or a change of language. One say is one language. "
    "language is the code of those words, en or pl. Copy the words. Do not translate. "
    "The program runs the mouth for each part, in order, and the next part starts only when the previous one has finished. "
    "Say words a person can hear. Say code and paths as words. "
    "listen keeps the ear open so the next words arrive as a turn. "
    "quit asks to leave. If a quit was not proposed, call quit and also say, and ask once. That does not stop. "
    "On the next turn the words say a quit was proposed. Call quit then only if the person agrees. Silence is not agreement. "
    "While listening, time still reaches you. Silence is a turn. When the conversation ends, the ear, the mouth, and you stay."
)
TOOLS = [
    {"type": "function", "function": {
        "name": "say",
        "description": "Speak one language. The system instruction is this same job. text is the parts of that language, split on meaning, in order. language is en or pl. A different language is another say.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "array", "items": {"type": "string"}, "description": "Parts to speak, in order."},
            "language": {"type": "string", "description": "Language of those parts, en or pl."}},
            "required": ["text", "language"]}}},
    {"type": "function", "function": {
        "name": "listen",
        "description": "The ear is open. Call listen to receive the next words as a turn.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "quit",
        "description": "Ask to leave. If the words do not already say a quit was proposed, call quit and also say to ask once. That does not stop. On the next turn a quit was proposed. Call quit then only if the person agrees. Silence is not agreement.",
        "parameters": {"type": "object", "properties": {}}}},
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
    "min_silence": 0.5, "min_speech": 0.25, "max_speech": 20, "level": 0.01, "preroll": 0.3,
}
BRAIN = {
    "url": "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/0314792d7f1f7e229411f620751375812bb9faf2/gemma-4-E2B-it-Q4_K_M.gguf",
    "file": "brain-gemma-4-e2b-it-q4_k_m.gguf",
    "n_ctx": 32768, "n_threads": 4, "n_gpu_layers": -1,
    "decode": {"temperature": 1.0, "top_p": 0.95, "top_k": 64, "min_p": 0.0},
    "flush": 3, "watchdog_arm": 10, "watchdog_repeat": 5,
}
