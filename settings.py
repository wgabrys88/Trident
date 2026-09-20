from scripts.quant import TYPES

FLAGS = [
    {'name': 'variant', 'default': None, 'help': 'nano/turbo: GPT-2 meanflow; v3: Llama CFG. Pick a variant for its flags.', 'group': 'model', 'architecture': 'both', 'positional': True, 'choices': ('nano', 'turbo', 'v3')},
    {'name': 'text', 'default': None, 'help': 'Text to synthesize.', 'group': 'model', 'architecture': 'both', 'positional': True, 'metavar': 'TEXT'},
    {'name': 'language', 'default': None, 'help': 'Language code for the v3 tokenizer.', 'group': 'model', 'architecture': 'llama', 'positional': True},
    {'name': 'reference', 'default': 'reference.wav', 'help': 'Reference voice WAV.', 'group': 'model', 'architecture': 'both'},
    {'name': 't3-weight-type', 'default': 'q4_0', 'help': 'Matrix GGUF type; changing conversion rebuilds GGUF, rebakes, restarts. Mix tensor types in JSON; integers never quantized; Q4_K_M is not a type. Types: ' + ', '.join(TYPES) + '.' + '', 'group': 'conversion', 'architecture': 'both', 'metavar': 'TYPE', 'choices': TYPES},
    {'name': 's3-weight-type', 'default': 'q4_0', 'help': 'Matrix GGUF type; changing conversion rebuilds GGUF, rebakes, restarts. Mix tensor types in JSON; integers never quantized; Q4_K_M is not a type. Types: ' + ', '.join(TYPES) + '.' + '', 'group': 'conversion', 'architecture': 'both', 'metavar': 'TYPE', 'choices': TYPES},
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

ARCHITECTURES = {
    "gpt2": {
        "ckpt": ".ckpt",
        "s3_checkpoint": "s3gen_meanflow.safetensors",
        "s3_family": "meanflow",
        "t3_script": "convert_t3_gpt2.py",
        "server": "chatterbox-server-gpt2.exe",
        "bake": "chatterbox-bake-gpt2.exe",
    },
    "llama": {
        "ckpt": ".ckpt-v3",
        "s3_checkpoint": "s3gen.safetensors",
        "s3_family": "v3",
        "t3_script": "convert_t3_llama.py",
        "server": "chatterbox-server-llama.exe",
        "bake": "chatterbox-bake-llama.exe",
    },
}
