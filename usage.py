import ctypes as C
import uuid
import win32api, win32pdh
from collections import defaultdict
from core import encode

PDH, KERNEL = C.WinDLL("pdh"), C.WinDLL("kernel32", use_last_error=True)
HANDLE = C.c_void_p
PDH.PdhGetFormattedCounterArrayW.argtypes = [HANDLE, C.c_uint32, C.POINTER(C.c_uint32), C.POINTER(C.c_uint32), C.c_void_p]
PDH.PdhGetFormattedCounterArrayW.restype = C.c_uint32
KERNEL.GetSystemTimes.argtypes = [C.c_void_p] * 3


def checked(status):
    if status:
        raise OSError(f"Windows performance counter error 0x{status:08x}")


class Value(C.Structure):
    _fields_ = [("status", C.c_uint32), ("number", C.c_double)]


class Item(C.Structure):
    _fields_ = [("name", C.c_wchar_p), ("value", Value)]


class Adapter(C.Structure):
    _fields_ = [("name", C.c_wchar * 128), ("ids", C.c_uint32 * 4), ("dedicated", C.c_size_t),
                ("system", C.c_size_t), ("shared", C.c_size_t), ("low", C.c_uint32), ("high", C.c_uint32), ("flags", C.c_uint32)]


def method(pointer, slot, *args):
    table = C.cast(pointer, C.POINTER(C.POINTER(C.c_void_p))).contents
    return C.WINFUNCTYPE(C.c_int32, C.c_void_p, *args)(table[slot])


def adapters():
    factory = HANDLE()
    iid = (C.c_ubyte * 16).from_buffer_copy(uuid.UUID("770aae78-f26f-4dba-a829-253c83d1b387").bytes_le)
    create = C.WinDLL("dxgi").CreateDXGIFactory1
    create.argtypes, create.restype = [C.c_void_p, C.POINTER(HANDLE)], C.c_int32
    checked(create(iid, C.byref(factory)) & 0xFFFFFFFF)
    result, index = [], 0
    try:
        while True:
            adapter, desc = HANDLE(), Adapter()
            status = method(factory, 12, C.c_uint32, C.POINTER(HANDLE))(factory, index, C.byref(adapter)) & 0xFFFFFFFF
            if status == 0x887A0002:  # DXGI_ERROR_NOT_FOUND ends enumeration.
                return result
            checked(status)
            try:
                checked(method(adapter, 10, C.c_void_p)(adapter, C.byref(desc)) & 0xFFFFFFFF)
                if not desc.flags & 2:
                    result.append((f"luid_0x{desc.high:08x}_0x{desc.low:08x}", desc.name, desc.dedicated, desc.shared))
            finally:
                method(adapter, 2)(adapter)
            index += 1
    finally:
        method(factory, 2)(factory)


class Usage:
    def __init__(self, folder):
        self.path, self.adapters, self.query = folder / "usage.txt", adapters(), win32pdh.OpenQuery()
        self.counters = {key: win32pdh.AddEnglishCounter(self.query, path) for key, path in {
            "engines": r"\GPU Engine(*)\Utilization Percentage", "dedicated": r"\GPU Adapter Memory(*)\Dedicated Usage",
            "shared": r"\GPU Adapter Memory(*)\Shared Usage"}.items()}
        win32pdh.CollectQueryData(self.query)
        self.previous = self.times()

    def times(self):
        times = [C.c_uint64() for _ in range(3)]
        if not KERNEL.GetSystemTimes(*(C.byref(value) for value in times)):
            raise C.WinError(C.get_last_error())
        return tuple(value.value for value in times)

    def values(self, counter):
        size, count = C.c_uint32(), C.c_uint32()
        status = PDH.PdhGetFormattedCounterArrayW(counter, 0x200, C.byref(size), C.byref(count), None)
        if status == 0x800007D5:  # PDH_NO_DATA: no live instances (for example, no GPU engine yet).
            return {}
        if status != 0x800007D2:  # PDH_MORE_DATA is the required buffer-size negotiation.
            checked(status)
        buffer = C.create_string_buffer(size.value)
        checked(PDH.PdhGetFormattedCounterArrayW(counter, 0x200, C.byref(size), C.byref(count), buffer))
        return {item.name: item.value.number for item in C.cast(buffer, C.POINTER(Item * count.value)).contents
                if item.value.status in (0, 1)}

    def record(self, stamp, model, direction):
        win32pdh.CollectQueryData(self.query)
        counters = {key: self.values(counter) for key, counter in self.counters.items()}
        current = self.times()
        idle, kernel, user = (now - before for now, before in zip(current, self.previous))
        self.previous = current
        memory = win32api.GlobalMemoryStatusEx()
        gpus = []
        for luid, name, dedicated, shared in self.adapters:
            engines = defaultdict(float)
            for instance, value in counters["engines"].items():
                if luid in instance:
                    engines[instance.split("_eng_", 1)[1]] += value
            used = {key: sum(values) if (values := [value for instance, value in counters[key].items() if instance.startswith(luid)]) else None
                    for key in ("dedicated", "shared")}
            gpus.append({"adapter": name, "utilization_percent": max(engines.values()) if engines else None,
                "dedicated_used_bytes": used["dedicated"], "dedicated_total_bytes": dedicated,
                "dedicated_available_bytes": dedicated - used["dedicated"] if used["dedicated"] is not None else None,
                "shared_used_bytes": used["shared"], "shared_total_bytes": shared,
                "shared_available_bytes": shared - used["shared"] if used["shared"] is not None else None})
        with self.path.open("a", encoding="utf-8") as ledger:
            ledger.write(encode({"timestamp": stamp, "model": model, "direction": direction,
                "cpu_percent": 100 * (kernel + user - idle) / (kernel + user) if kernel + user else None,
                "ram_used_bytes": memory["TotalPhys"] - memory["AvailPhys"], "ram_total_bytes": memory["TotalPhys"], "gpus": gpus}) + "\n")

    def close(self):
        win32pdh.CloseQuery(self.query)
