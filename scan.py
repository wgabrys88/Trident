import ctypes, json, os, time
from pathlib import Path

ctypes.WinDLL('shcore', use_last_error=True).SetProcessDpiAwareness(2)

import cv2
import numpy as np

import win32

ROOT = Path(__file__).resolve().parent
CELL, HZ, WINDOW, THRESH, ALERT, MIN_CELLS, RHYTHM, HOLD = 24, 4, 16, 1, 8, 6, 2, 2.0
NORM = win32.K.norm
PID, OUT, STOP = ROOT / 'scan.pid', ROOT / 'scan.json', ROOT / 'scan.stop'

def alive(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel.CloseHandle.restype = ctypes.c_int
    handle = kernel.OpenProcess(0x1000, 0, int(pid))
    if not handle: return False
    kernel.CloseHandle(handle)
    return True

def claim():
    try: fd = os.open(PID, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try: old = int(PID.read_text('utf-8').strip() or '0')
        except (OSError, ValueError): old = 0
        if old and alive(old): return False
        try: PID.unlink()
        except OSError: return False
        try: fd = os.open(PID, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError: return False
    os.write(fd, str(os.getpid()).encode()); os.close(fd)
    return True

def publish(payload):
    tmp = OUT.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, separators=(',', ':')), encoding='utf-8')
    for _ in range(20):
        try:
            tmp.replace(OUT)
            return
        except PermissionError:
            time.sleep(0.01)
    tmp.replace(OUT)

def grab():
    raw, width, height = win32._capture_full_screen()
    frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
    return cv2.resize(gray, (max(8, width // CELL), max(8, height // CELL)), interpolation=cv2.INTER_AREA)

def components(mask, cols, rows, min_cells):
    count, _, stats, _ = cv2.connectedComponentsWithStats(np.ascontiguousarray(mask.astype(np.uint8)), 8)
    found = []
    for index in range(1, int(count)):
        x, y, w, h, area = (int(v) for v in stats[index])
        if area < min_cells: continue
        found.append(dict(box=[x * NORM // cols, y * NORM // rows, (x + w) * NORM // cols, (y + h) * NORM // rows], cells=area))
    return found

class Eye:
    def __init__(self):
        self.prev, self.diffs, self.hold = None, [], None

    def step(self, small):
        now = time.time()
        if self.prev is None or self.prev.shape != small.shape:
            self.prev, self.diffs, self.hold = small.copy(), [], None
            return self._report(None, now)
        delta = cv2.absdiff(small, self.prev)
        self.diffs.append(delta >= THRESH)
        self.prev = small.copy()
        if len(self.diffs) > WINDOW: del self.diffs[0]
        fresh = None
        if len(self.diffs) >= WINDOW:
            quiet = np.stack(self.diffs[:-1], 0).sum(0) == 0
            found = components((delta >= ALERT) & quiet, small.shape[1], small.shape[0], MIN_CELLS)
            if found:
                best = max(found, key=lambda item: item['cells'])
                fresh = dict(box=best['box'], cells=best['cells'], at=round(now, 3), until=round(now + HOLD, 3))
        return self._report(self._latch(fresh, now), now)

    def _latch(self, fresh, now):
        if fresh:
            self.hold = fresh
            return fresh
        if self.hold and now < self.hold['until']: return self.hold
        self.hold = None
        return None

    def _report(self, alert, now):
        rows, cols = (0, 0) if self.prev is None else self.prev.shape
        if self.diffs:
            count = np.stack(self.diffs, 0).sum(0)
            rate = (count * NORM // len(self.diffs)).astype(int).ravel().tolist()
            rhythmic = [item['box'] for item in components(count >= RHYTHM, cols, rows, 1)] if len(self.diffs) >= WINDOW else []
        else:
            rate, rhythmic = [], []
        return dict(grid=[cols, rows], samples=len(self.diffs), kind='live' if len(self.diffs) >= WINDOW else 'warming', rate=rate, rhythmic=rhythmic, alert=alert, at=round(now, 3))

def watch():
    if not claim(): return
    win32._setup_bindings()
    eye, period = Eye(), 1 / HZ
    try:
        while not STOP.exists():
            started = time.perf_counter()
            publish(eye.step(grab()))
            left = period - (time.perf_counter() - started)
            if left > 0: time.sleep(left)
    finally:
        PID.unlink(missing_ok=True)

if __name__ == '__main__':
    watch()
