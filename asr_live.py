from __future__ import annotations

import ctypes
import json
import multiprocessing as mp
import os
from pathlib import Path


def _api(dll: str):
    directory = os.add_dll_directory(str(Path(dll).parent)) if hasattr(os, "add_dll_directory") else None
    lib = ctypes.CDLL(dll)
    lib.parakeet_capi_abi_version.restype = ctypes.c_int
    lib.parakeet_capi_load.argtypes = [ctypes.c_char_p]
    lib.parakeet_capi_load.restype = ctypes.c_void_p
    lib.parakeet_capi_free.argtypes = [ctypes.c_void_p]
    lib.parakeet_capi_stream_begin_lang.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.parakeet_capi_stream_begin_lang.restype = ctypes.c_void_p
    lib.parakeet_capi_stream_feed_json.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int]
    lib.parakeet_capi_stream_feed_json.restype = ctypes.c_void_p
    lib.parakeet_capi_stream_finalize_json.argtypes = [ctypes.c_void_p]
    lib.parakeet_capi_stream_finalize_json.restype = ctypes.c_void_p
    lib.parakeet_capi_stream_free.argtypes = [ctypes.c_void_p]
    lib.parakeet_capi_free_string.argtypes = [ctypes.c_void_p]
    lib.parakeet_capi_last_error.argtypes = [ctypes.c_void_p]
    lib.parakeet_capi_last_error.restype = ctypes.c_char_p
    return lib, directory


def _json_result(lib, ptr):
    if not ptr:
        return None
    try:
        return json.loads(ctypes.string_at(ptr).decode("utf-8"))
    finally:
        lib.parakeet_capi_free_string(ptr)


def _worker(conn, dll: str, model: str):
    lib = ctx = stream = directory = None
    try:
        lib, directory = _api(dll)
        abi = lib.parakeet_capi_abi_version()
        if abi < 5:
            raise RuntimeError(f"Parakeet C API ABI {abi} is too old; need >=5")
        ctx = lib.parakeet_capi_load(os.fsencode(model))
        if not ctx:
            raise RuntimeError("Parakeet model load failed")
        stream = lib.parakeet_capi_stream_begin_lang(ctx, b"auto")
        if not stream:
            raise RuntimeError((lib.parakeet_capi_last_error(ctx) or b"stream begin failed").decode("utf-8", "replace"))
        conn.send(("ready", {"abi": abi}))
        while True:
            op, payload = conn.recv()
            if op == "feed":
                count = len(payload) // 4
                pcm = (ctypes.c_float * count).from_buffer_copy(payload)
                result = _json_result(lib, lib.parakeet_capi_stream_feed_json(stream, pcm, count))
                if result is None:
                    raise RuntimeError((lib.parakeet_capi_last_error(ctx) or b"stream feed failed").decode("utf-8", "replace"))
                conn.send(("data", result))
            elif op == "finish":
                result = _json_result(lib, lib.parakeet_capi_stream_finalize_json(stream))
                if result is None:
                    raise RuntimeError((lib.parakeet_capi_last_error(ctx) or b"stream finalize failed").decode("utf-8", "replace"))
                conn.send(("data", result))
                conn.send(("done", None))
                break
            else:
                raise RuntimeError(f"unknown live ASR operation: {op}")
    except BaseException as exc:
        try:
            conn.send(("error", str(exc)))
        except Exception:
            pass
    finally:
        if lib and stream:
            lib.parakeet_capi_stream_free(stream)
        if lib and ctx:
            lib.parakeet_capi_free(ctx)
        conn.close()
        if directory:
            directory.close()


class LiveASR:
    def __init__(self, dll: Path, model: Path) -> None:
        self.dll = dll.resolve()
        self.model = model.resolve()
        self.process = None
        self.conn = None
        self.text = ""
        self.eou = False
        self.eob = False

    def start(self) -> None:
        self.close()
        parent, child = mp.Pipe()
        process = mp.Process(target=_worker, args=(child, str(self.dll), str(self.model)), daemon=True)
        process.start()
        child.close()
        kind, payload = parent.recv()
        if kind != "ready":
            process.kill()
            process.join()
            parent.close()
            raise RuntimeError(payload if kind == "error" else "live ASR failed to start")
        self.process, self.conn = process, parent
        self.text = ""
        self.eou = self.eob = False

    def _apply(self, payload: dict) -> str:
        fragment = str(payload.get("text") or "")
        if fragment:
            self.text = (self.text + " " + fragment).strip()
        self.eou = bool(payload.get("eou"))
        self.eob = bool(payload.get("eob"))
        return self.text

    def feed(self, pcm_f32: bytes) -> str:
        if not self.conn:
            raise RuntimeError("live ASR is not running")
        if not pcm_f32:
            return self.text
        self.conn.send(("feed", pcm_f32))
        kind, payload = self.conn.recv()
        if kind == "error":
            raise RuntimeError(payload)
        if kind != "data":
            raise RuntimeError(f"unexpected live ASR response: {kind}")
        return self._apply(payload)

    def finish(self) -> str:
        if not self.conn:
            return self.text
        self.conn.send(("finish", None))
        while True:
            kind, payload = self.conn.recv()
            if kind == "data":
                self._apply(payload)
            elif kind == "done":
                break
            elif kind == "error":
                raise RuntimeError(payload)
        text = self.text
        self.close()
        return text

    def close(self) -> None:
        conn, process = self.conn, self.process
        self.conn = self.process = None
        if conn:
            conn.close()
        if process:
            if process.is_alive():
                process.kill()
            process.join()
