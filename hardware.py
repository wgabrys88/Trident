import ctypes as C
import os

def adapters():
    class Device(C.Structure):
        _fields_ = [("cb", C.c_uint32), ("name", C.c_wchar * 32), ("description", C.c_wchar * 128),
                    ("flags", C.c_uint32), ("id", C.c_wchar * 128), ("key", C.c_wchar * 128)]
    enumerate_device = C.WinDLL("user32").EnumDisplayDevicesW
    enumerate_device.argtypes = [C.c_wchar_p, C.c_uint32, C.POINTER(Device), C.c_uint32]
    enumerate_device.restype = C.c_int
    names, index = [], 0
    while True:
        device = Device(C.sizeof(Device))
        if not enumerate_device(None, index, C.byref(device), 0):
            return names
        names.append(device.description)
        index += 1

def select(config):
    name, profiles = config["profile"], config["profiles"]
    if name != "auto":
        return name, profiles[name]
    names = adapters()
    text = " ".join(names).casefold()
    for name, profile in profiles.items():
        if any(marker.casefold() in text for marker in profile["adapter_names"]):
            return name, profile
    raise RuntimeError("No configured GPU profile matches Windows adapters: " + ", ".join(names))

def environment(profile):
    env = os.environ.copy()
    if "sdk" in profile:
        from rocm_sdk import find_libraries
        paths = dict.fromkeys(str(path.parent) for path in find_libraries("hipblas", "hipblaslt"))
        env["PATH"] = os.pathsep.join([*paths, env["PATH"]])
    return env
