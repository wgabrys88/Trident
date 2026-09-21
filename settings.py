from dataclasses import dataclass

FLAGS = [
    {'name': 'variant', 'default': None, 'help': 'nano/turbo: GPT-2 meanflow; v3: Llama CFG. Pick a variant for its flags.', 'group': 'model', 'architecture': 'both', 'positional': True, 'choices': ('nano', 'turbo', 'v3')},
    {'name': 'text', 'default': None, 'help': 'Text to synthesize; with --listen, a wav, a wav directory, a text file, - for stdin, or a string.', 'group': 'model', 'architecture': 'both', 'positional': True, 'metavar': 'TEXT', 'nargs': '?'},
    {'name': 'language', 'default': None, 'help': 'Language code for the v3 tokenizer.', 'group': 'model', 'architecture': 'llama', 'positional': True, 'nargs': '?'},
    {'name': 'reference', 'default': 'reference.wav', 'help': 'Reference voice WAV.', 'group': 'model', 'architecture': 'both'},
    {'name': 'listen', 'default': False, 'help': 'Voice loop: ear, brain, mouth. Each hear is one request. TEXT feeds the ear (wav or wav directory through decode, text file / - / string as lines); without TEXT the microphone is used.', 'group': 'model', 'architecture': 'both', 'action': 'store_true'},
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
    "url": "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/0314792d7f1f7e229411f620751375812bb9faf2/gemma-4-E2B-it-Q4_K_M.gguf",
    "file": "brain-gemma-4-e2b-it-q4_k_m.gguf",
    "n_ctx": 8192, "n_threads": 4, "n_gpu_layers": -1,
    "decode": {
        "speak": {"max_tokens": 4096, "temperature": 1.0, "top_p": 0.95, "top_k": 64},
        "consent": {"max_tokens": 32, "temperature": 0.0, "top_p": 0.95, "top_k": 64},
        "report": {"max_tokens": 256, "temperature": 1.0, "top_p": 0.95, "top_k": 64},
    },
}
_OFF = "If the hear means power off, shut down, quit, exit, switch off, or the same in any language, reply off. One word.\n"
_TASK = "If the hear is a task to run on this Windows computer, reply with only a Python 3 file. First line '# I will ' plus the action, then code that prints the result. User print hello ->\n# I will print hello.\nprint(\"hello\")\nUser print the current time ->\n# I will print the current time.\nimport datetime\nprint(datetime.datetime.now())\n"
PROMPTS = {
    "open": {
        "gpt2": _OFF + _TASK + "If the hear is speech to read or answer, each line is one spoken English chunk: a complete thought, two or three sentences, about {limit} characters. Split only where the meaning pauses, never mid-sentence, never by counting letters. Many lines. Write numbers, symbols and abbreviations as words. A line may start with one tag from: {tags}. Keep every fact. Do not summarize. Do not copy example wording. If the hear is not for you, reply with nothing. No markdown.",
        "llama": _OFF + _TASK + "If the hear is speech to read or answer, each line is language|text. language is from: {languages}. Each text is one spoken chunk: two or three sentences in one language, about {limit} characters. If the next sentence is another language, start a new line. Never put English on a pl| line. Never put Polish on an en| line. Never mid-sentence. Never by counting letters. Keep the original words. Do not translate. Keep source order. Many lines. Keep every fact. Do not summarize. Do not copy example wording. Format: pl|Ala ma kota. en|The ship is at the dock. pl|Kot pije mleko. If the hear is not for you, reply with nothing. No markdown.",
    },
    "consent": "The proposed action is: {intent}. Map the human's meaning to yes, no, or off. One word. No markdown. tak means yes. nie means no. yes means yes. no means no. Power off, shut down, quit, switch off, or the same in any language means off.",
    "report": "The program output follows. Reply with spoken lines in the same format, one sentence that states what the program printed. Not a Python file.",
}
