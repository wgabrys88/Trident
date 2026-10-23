"""The watch: a windowless 4 Hz screen sampler that publishes scan.json for the hand's gate.

It grabs the full screen, shrinks it to 24-pixel cells, learns which cells blink by themselves (rhythmic)
and records an alert when a block of at least MIN_CELLS quiet cells changes by ALERT or more.
One instance per directory (scan.pid with a heartbeat); scan.stop ends it; a crash lands in scan.err.
"""
import ctypes, json, os, time, traceback
from collections import deque
from pathlib import Path

ctypes.WinDLL('shcore', use_last_error=True).SetProcessDpiAwareness(2)

import cv2
import numpy as np

import win32

ROOT = Path(__file__).resolve().parent
CELL, HZ, WINDOW, THRESH, ALERT, MIN_CELLS, RHYTHM, HISTORY, STALE = 24, 4, 16, 1, 8, 6, 2, 16, 1.5
NORM = win32.K.norm
PID, OUT, STOP, ERR = ROOT / 'scan.pid', ROOT / 'scan.json', ROOT / 'scan.stop', ROOT / 'scan.err'

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

def beating():
    try: return time.time() - OUT.stat().st_mtime < STALE
    except OSError: return False

def claim():
    """Take scan.pid unless a live process holds it and scan.json is still beating."""
    try: fd = os.open(PID, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try: old = int(PID.read_text('utf-8').strip() or '0')
        except (OSError, ValueError): old = 0
        if old and alive(old) and beating(): return False
        try: PID.unlink()
        except OSError: return False
        try: fd = os.open(PID, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError: return False
    os.write(fd, str(os.getpid()).encode()); os.close(fd)
    return True

def publish(payload):
    """Write scan.json atomically; False (frame skipped) while a reader holds it."""
    tmp = OUT.with_suffix('.tmp')
    try:
        tmp.write_text(json.dumps(payload, separators=(',', ':')), encoding='utf-8')
        tmp.replace(OUT)
    except PermissionError:
        return False
    return True

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
    """Turns successive small gray frames into a report: rate per cell, rhythmic boxes, the last HISTORY alerts."""
    def __init__(self):
        self.prev, self.diffs, self.alerts = None, [], deque(maxlen=HISTORY)

    def step(self, small):
        now = time.time()
        if self.prev is None or self.prev.shape != small.shape:
            self.prev, self.diffs = small.copy(), []
            return self._report(now)
        delta = cv2.absdiff(small, self.prev)
        self.diffs.append(delta >= THRESH)
        self.prev = small.copy()
        if len(self.diffs) > WINDOW: del self.diffs[0]
        if len(self.diffs) >= WINDOW:
            quiet = np.stack(self.diffs[:-1], 0).sum(0) == 0
            found = components((delta >= ALERT) & quiet, small.shape[1], small.shape[0], MIN_CELLS)
            if found:
                best = max(found, key=lambda item: item['cells'])
                self.alerts.append(dict(box=best['box'], cells=best['cells'], at=round(now, 3)))
        return self._report(now)

    def _report(self, now):
        rows, cols = (0, 0) if self.prev is None else self.prev.shape
        if self.diffs:
            count = np.stack(self.diffs, 0).sum(0)
            rate = (count * NORM // len(self.diffs)).astype(int).ravel().tolist()
            rhythmic = [item['box'] for item in components(count >= RHYTHM, cols, rows, 1)] if len(self.diffs) >= WINDOW else []
        else:
            rate, rhythmic = [], []
        return dict(grid=[cols, rows], samples=len(self.diffs), kind='live' if len(self.diffs) >= WINDOW else 'warming', rate=rate, rhythmic=rhythmic, alerts=list(self.alerts), at=round(now, 3))

def watch():
    """Run the watch until scan.stop appears."""
    if not claim(): return
    ERR.unlink(missing_ok=True)
    eye, period, skipped = Eye(), 1 / HZ, 0
    try:
        win32._setup_bindings()
        while not STOP.exists():
            started = time.perf_counter()
            skipped += not publish(eye.step(grab()) | dict(pid=os.getpid(), skipped=skipped))
            left = period - (time.perf_counter() - started)
            if left > 0: time.sleep(left)
    except Exception:
        ERR.write_text(traceback.format_exc(), encoding='utf-8')
        raise
    finally:
        PID.unlink(missing_ok=True)
        STOP.unlink(missing_ok=True)

if __name__ == '__main__':
    watch()
