from dataclasses import dataclass

FLAGS = [
    {'name': 'variant', 'default': None, 'help': 'nano/turbo: GPT-2 meanflow; v3: Llama CFG. Pick a variant for its flags.', 'group': 'model', 'architecture': 'both', 'positional': True, 'choices': ('nano', 'turbo', 'v3')},
    {'name': 'text', 'default': None, 'help': 'Text to synthesize.', 'group': 'model', 'architecture': 'both', 'positional': True, 'metavar': 'TEXT', 'nargs': '?'},
    {'name': 'language', 'default': None, 'help': 'Language code for the v3 tokenizer.', 'group': 'model', 'architecture': 'llama', 'positional': True, 'nargs': '?'},
    {'name': 'reference', 'default': 'reference.wav', 'help': 'Reference voice WAV.', 'group': 'model', 'architecture': 'both'},
    {'name': 'listen', 'default': False, 'help': 'Voice loop: ear, brain, mouth. TEXT feeds ear lines (file, - for stdin, or a string); without TEXT the microphone is used.', 'group': 'model', 'architecture': 'both', 'action': 'store_true'},
    {'name': 'wake-phrase', 'default': 'trident alpha bravo charlie', 'help': 'NATO radio activation (alphabet) that starts a request, not casual English.', 'group': 'session', 'architecture': 'both'},
    {'name': 'stop-phrase', 'default': 'trident over', 'help': 'NATO radio activation (proword) that ends a request, not casual English.', 'group': 'session', 'architecture': 'both'},
    {'name': 'tool-phrase', 'default': 'trident delta oscar', 'help': 'NATO radio activation (alphabet) after the wake phrase that asks for a script instead of speech, not casual English.', 'group': 'session', 'architecture': 'both'},
    {'name': 'approve-phrase', 'default': 'trident roger', 'help': 'NATO radio activation (proword) that runs the proposed script, not casual English.', 'group': 'session', 'architecture': 'both'},
    {'name': 'reject-phrase', 'default': 'trident negative', 'help': 'NATO radio activation (proword) that discards the proposed script, not casual English.', 'group': 'session', 'architecture': 'both'},
    {'name': 'chunk-chars', 'default': '300', 'help': 'Maximum characters per spoken line produced by the brain.', 'group': 'session', 'architecture': 'both'},
    {'name': 'tool-timeout', 'default': '60', 'help': 'Seconds a tool script may run.', 'group': 'session', 'architecture': 'both'},
    {'name': 't3-weight-type', 'default': 'q4_0', 'help': 'Matrix GGUF type; changing conversion rebuilds GGUF, rebakes, restarts. Mix tensor types in JSON; integers never quantized; Q4_K_M is not a type.', 'group': 'conversion', 'architecture': 'both', 'metavar': 'TYPE'},
    {'name': 's3-weight-type', 'default': 'q4_0', 'help': 'Matrix GGUF type; changing conversion rebuilds GGUF, rebakes, restarts. Mix tensor types in JSON; integers never quantized; Q4_K_M is not a type.', 'group': 'conversion', 'architecture': 'both', 'metavar': 'TYPE'},
    {'name': 't3-quant-policy', 'default': 'scripts/quant_t3.json', 'help': 'Per-tensor JSON rules; replaces shipped rules. Mix tensor types in JSON; integers never quantized; Q4_K_M is not a type. Changes rebuild GGUF, rebake, restart.', 'group': 'conversion', 'architecture': 'both'},
    {'name': 's3-quant-policy', 'default': 'scripts/quant_s3.json', 'help': 'Per-tensor JSON rules; replaces shipped rules. Mix tensor types in JSON; integers never quantized; Q4_K_M is not a type. Changes rebuild GGUF, rebake, restart.', 'group': 'conversion', 'architecture': 'both'},
    {'name': 'seed', 'default': '42', 'help': 'Sampling random seed. Changes restart the exclusive server; matching GGUF reused.', 'group': 'server', 'architecture': 'both'},
    {'name': 'temperature', 'default': '0.8', 'help': 'Sampling temperature. Changes restart the exclusive server; matching GGUF reused.', 'group': 'server', 'architecture': 'both'},
    {'name': 'top-k', 'default': '1000', 'help': 'Top-k sampling cutoff. Changes restart the exclusive server; matching GGUF reused.', 'group': 'server', 'architecture': 'gpt2'},
    {'name': 'top-p', 'default': {'gpt2': '0.95', 'llama': '1.0'}, 'help': 'Nucleus sampling probability. Changes restart the exclusive server; matching GGUF reused.', 'group': 'server', 'architecture': 'both'},
    {'name': 'repeat-penalty', 'default': '1.2', 'help': 'Repeated speech token penalty. Changes restart the exclusive server; matching GGUF reused.', 'group': 'server', 'architecture': 'both'},
    {'name': 'n-predict', 'default': '1000', 'help': 'Maximum generated speech tokens. Changes restart the exclusive server; matching GGUF reused.', 'group': 'server', 'architecture': 'both'},
    {'name': 'cfm-steps', 'default': {'gpt2': '2', 'llama': '5'}, 'help': 'Flow matching steps. Changes restart the exclusive server; matching GGUF reused.', 'group': 'server', 'architecture': 'both'},
    {'name': 'trim-fade-samples', 'default': '480', 'help': 'Leading silence and fade length in samples. Changes restart the exclusive server; matching GGUF reused.', 'group': 'server', 'architecture': 'both'},
    {'name': 'min-p', 'default': '0.05', 'help': 'Minimum relative token probability. Changes restart the exclusive server; matching GGUF reused.', 'group': 'server', 'architecture': 'llama'},
    {'name': 'cfg-weight', 'default': '0.5', 'help': 'T3 classifier-free guidance weight. Changes restart the exclusive server; matching GGUF reused.', 'group': 'server', 'architecture': 'llama'},
    {'name': 'exaggeration', 'default': '0.5', 'help': 'Voice emotion exaggeration. Changes restart the exclusive server; matching GGUF reused.', 'group': 'server', 'architecture': 'llama'},
    {'name': 'cfm-cfg', 'default': '0.7', 'help': 'S3 classifier-free guidance weight. Changes restart the exclusive server; matching GGUF reused.', 'group': 'server', 'architecture': 'llama'},
    {'name': 'gpu', 'default': '0', 'help': 'Vulkan device index. Changes restart the exclusive server; matching GGUF reused.', 'group': 'server', 'architecture': 'both'},
]

