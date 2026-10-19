"""Read the guest Chrome window through UI Automation.

Wake 2: Chrome stable does not expose page content unless it is launched with
--force-renderer-accessibility. DevTools port 9222 still does not listen.
Coordinates below are physical pixels. dpi() must run before reading rects.
"""
import ctypes
from ctypes import wintypes

import uiautomation as auto

u = ctypes.WinDLL('user32', use_last_error=True)
u.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
u.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p


def dpi():
    u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))


def chrome_hwnd():
    dpi()
    u.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _l):
        if not u.IsWindowVisible(h):
            return True
        n = u.GetWindowTextLengthW(h)
        if n < 8:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, buf, n + 1)
        title = buf.value
        if 'Chess.com' in title and 'Google Chrome' in title:
            found.append(int(h))
        return True

    u.EnumWindows(cb, 0)
    if not found:
        raise RuntimeError('no visible Chess.com Chrome window')
    return found[0]


def walk(max_depth=24):
    win = auto.ControlFromHandle(chrome_hwnd())
    rows = []
    for ctrl, depth in auto.WalkControl(win, maxDepth=max_depth):
        name = (ctrl.Name or '').strip().replace('\n', ' ')
        if not name:
            continue
        rect = ctrl.BoundingRectangle
        if rect.width() < 4 or rect.height() < 4:
            continue
        rows.append(dict(depth=depth, type=ctrl.ControlTypeName, name=name,
                          left=rect.left, top=rect.top, width=rect.width(), height=rect.height()))
    return rows


def find(label, type_name=None):
    want = label.lower()
    hits = []
    for row in walk():
        if want not in row['name'].lower():
            continue
        if type_name and row['type'] != type_name:
            continue
        row = dict(row)
        row['cx'] = row['left'] + row['width'] / 2
        row['cy'] = row['top'] + row['height'] / 2
        row['x1000'] = round(row['cx'] * 1000 / 1920)
        row['y1000'] = round(row['cy'] * 1000 / 1080)
        hits.append(row)
    hits.sort(key=lambda r: (r['name'].lower() != want, len(r['name'])))
    return hits


if __name__ == '__main__':
    import json, sys
    if len(sys.argv) > 1:
        print(json.dumps(find(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None), indent=2))
    else:
        for row in walk():
            if row['top'] < 90 or row['left'] < 0:
                continue
            print('%4d,%4d %4dx%-4d %-16s %s' % (row['left'], row['top'], row['width'], row['height'], row['type'][:16], row['name'][:110]))
