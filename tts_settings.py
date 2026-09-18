import json
from pathlib import Path
from types import MappingProxyType

_ENGINE_PRECISION_POLICY = Path(__file__).resolve().parent.parent / "chatterbox.cpp/scripts/precision_policy.json"
WEIGHT_TYPES = tuple(json.loads(_ENGINE_PRECISION_POLICY.read_text(encoding="utf-8"))["weight_types"])
ANALYSIS_MODES = ("none", "quick", "full")
WATERMARK_MODES = ("on", "off")

# kind: i=integer, f=finite float, f+=strictly-positive finite float
_RUNTIME_SPEC = {
    "gpt2": {
        "seed": ("i", "42"),
        "temperature": ("f", "0.8"),
        "top-k": ("i", "1000"),
        "top-p": ("f", "0.95"),
        "repeat-penalty": ("f+", "1.2"),
        "n-predict": ("i", "1000"),
        "cfm-steps": ("i", "2"),
        "trim-fade-samples": ("i", "480"),
    },
    "v3": {
        "seed": ("i", "42"),
        "temperature": ("f", "0.8"),
        "top-p": ("f", "1.0"),
        "repeat-penalty": ("f+", "1.2"),
        "n-predict": ("i", "1000"),
        "min-p": ("f", "0.05"),
        "cfg-weight": ("f", "0.5"),
        "exaggeration": ("f", "0.5"),
        "cfm-steps": ("i", "10"),
        "cfm-cfg": ("f", "0.7"),
        "trim-fade-samples": ("i", "480"),
    },
}
RUNTIME_DEFAULTS = MappingProxyType({
    family: MappingProxyType({name: default for name, (_, default) in spec.items()})
    for family, spec in _RUNTIME_SPEC.items()
})
RUNTIME_KINDS = MappingProxyType({
    name: kind for spec in _RUNTIME_SPEC.values() for name, (kind, _) in spec.items()
})
CONVERSION_DEFAULTS = MappingProxyType({
    "gpt2": MappingProxyType({"t3-weight-type": "f32", "s3-weight-type": "f32"}),
    "v3": MappingProxyType({"t3-weight-type": "f32", "s3-weight-type": "f32"}),
})
CONTROL_DEFAULTS = MappingProxyType({
    "analysis": "full",
    "determinism-repeats": "2",
    "watermark": "on",
})

CMAKE_GENERATOR = "Visual Studio 17 2022"
CMAKE_ARCH = "x64"
CMAKE_FLAGS = MappingProxyType({
    "BUILD_SHARED_LIBS": "ON",
    "TTS_CPP_BUILD_EXECUTABLES": "ON",
    "GGML_BUILD_TESTS": "OFF",
    "GGML_BUILD_EXAMPLES": "OFF",
})

PYTORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
PYTHON_ENV_BOOTSTRAP = MappingProxyType({
    "converter": MappingProxyType({"torch": ("torch==2.6.0",), "pre": (), "msvc": False}),
    "tokenizer": MappingProxyType({"torch": ("torch==2.6.0",), "pre": (), "msvc": False}),
    "analysis": MappingProxyType({
        "torch": ("torch==2.5.1", "torchaudio==2.5.1"),
        "pre": ("setuptools==80.9.0", "wheel==0.45.1", "Cython==3.1.3", "numpy==2.1.3"),
        "msvc": True,
    }),
    "watermark": MappingProxyType({"torch": ("torch==2.5.1",), "pre": (), "msvc": False}),
})
