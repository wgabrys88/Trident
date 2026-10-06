import ctypes, subprocess, sys
from contextlib import ExitStack
from agent import Trident
from core import ROOT, STATE, timestamp

if __name__ == "__main__":
    STATE.mkdir(parents=True, exist_ok=True)
    folder = ROOT / ("run_" + timestamp())
    folder.mkdir()
    with ExitStack() as resources:
        trident = Trident(folder, resources)
        try:
            trident.serve()
        except Exception as error:
            if not trident.line.owner:
                raise
            trident.line.send(f"TRIDENT -> OWNER\n\n{type(error).__name__}: {error}", direction="blocked")
    if trident.restart:
        subprocess.Popen([sys.executable, str(ROOT / "trident.py")])
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    if not kernel.TerminateProcess(kernel.GetCurrentProcess(), 0):
        raise ctypes.WinError(ctypes.get_last_error())
