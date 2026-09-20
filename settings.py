import json
from pathlib import Path

WEIGHT_TYPES = tuple(json.loads((Path(__file__).resolve().parent / "scripts/precision_policy.json").read_text(encoding="utf-8"))["weight_types"])

RUNTIME_DEFAULTS = {
    "gpt2": {
        "seed": "42",
        "temperature": "0.8",
        "top-k": "1000",
        "top-p": "0.95",
        "repeat-penalty": "1.2",
        "n-predict": "1000",
        "cfm-steps": "2",
        "trim-fade-samples": "480",
    },
    "llama": {
        "seed": "42",
        "temperature": "0.8",
        "top-p": "1.0",
        "repeat-penalty": "1.2",
        "n-predict": "1000",
        "min-p": "0.05",
        "cfg-weight": "0.5",
        "exaggeration": "0.5",
        "cfm-steps": "5",
        "cfm-cfg": "0.7",
        "trim-fade-samples": "480",
    },
}
CONVERSION_DEFAULTS = {"t3-weight-type": "q4_0", "s3-weight-type": "q4_0"}

CMAKE_GENERATOR = "Visual Studio 17 2022"
CMAKE_ARCH = "x64"
CMAKE_FLAGS = {
    "BUILD_SHARED_LIBS": "ON",
    "GGML_BUILD_TESTS": "OFF",
    "GGML_BUILD_EXAMPLES": "OFF",
    "CMAKE_SKIP_INSTALL_RULES": "ON",
    "GGML_VULKAN": "ON",
    "GGML_CPU": "OFF",
    "GGML_CUDA": "OFF",
    "GGML_OPENMP": "OFF",
}

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
