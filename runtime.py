import ctypes, hashlib, json, os, subprocess, sys
from dataclasses import dataclass
from pathlib import Path
from settings import FLAGS, Variant

ROOT, MODELS = Path(__file__).resolve().parent, Path(__file__).resolve().parent / "models"
K32 = ctypes.WinDLL("kernel32", use_last_error=True)
K32.OpenProcess.argtypes, K32.OpenProcess.restype = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint], ctypes.c_void_p
K32.WaitForSingleObject.argtypes, K32.CloseHandle.argtypes = [ctypes.c_void_p, ctypes.c_uint], [ctypes.c_void_p]

def vulkan():
    os.environ.pop("GGML_VK_DISABLE_COOPMAT", None)
    os.environ.pop("GGML_VK_PREFER_HOST_MEMORY", None)
    from settings import VULKAN_DEVICE
    devices = vulkan_devices()
    if VULKAN_DEVICE is not None:
        os.environ["GGML_VK_VISIBLE_DEVICES"] = str(VULKAN_DEVICE)
        chosen = devices[int(VULKAN_DEVICE)]
        print(f"vulkan device {chosen['index']} {chosen['name']} {chosen['total']}", flush=True)
        return 0
    chosen = max(devices, key=lambda item: item["total"])
    print(f"vulkan device {chosen['index']} {chosen['name']} {chosen['total']}", flush=True)
    return chosen["index"]


def vulkan_devices():
    script = r"""
import ctypes, json
from pathlib import Path
dll = Path(r""" + '"' + str(ROOT / ".venv" / "Lib" / "site-packages" / "llama_cpp" / "lib" / "ggml-vulkan.dll") + '"' + r""")
lib = ctypes.CDLL(str(dll))
lib.ggml_backend_vk_get_device_count.restype = ctypes.c_int
lib.ggml_backend_vk_get_device_description.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t]
lib.ggml_backend_vk_get_device_memory.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
devices = []
for index in range(lib.ggml_backend_vk_get_device_count()):
    name = ctypes.create_string_buffer(256)
    lib.ggml_backend_vk_get_device_description(index, name, 256)
    free, total = ctypes.c_size_t(), ctypes.c_size_t()
    lib.ggml_backend_vk_get_device_memory(index, ctypes.byref(free), ctypes.byref(total))
    devices.append({"index": index, "name": name.value.decode("utf-8", "replace"), "total": total.value})
print(json.dumps(devices))
"""
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "vulkan device list")
    devices = json.loads(proc.stdout.strip().splitlines()[-1])
    if not devices:
        raise RuntimeError("no vulkan device")
    return devices

def venv_python() -> Path:
    return ROOT / ".venv" / "Scripts" / "python.exe"

def reexec():
    py = venv_python()
    if not py.is_file():
        raise RuntimeError("missing " + str(py))
    if Path(sys.executable).resolve() != py.resolve():
        raise SystemExit(subprocess.call([str(py), *sys.argv]))
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

@dataclass
class LaunchArgs:
    knobs: dict[str, str]
    t3_weight_type: str
    s3_weight_type: str
    reference: Path
    t3_quant_policy: Path
    s3_quant_policy: Path
    def policy(self, kind):
        return {"default": getattr(self, kind + "_weight_type"),
                "rules": json.loads(getattr(self, kind + "_quant_policy").read_text(encoding="utf-8"))["rules"]}
    def gguf(self, kind, family):
        policy = self.policy("s3" if kind == "s3gen" else kind)
        digest = hashlib.sha256(json.dumps(policy["rules"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:8]
        return MODELS / f"chatterbox-{kind}-{family}-{policy['default']}-{digest}.gguf"

def launch_args(cfg: Variant) -> LaunchArgs:
    knobs, conv = {}, {}
    for row in FLAGS:
        if row["architecture"] not in ("both", cfg.architecture):
            continue
        default = row["default"][cfg.architecture] if isinstance(row["default"], dict) else row["default"]
        if row["group"] == "server":
            knobs[row["name"]] = str(default)
        else:
            conv[row["name"]] = default
    return LaunchArgs(knobs, conv["t3-weight-type"], conv["s3-weight-type"],
                      (ROOT / conv["reference"]).resolve(), (ROOT / conv["t3-quant-policy"]).resolve(), (ROOT / conv["s3-quant-policy"]).resolve())

class Contract:
    def __init__(self, payload: dict, *outputs: Path):
        self.payload, self.outputs = payload, outputs
    def matches(self, path: Path) -> bool:
        return path.is_file() and json.loads(path.read_text(encoding="utf-8")) == self.payload and all(item.is_file() for item in self.outputs)
    def write(self, path: Path) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.payload, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)

def alive(pid: int) -> bool:
    handle = K32.OpenProcess(0x00100000, False, pid)
    if not handle:
        return False
    status = K32.WaitForSingleObject(handle, 0)
    K32.CloseHandle(handle)
    if status == 0xFFFFFFFF:
        raise ctypes.WinError(ctypes.get_last_error())
    return status == 258

def kill(pid: Path):
    if not pid.is_file():
        return
    record = json.loads(pid.read_text(encoding="utf-8"))
    if not alive(record["pid"]):
        pid.unlink()
        return
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(record["pid"])], check=True)
    handle = K32.OpenProcess(0x00100000, False, record["pid"])
    if handle:
        K32.WaitForSingleObject(handle, 10000)
        K32.CloseHandle(handle)
    pid.unlink()
