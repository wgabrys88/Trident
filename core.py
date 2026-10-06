import ctypes, json, tomllib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
STATE, MODELS, BIN = (ROOT / CONFIG["paths"][key] for key in ("state", "models", "bin"))
SYSTEM = (ROOT / "organism.txt").read_bytes().decode("utf-8")

def adapters():
    user = ctypes.WinDLL("user32", use_last_error=True)
    class Device(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_uint32), ("name", ctypes.c_wchar * 32),
                    ("string", ctypes.c_wchar * 128), ("flags", ctypes.c_uint32),
                    ("id", ctypes.c_wchar * 128), ("key", ctypes.c_wchar * 128)]
    user.EnumDisplayDevicesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.POINTER(Device), ctypes.c_uint32]
    user.EnumDisplayDevicesW.restype = ctypes.c_int
    found, index = [], 0
    while True:
        device = Device(ctypes.sizeof(Device))
        if not user.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
            return found
        found.append(device.string)
        index += 1

def accelerator():
    names = adapters()
    text = " ".join(names).casefold()
    if "nvidia" in text:
        return "nvidia"
    if "intel" in text:
        return "intel"
    raise RuntimeError("Need an NVIDIA or Intel GPU: " + ", ".join(names))

ACCELERATOR = accelerator()
_PROFILE = {
    "nvidia": {"flash_attention": "on", "release_gpu": True, "device": "cuda",
               "llama_asset": "bin-win-cuda-12.4-x64.zip",
               "cudart_asset": "cudart-llama-bin-win-cuda-12.4-x64.zip",
               "torch_index": "https://download.pytorch.org/whl/cu124"},
    "intel": {"flash_attention": "auto", "release_gpu": False, "device": "cpu",
              "llama_asset": "bin-win-vulkan-x64.zip", "cudart_asset": "",
              "torch_index": "https://download.pytorch.org/whl/cpu"},
}[ACCELERATOR]
CONFIG["brain"]["flash_attention"] = _PROFILE["flash_attention"]
CONFIG["brain"]["release_gpu"] = _PROFILE["release_gpu"]
CONFIG["mouth"]["device"] = _PROFILE["device"]
CONFIG["install"]["llama_asset"] = _PROFILE["llama_asset"]
CONFIG["install"]["cudart_asset"] = _PROFILE["cudart_asset"]
CONFIG["install"]["torch_index"] = _PROFILE["torch_index"]

def timestamp():
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S_%f%z")

def encode(value):
    return json.dumps(value, ensure_ascii=False, indent=2)

def tool(description, **parameters):
    def declare(method):
        properties = {name: {"type": {str: "string", int: "integer"}[method.__annotations__[name]],
                            "description": details} for name, details in parameters.items()}
        if "how" in properties:
            properties["how"]["enum"] = ["left", "right", "double"]
        method.schema = {"type": "function", "function": {"name": method.__name__, "description": description,
                         "parameters": {"type": "object", "properties": properties,
                                        "required": list(properties), "additionalProperties": False}}}
        return method
    return declare

class Interrupted(Exception):
    pass
