"""Private launcher: join the precreated job before starting any vendor code."""
import os, subprocess, sys


def launch(name, argv):
    import ctypes
    from ctypes import wintypes as W
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenJobObjectW.argtypes = [W.DWORD, W.BOOL, W.LPCWSTR]
    api.OpenJobObjectW.restype = W.HANDLE
    api.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
    api.AssignProcessToJobObject.restype = W.BOOL
    api.GetCurrentProcess.restype = W.HANDLE
    api.CloseHandle.argtypes = [W.HANDLE]
    handle = api.OpenJobObjectW(0x0001, False, name)  # JOB_OBJECT_ASSIGN_PROCESS
    if not handle:
        raise RuntimeError('bootstrap could not open job; vendor was not launched')
    try:
        if not api.AssignProcessToJobObject(handle, api.GetCurrentProcess()):
            raise RuntimeError('bootstrap could not join job; vendor was not launched')
    finally:
        api.CloseHandle(handle)
    # Only the parent owns a job handle now; closing it kills this tree.
    return subprocess.call(argv, stdin=subprocess.DEVNULL, creationflags=0x08000000 if os.name == 'nt' else 0)


if __name__ == '__main__':
    try:
        sys.exit(launch(sys.argv[1], sys.argv[2:]))
    except Exception as error:
        print(str(error), file=sys.stderr, flush=True)
        sys.exit(1)
