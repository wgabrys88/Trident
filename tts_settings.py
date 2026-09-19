import json
from pathlib import Path

_ENGINE_PRECISION_POLICY = Path(__file__).resolve().parent.parent / "chatterbox.cpp/scripts/precision_policy.json"
WEIGHT_TYPES = tuple(json.loads(_ENGINE_PRECISION_POLICY.read_text(encoding="utf-8"))["weight_types"])

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
    "v3": {
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
    "TTS_CPP_BUILD_EXECUTABLES": "ON",
    "GGML_BUILD_TESTS": "OFF",
    "GGML_BUILD_EXAMPLES": "OFF",
    "CMAKE_SKIP_INSTALL_RULES": "ON",
}

PYTORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
PYTHON_ENV_BOOTSTRAP = {
    "torch": ("torch==2.6.0",),
}