CMAKE_GENERATOR = "Visual Studio 17 2022"
CMAKE_ARCH = "x64"

PYTORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
PYTHON_ENV_BOOTSTRAP = {
    "torch": ("torch==2.6.0",),
}

@dataclass(frozen=True)
class Variant:
    name: str
    hf: str
    assets: tuple[str, ...]
    t3_ckpt: str
    architecture: str
    external_assets: tuple[tuple[str, str], ...] = ()

VARIANTS = {
    "nano": Variant(
        name="nano", t3_ckpt="t3_nano_v1.safetensors",
        hf="https://huggingface.co/ResembleAI/chatterbox-nano/resolve/71ccd1d0081b430592cea481f4307e764e07bc64",
        assets=("t3_nano_v1.safetensors","s3gen_meanflow.safetensors","conds.pt","ve.safetensors","vocab.json","merges.txt","added_tokens.json"),
        architecture="gpt2",
    ),
    "turbo": Variant(
        name="turbo", t3_ckpt="t3_turbo_v1.safetensors",
        hf="https://huggingface.co/ResembleAI/chatterbox-turbo/resolve/749d1c1a46eb10492095d68fbcf55691ccf137cd",
        assets=("t3_turbo_v1.safetensors","s3gen_meanflow.safetensors","conds.pt","ve.safetensors","vocab.json","merges.txt","added_tokens.json"),
        architecture="gpt2",
    ),
    "v3": Variant(
        name="v3", t3_ckpt="t3_mtl23ls_v3.safetensors",
        hf="https://huggingface.co/ResembleAI/chatterbox/resolve/5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
        assets=("t3_mtl23ls_v3.safetensors","s3gen.safetensors","conds.pt","ve.safetensors","grapheme_mtl_merged_expanded_v1.json","Cangjie5_TC.json"),
        architecture="llama",
        external_assets=(
            ("official_mtl_tokenizer.py", "https://raw.githubusercontent.com/resemble-ai/chatterbox/5de7a54aa4e5e2baadb0182dde554908b48b85c2/src/chatterbox/models/tokenizers/tokenizer.py"),
            ("official_mtl_tts.py", "https://raw.githubusercontent.com/resemble-ai/chatterbox/5de7a54aa4e5e2baadb0182dde554908b48b85c2/src/chatterbox/mtl_tts.py"),
            ("dicta-1.0.int8.onnx", "https://github.com/thewh1teagle/dicta-onnx/releases/download/model-files-v1.0/dicta-1.0.int8.onnx"),
        ),
    ),
}

ARCHITECTURES = {
    "gpt2": {
        "ckpt": ".ckpt",
        "s3_checkpoint": "s3gen_meanflow.safetensors",
        "s3_family": "meanflow",
    },
    "llama": {
        "ckpt": ".ckpt-v3",
        "s3_checkpoint": "s3gen.safetensors",
        "s3_family": "v3",
    },
}

EAR = {
    "archive": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2",
    "dir": "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
    "files": ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"),
    "vad": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
    "sample_rate": 16000, "threads": 2, "provider": "cpu",
    "vad_threshold": 0.5, "min_silence": 0.25, "min_speech": 0.25, "max_speech": 20,
}
BRAIN = {
    "url": "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/6dd44a1fb35d11b5d1b28902876ce3cc9e882d0e/qwen2.5-0.5b-instruct-q4_k_m.gguf",
    "file": "brain-qwen2.5-0.5b-instruct-q4_k_m.gguf",
    "n_ctx": 2048, "n_threads": 4, "n_gpu_layers": 0,
}
PROMPTS = {
    "speak": {
        "gpt2": "You prepare text for an English speech synthesizer. Rewrite the user's message as natural spoken English. Split it into short chunks, one per line, each a complete thought under {limit} characters. Write numbers, symbols and abbreviations as words. A line may start with one tag from: {tags}. Output only the lines.",
        "llama": "You prepare text for a multilingual speech synthesizer. Keep the user's language. Split the message into short chunks, one per line, each a complete thought under {limit} characters. Start every line with the language code of that line and a vertical bar, for example: pl|Dzien dobry. Allowed codes: {languages}. Output only the lines.",
    },
    "code": "Write a Python 3 script for Windows that does what the user asks. The first line is a comment that starts with '# I will ' and states the action in one sentence. Print the result. Output only the code.",
    "report": " The user ran a program. Its output follows. Say in one sentence what happened.",
}
