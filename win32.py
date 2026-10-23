"""The hand: Win32 input, capture and window queries through ctypes.

main.py loads this file as a module and calls _setup_bindings() once. Every _do_* function takes points
as "x,y" on 0-1000 of the region string ("NONE" or "left,top,right,bottom" on 0-1000 of the screen).
"""
import ctypes
import ctypes.wintypes as W
import struct
import sys
import time
import zlib
from dataclasses import dataclass
from typing import NoReturn


@dataclass(frozen=True, slots=True)
class Win32Config:
    drag_step_count: int = 25
    drag_step_delay: float = 0.016
    click_settle_delay: float = 0.03
    type_inter_key_delay: float = 0.02
    type_down_delay: float = 0.01
    hotkey_inter_delay: float = 0.02
    scroll_click_delay: float = 0.03
    double_click_inter: float = 0.05
    overlay_alpha: int = 90
    selector_min_size: int = 5


@dataclass(frozen=True, slots=True)
class Win32Constants:
    norm: int = 1000
    sentinel: str = "NONE"
    srccopy: int = 0x00CC0020
    captureblt: int = 0x40000000
    halftone: int = 4
    left_down: int = 0x0002
    left_up: int = 0x0004
    right_down: int = 0x0008
    right_up: int = 0x0010
    mouse_move: int = 0x0001
    mouse_absolute: int = 0x8000
    mouse_wheel: int = 0x0800
    mouse_hwheel: int = 0x1000
    wheel_delta: int = 120
    keyeventf_keyup: int = 0x0002
    keyeventf_extended: int = 0x0001
    ws_ex_layered: int = 0x00080000
    ws_ex_topmost: int = 0x00000008
    ws_ex_toolwindow: int = 0x00000080
    ws_popup: int = 0x80000000
    ws_visible: int = 0x10000000
    lwa_alpha: int = 0x00000002
    wm_paint: int = 0x000F
    wm_erasebkgnd: int = 0x0014
    wm_lbuttondown: int = 0x0201
    wm_lbuttonup: int = 0x0202
    wm_mousemove: int = 0x0200
    wm_rbuttondown: int = 0x0204
    wm_keydown: int = 0x0100
    wm_close: int = 0x0010
    wm_destroy: int = 0x0002
    vk_escape: int = 0x1B
    idc_cross: int = 32515
    cs_hredraw: int = 0x0002
    cs_vredraw: int = 0x0001
    sm_cxscreen: int = 0
    sm_cyscreen: int = 1
    ps_solid: int = 0
    ps_dash: int = 1
    transparent_bk: int = 1
    null_brush: int = 5
    error_class_already_exists: int = 1410
    gwl_exstyle: int = -20
    dwmwa_cloaked: int = 14


EXTENDED_VKS: frozenset[int] = frozenset(
    {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}
)

VK_MAP: dict[str, int] = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "escape": 0x1B, "esc": 0x1B,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10,
    "win": 0x5B, "windows": 0x5B, "space": 0x20,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B,
}
for _i in range(26):
    VK_MAP[chr(ord("a") + _i)] = ord("A") + _i
for _i in range(10):
    VK_MAP[chr(ord("0") + _i)] = ord("0") + _i

CFG: Win32Config = Win32Config()
K: Win32Constants = Win32Constants()

LRESULT = ctypes.c_ssize_t
WNDPROC_TYPE = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)

HCURSOR = W.HANDLE
HICON = W.HANDLE
HMODULE = W.HANDLE

_user32: ctypes.WinDLL = ctypes.WinDLL("user32", use_last_error=True)
_gdi32: ctypes.WinDLL = ctypes.WinDLL("gdi32", use_last_error=True)
_kernel32: ctypes.WinDLL = ctypes.WinDLL("kernel32", use_last_error=True)
_dwmapi: ctypes.WinDLL = ctypes.WinDLL("dwmapi", use_last_error=True)


def _err(msg: str) -> NoReturn:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()
    raise SystemExit(1)


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG),
        ("biPlanes", W.WORD), ("biBitCount", W.WORD), ("biCompression", W.DWORD),
        ("biSizeImage", W.DWORD), ("biXPelsPerMeter", W.LONG),
        ("biYPelsPerMeter", W.LONG), ("biClrUsed", W.DWORD),
        ("biClrImportant", W.DWORD),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", W.DWORD * 3)]


class _PaintStruct(ctypes.Structure):
    _fields_ = [
        ("hdc", W.HDC), ("fErase", W.BOOL), ("rcPaint", W.RECT),
        ("fRestore", W.BOOL), ("fIncUpdate", W.BOOL),
        ("rgbReserved", W.BYTE * 32),
    ]


class _WndClassExW(ctypes.Structure):
    _fields_ = [
        ("cbSize", W.UINT), ("style", W.UINT), ("lpfnWndProc", WNDPROC_TYPE),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", W.HINSTANCE), ("hIcon", HICON), ("hCursor", HCURSOR),
        ("hbrBackground", W.HBRUSH), ("lpszMenuName", W.LPCWSTR),
        ("lpszClassName", W.LPCWSTR), ("hIconSm", HICON),
    ]


def _setup_bindings() -> None:
    _user32.GetDC.argtypes = [W.HWND]
    _user32.GetDC.restype = W.HDC
    _user32.ReleaseDC.argtypes = [W.HWND, W.HDC]
    _user32.ReleaseDC.restype = ctypes.c_int
    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _user32.GetSystemMetrics.restype = ctypes.c_int
    _gdi32.CreateCompatibleDC.argtypes = [W.HDC]
    _gdi32.CreateCompatibleDC.restype = W.HDC
    _gdi32.CreateDIBSection.argtypes = [W.HDC, ctypes.c_void_p, W.UINT, ctypes.POINTER(ctypes.c_void_p), W.HANDLE, W.DWORD]
    _gdi32.CreateDIBSection.restype = W.HBITMAP
    _gdi32.SelectObject.argtypes = [W.HDC, W.HGDIOBJ]
    _gdi32.SelectObject.restype = W.HGDIOBJ
    _gdi32.BitBlt.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HDC, ctypes.c_int, ctypes.c_int, W.DWORD]
    _gdi32.BitBlt.restype = W.BOOL
    _gdi32.StretchBlt.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.DWORD]
    _gdi32.StretchBlt.restype = W.BOOL
    _gdi32.SetStretchBltMode.argtypes = [W.HDC, ctypes.c_int]
    _gdi32.SetStretchBltMode.restype = ctypes.c_int
    _gdi32.SetBrushOrgEx.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    _gdi32.SetBrushOrgEx.restype = W.BOOL
    _gdi32.DeleteObject.argtypes = [W.HGDIOBJ]
    _gdi32.DeleteObject.restype = W.BOOL
    _gdi32.DeleteDC.argtypes = [W.HDC]
    _gdi32.DeleteDC.restype = W.BOOL
    _user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    _user32.SetCursorPos.restype = W.BOOL
    _user32.mouse_event.argtypes = [W.DWORD, W.DWORD, W.DWORD, ctypes.c_long, ctypes.c_ulong]
    _user32.mouse_event.restype = None
    _user32.keybd_event.argtypes = [W.BYTE, W.BYTE, W.DWORD, ctypes.POINTER(ctypes.c_ulong)]
    _user32.keybd_event.restype = None
    _user32.VkKeyScanW.argtypes = [W.WCHAR]
    _user32.VkKeyScanW.restype = ctypes.c_short
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(W.POINT)]
    _user32.GetCursorPos.restype = W.BOOL
    _kernel32.GetModuleHandleW.argtypes = [W.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = HMODULE
    _user32.LoadCursorW.argtypes = [W.HINSTANCE, W.LPCWSTR]
    _user32.LoadCursorW.restype = HCURSOR
    _user32.RegisterClassExW.argtypes = [ctypes.POINTER(_WndClassExW)]
    _user32.RegisterClassExW.restype = W.ATOM
    _user32.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
    _user32.CreateWindowExW.restype = W.HWND
    _user32.SetLayeredWindowAttributes.argtypes = [W.HWND, W.DWORD, W.BYTE, W.DWORD]
    _user32.SetLayeredWindowAttributes.restype = W.BOOL
    _user32.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    _user32.DefWindowProcW.restype = LRESULT
    _user32.BeginPaint.argtypes = [W.HWND, ctypes.POINTER(_PaintStruct)]
    _user32.BeginPaint.restype = W.HDC
    _user32.EndPaint.argtypes = [W.HWND, ctypes.POINTER(_PaintStruct)]
    _user32.EndPaint.restype = W.BOOL
    _user32.InvalidateRect.argtypes = [W.HWND, ctypes.c_void_p, W.BOOL]
    _user32.InvalidateRect.restype = W.BOOL
    _user32.DestroyWindow.argtypes = [W.HWND]
    _user32.DestroyWindow.restype = W.BOOL
    _user32.PostQuitMessage.argtypes = [ctypes.c_int]
    _user32.PostQuitMessage.restype = None
    _user32.GetMessageW.argtypes = [ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT]
    _user32.GetMessageW.restype = ctypes.c_int
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(W.MSG)]
    _user32.TranslateMessage.restype = W.BOOL
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(W.MSG)]
    _user32.DispatchMessageW.restype = LRESULT
    _user32.SetCapture.argtypes = [W.HWND]
    _user32.SetCapture.restype = W.HWND
    _user32.ReleaseCapture.argtypes = []
    _user32.ReleaseCapture.restype = W.BOOL
    _user32.SetForegroundWindow.argtypes = [W.HWND]
    _user32.SetForegroundWindow.restype = W.BOOL
    _user32.SetFocus.argtypes = [W.HWND]
    _user32.SetFocus.restype = W.HWND
    _user32.FillRect.argtypes = [W.HDC, ctypes.POINTER(W.RECT), W.HBRUSH]
    _user32.FillRect.restype = W.BOOL
    _user32.EnumWindows.argtypes = [ctypes.c_void_p, W.LPARAM]
    _user32.EnumWindows.restype = W.BOOL
    _user32.GetForegroundWindow.argtypes = []
    _user32.GetForegroundWindow.restype = W.HWND
    _user32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetWindowTextLengthW.argtypes = [W.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.IsWindowVisible.argtypes = [W.HWND]
    _user32.IsWindowVisible.restype = W.BOOL
    _user32.IsIconic.argtypes = [W.HWND]
    _user32.IsIconic.restype = W.BOOL
    _user32.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
    _user32.GetWindowRect.restype = W.BOOL
    _user32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
    _user32.GetWindowThreadProcessId.restype = W.DWORD
    _user32.GetWindowLongW.argtypes = [W.HWND, ctypes.c_int]
    _user32.GetWindowLongW.restype = W.LONG
    _dwmapi.DwmGetWindowAttribute.argtypes = [W.HWND, W.DWORD, ctypes.c_void_p, W.DWORD]
    _dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
    _gdi32.CreateSolidBrush.argtypes = [W.DWORD]
    _gdi32.CreateSolidBrush.restype = W.HBRUSH
    _gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, W.DWORD]
    _gdi32.CreatePen.restype = W.HGDIOBJ
    _gdi32.Rectangle.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    _gdi32.Rectangle.restype = W.BOOL
    _gdi32.SetBkMode.argtypes = [W.HDC, ctypes.c_int]
    _gdi32.SetBkMode.restype = ctypes.c_int
    _gdi32.GetStockObject.argtypes = [ctypes.c_int]
    _gdi32.GetStockObject.restype = W.HGDIOBJ
    _user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(_Input), ctypes.c_int]
    _user32.SendInput.restype = ctypes.c_uint


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ('dx', ctypes.c_long),
        ('dy', ctypes.c_long),
        ('mouseData', ctypes.c_uint),
        ('dwFlags', ctypes.c_uint),
        ('time', ctypes.c_uint),
        ('dwExtraInfo', ctypes.c_ulonglong),
    ]


class _Input(ctypes.Structure):
    _fields_ = [('type', ctypes.c_uint), ('mi', _MouseInput)]


def _clamp_norm(value: int) -> int:
    return max(0, min(K.norm, value))


def _screen_size() -> tuple[int, int]:
    return int(_user32.GetSystemMetrics(K.sm_cxscreen)), int(_user32.GetSystemMetrics(K.sm_cyscreen))


def _do_window_list() -> list[tuple[int, str, list[int], int, bool, bool, bool]]:
    """Visible, uncloaked, titled top-level windows in z-order: (hwnd, title, box 0-1000, pid, minimized, topmost, foreground)."""
    found: list[tuple[int, str, list[int], int, bool, bool, bool]] = []
    front: int = int(_user32.GetForegroundWindow() or 0)
    screen_w, screen_h = _screen_size()

    def visit(hwnd: int, _: int) -> bool:
        cloaked: W.DWORD = W.DWORD()
        _dwmapi.DwmGetWindowAttribute(hwnd, K.dwmwa_cloaked, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        length: int = _user32.GetWindowTextLengthW(hwnd)
        if not _user32.IsWindowVisible(hwnd) or cloaked.value or not length:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, title, length + 1)
        rect: W.RECT = W.RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(rect))
        pid: W.DWORD = W.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        box: list[int] = [rect.left * K.norm // screen_w, rect.top * K.norm // screen_h, rect.right * K.norm // screen_w, rect.bottom * K.norm // screen_h]
        topmost: bool = bool(_user32.GetWindowLongW(hwnd, K.gwl_exstyle) & K.ws_ex_topmost)
        found.append((int(hwnd or 0), title.value, box, int(pid.value), bool(_user32.IsIconic(hwnd)), topmost, int(hwnd or 0) == front))
        return True

    _user32.EnumWindows(ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)(visit), 0)
    return found


def _make_bmi(width: int, height: int) -> _BitmapInfo:
    info: _BitmapInfo = _BitmapInfo()
    header: _BitmapInfoHeader = info.bmiHeader
    header.biSize = ctypes.sizeof(_BitmapInfoHeader)
    header.biWidth = width
    header.biHeight = -height
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = 0
    return info


def _create_dib(device_context: int, width: int, height: int) -> tuple[int, int]:
    bits_ptr: ctypes.c_void_p = ctypes.c_void_p()
    bmi: _BitmapInfo = _make_bmi(width, height)
    bitmap_handle: int = _gdi32.CreateDIBSection(
        device_context, ctypes.byref(bmi), 0, ctypes.byref(bits_ptr), None, 0
    )
    if not bitmap_handle or not bits_ptr.value:
        _err("CreateDIBSection failed")
    return bitmap_handle, int(bits_ptr.value)


def _capture_full_screen() -> tuple[bytes, int, int]:
    screen_w, screen_h = _screen_size()
    screen_dc: int = _user32.GetDC(0)
    if not screen_dc:
        _err("capture: GetDC failed")
    mem_dc: int = _gdi32.CreateCompatibleDC(screen_dc)
    if not mem_dc:
        _user32.ReleaseDC(0, screen_dc)
        _err("capture: CreateCompatibleDC failed")
    bitmap_handle, bits_addr = _create_dib(screen_dc, screen_w, screen_h)
    old_obj: int = _gdi32.SelectObject(mem_dc, bitmap_handle)
    _gdi32.BitBlt(mem_dc, 0, 0, screen_w, screen_h, screen_dc, 0, 0, K.srccopy | K.captureblt)
    raw_bytes: bytes = bytes(
        (ctypes.c_ubyte * (screen_w * screen_h * 4)).from_address(bits_addr)
    )
    _gdi32.SelectObject(mem_dc, old_obj)
    _gdi32.DeleteObject(bitmap_handle)
    _gdi32.DeleteDC(mem_dc)
    _user32.ReleaseDC(0, screen_dc)
    return raw_bytes, screen_w, screen_h


def _parse_region(region_str: str) -> tuple[int, int, int, int]:
    if region_str == K.sentinel:
        return 0, 0, K.norm, K.norm
    parts: list[str] = region_str.split(",")
    if len(parts) != 4:
        _err(f"region must be x1,y1,x2,y2 or NONE, got: {region_str}")
    return int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])


def _parse_pos(pos_str: str) -> tuple[int, int]:
    parts: list[str] = pos_str.split(",")
    if len(parts) != 2:
        _err(f"pos must be x,y got: {pos_str}")
    return int(parts[0]), int(parts[1])


def _norm_region_to_pixels(
    norm_x1: int, norm_y1: int, norm_x2: int, norm_y2: int,
    base_w: int, base_h: int,
) -> tuple[int, int, int, int]:
    x1_val: int = _clamp_norm(norm_x1)
    y1_val: int = _clamp_norm(norm_y1)
    x2_val: int = _clamp_norm(norm_x2)
    y2_val: int = _clamp_norm(norm_y2)
    if x2_val < x1_val:
        x1_val, x2_val = x2_val, x1_val
    if y2_val < y1_val:
        y1_val, y2_val = y2_val, y1_val
    px_x1: int = max(0, min(base_w, (x1_val * base_w + K.norm // 2) // K.norm))
    px_y1: int = max(0, min(base_h, (y1_val * base_h + K.norm // 2) // K.norm))
    px_x2: int = max(0, min(base_w, (x2_val * base_w + K.norm // 2) // K.norm))
    px_y2: int = max(0, min(base_h, (y2_val * base_h + K.norm // 2) // K.norm))
    return px_x1, px_y1, px_x2, px_y2


def _norm_to_screen_pixel(
    norm_x: int, norm_y: int,
    region_x1: int, region_y1: int, region_x2: int, region_y2: int,
) -> tuple[int, int]:
    screen_w, screen_h = _screen_size()
    px_x1, px_y1, px_x2, px_y2 = _norm_region_to_pixels(
        region_x1, region_y1, region_x2, region_y2, screen_w, screen_h
    )
    crop_w: int = max(1, px_x2 - px_x1)
    crop_h: int = max(1, px_y2 - px_y1)
    clamped_x: int = _clamp_norm(norm_x)
    clamped_y: int = _clamp_norm(norm_y)
    pixel_x: int = px_x1 + (clamped_x * (crop_w - 1) + K.norm // 2) // K.norm if crop_w > 1 else px_x1
    pixel_y: int = px_y1 + (clamped_y * (crop_h - 1) + K.norm // 2) // K.norm if crop_h > 1 else px_y1
    return pixel_x, pixel_y


def _screen_pixel_to_norm(
    pixel_x: int, pixel_y: int,
    region_x1: int, region_y1: int, region_x2: int, region_y2: int,
) -> tuple[int, int]:
    screen_w, screen_h = _screen_size()
    px_x1, px_y1, px_x2, px_y2 = _norm_region_to_pixels(
        region_x1, region_y1, region_x2, region_y2, screen_w, screen_h
    )
    crop_w: int = max(1, px_x2 - px_x1)
    crop_h: int = max(1, px_y2 - px_y1)
    rel_x: int = pixel_x - px_x1
    rel_y: int = pixel_y - px_y1
    norm_x: int = _clamp_norm((rel_x * K.norm + crop_w // 2) // crop_w) if crop_w > 1 else 500
    norm_y: int = _clamp_norm((rel_y * K.norm + crop_h // 2) // crop_h) if crop_h > 1 else 500
    return norm_x, norm_y


def _stretch_region(
    bgra: bytes, src_w: int, src_h: int,
    crop_x1: int, crop_y1: int, crop_x2: int, crop_y2: int,
    dst_w: int, dst_h: int,
) -> bytes:
    crop_w: int = crop_x2 - crop_x1
    crop_h: int = crop_y2 - crop_y1
    if crop_w <= 0 or crop_h <= 0:
        _err(f"stretch_region: invalid crop {crop_w}x{crop_h}")
    screen_dc: int = _user32.GetDC(0)
    if not screen_dc:
        _err("stretch_region: GetDC failed")
    src_dc: int = _gdi32.CreateCompatibleDC(screen_dc)
    if not src_dc:
        _user32.ReleaseDC(0, screen_dc)
        _err("stretch_region: CreateCompatibleDC(src) failed")
    dst_dc: int = _gdi32.CreateCompatibleDC(screen_dc)
    if not dst_dc:
        _gdi32.DeleteDC(src_dc)
        _user32.ReleaseDC(0, screen_dc)
        _err("stretch_region: CreateCompatibleDC(dst) failed")
    src_bmp, src_bits = _create_dib(screen_dc, src_w, src_h)
    ctypes.memmove(src_bits, bgra, src_w * src_h * 4)
    old_src: int = _gdi32.SelectObject(src_dc, src_bmp)
    dst_bmp, dst_bits = _create_dib(screen_dc, dst_w, dst_h)
    old_dst: int = _gdi32.SelectObject(dst_dc, dst_bmp)
    _gdi32.SetStretchBltMode(dst_dc, K.halftone)
    _gdi32.SetBrushOrgEx(dst_dc, 0, 0, None)
    _gdi32.StretchBlt(dst_dc, 0, 0, dst_w, dst_h, src_dc, crop_x1, crop_y1, crop_w, crop_h, K.srccopy)
    result: bytes = bytes(
        (ctypes.c_ubyte * (dst_w * dst_h * 4)).from_address(dst_bits)
    )
    _gdi32.SelectObject(dst_dc, old_dst)
    _gdi32.SelectObject(src_dc, old_src)
    _gdi32.DeleteObject(dst_bmp)
    _gdi32.DeleteObject(src_bmp)
    _gdi32.DeleteDC(dst_dc)
    _gdi32.DeleteDC(src_dc)
    _user32.ReleaseDC(0, screen_dc)
    return result


def _bgra_to_png(bgra: bytes, width: int, height: int) -> bytes:
    stride: int = width * 4
    source: memoryview = memoryview(bgra)
    rows: bytearray = bytearray()
    for yidx in range(height):
        rows.append(0)
        row: memoryview = source[yidx * stride:(yidx + 1) * stride]
        for xoff in range(0, len(row), 4):
            rows.extend((row[xoff + 2], row[xoff + 1], row[xoff + 0], 255))

    def make_chunk(chunk_type: bytes, chunk_data: bytes) -> bytes:
        combined: bytes = chunk_type + chunk_data
        return (
            struct.pack(">I", len(chunk_data))
            + combined
            + struct.pack(">I", zlib.crc32(combined) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + make_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + make_chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + make_chunk(b"IEND", b"")
    )


def _do_capture(region_str: str, width: int, height: int, scale: float) -> bytes:
    bgra, src_w, src_h = _capture_full_screen()
    crop_x1: int = 0
    crop_y1: int = 0
    crop_x2: int = src_w
    crop_y2: int = src_h
    if region_str != K.sentinel:
        norm_x1, norm_y1, norm_x2, norm_y2 = _parse_region(region_str)
        crop_x1, crop_y1, crop_x2, crop_y2 = _norm_region_to_pixels(
            norm_x1, norm_y1, norm_x2, norm_y2, src_w, src_h
        )
    crop_w: int = max(1, crop_x2 - crop_x1)
    crop_h: int = max(1, crop_y2 - crop_y1)
    if scale > 0.0:
        width = max(1, int(crop_w * scale))
        height = max(1, int(crop_h * scale))
    stretched: bytes = _stretch_region(bgra, src_w, src_h, crop_x1, crop_y1, crop_x2, crop_y2, width, height)
    return _bgra_to_png(stretched, width, height)


def _resolve_screen_pos(norm_x: int, norm_y: int, region_str: str) -> tuple[int, int]:
    rx1, ry1, rx2, ry2 = _parse_region(region_str)
    return _norm_to_screen_pixel(norm_x, norm_y, rx1, ry1, rx2, ry2)


def _move_cursor(pixel_x: int, pixel_y: int) -> None:
    _user32.SetCursorPos(pixel_x, pixel_y)


def _send_mouse(flags: int, pixel_x: int, pixel_y: int) -> None:
    screen_w, screen_h = _screen_size()
    packet = _Input()
    packet.type = 0
    packet.mi.dx = int(pixel_x * 65535 / max(1, screen_w - 1))
    packet.mi.dy = int(pixel_y * 65535 / max(1, screen_h - 1))
    packet.mi.dwFlags = flags
    _user32.SendInput(1, ctypes.byref(packet), ctypes.sizeof(packet))


def _mouse_event(flags: int, data: int = 0) -> None:
    _user32.mouse_event(flags, 0, 0, data, 0)


def _key_event(vk_code: int, is_up: bool = False) -> None:
    flags: int = 0
    if is_up:
        flags |= K.keyeventf_keyup
    if vk_code in EXTENDED_VKS:
        flags |= K.keyeventf_extended
    _user32.keybd_event(vk_code, 0, flags, None)


def _click_at(pixel_x: int, pixel_y: int, down_flag: int, up_flag: int) -> None:
    _move_cursor(pixel_x, pixel_y)
    time.sleep(CFG.click_settle_delay)
    _mouse_event(down_flag)
    time.sleep(CFG.click_settle_delay)
    _mouse_event(up_flag)


def _do_click(pos_str: str, region_str: str) -> None:
    norm_x, norm_y = _parse_pos(pos_str)
    pixel_x, pixel_y = _resolve_screen_pos(norm_x, norm_y, region_str)
    _click_at(pixel_x, pixel_y, K.left_down, K.left_up)


def _do_double_click(pos_str: str, region_str: str) -> None:
    norm_x, norm_y = _parse_pos(pos_str)
    pixel_x, pixel_y = _resolve_screen_pos(norm_x, norm_y, region_str)
    _click_at(pixel_x, pixel_y, K.left_down, K.left_up)
    time.sleep(CFG.double_click_inter)
    _click_at(pixel_x, pixel_y, K.left_down, K.left_up)


def _do_right_click(pos_str: str, region_str: str) -> None:
    norm_x, norm_y = _parse_pos(pos_str)
    pixel_x, pixel_y = _resolve_screen_pos(norm_x, norm_y, region_str)
    _click_at(pixel_x, pixel_y, K.right_down, K.right_up)


def _vk_scan(char: str) -> int:
    result: int = int(_user32.VkKeyScanW(char))
    if result == -1:
        _err(f"unmappable character: {repr(char)}")
    return result


def _do_type_text(text: str) -> None:
    for char in text:
        vk_scan: int = _vk_scan(char)
        vk_code: int = vk_scan & 0xFF
        need_shift: bool = bool(vk_scan & 0x100)
        need_ctrl: bool = bool(vk_scan & 0x200)
        need_alt: bool = bool(vk_scan & 0x400)
        if need_ctrl:
            _key_event(0x11)
        if need_alt:
            _key_event(0x12)
        if need_shift:
            _key_event(0x10)
        _key_event(vk_code)
        time.sleep(CFG.type_down_delay)
        _key_event(vk_code, True)
        if need_shift:
            _key_event(0x10, True)
        if need_alt:
            _key_event(0x12, True)
        if need_ctrl:
            _key_event(0x11, True)
        time.sleep(CFG.type_inter_key_delay)


def _do_hotkey(keys_str: str) -> None:
    key_names: list[str] = []
    for part in keys_str.replace(",", "+").replace(" ", "+").split("+"):
        stripped: str = part.strip().lower()
        if stripped:
            key_names.append(stripped)
    if not key_names:
        _err(f"hotkey: no valid key components in: {keys_str}")
    vk_codes: list[int] = []
    for name in key_names:
        vk_code: int | None = VK_MAP.get(name)
        if vk_code is not None:
            vk_codes.append(vk_code)
        elif len(name) == 1:
            vk_scan: int = _vk_scan(name)
            vk_codes.append(vk_scan & 0xFF)
        else:
            _err(f"unknown hotkey component: {name}")
    for vk_code_val in vk_codes:
        _key_event(vk_code_val)
        time.sleep(CFG.hotkey_inter_delay)
    for vk_code_val in reversed(vk_codes):
        _key_event(vk_code_val, True)
        time.sleep(CFG.hotkey_inter_delay)


def _do_scroll(pos_str: str, region_str: str, direction: int, clicks: int) -> None:
    if clicks <= 0:
        return
    norm_x, norm_y = _parse_pos(pos_str)
    pixel_x, pixel_y = _resolve_screen_pos(norm_x, norm_y, region_str)
    _move_cursor(pixel_x, pixel_y)
    time.sleep(CFG.click_settle_delay)
    for _ in range(clicks):
        _mouse_event(K.mouse_wheel, direction * K.wheel_delta)
        time.sleep(CFG.scroll_click_delay)


def _do_wheel(pos_str: str, region_str: str, amount: int, horizontal: bool = False) -> None:
    amount = int(amount)
    if not amount:
        return
    norm_x, norm_y = _parse_pos(pos_str)
    pixel_x, pixel_y = _resolve_screen_pos(norm_x, norm_y, region_str)
    _move_cursor(pixel_x, pixel_y)
    time.sleep(CFG.click_settle_delay)
    _mouse_event(K.mouse_hwheel if horizontal else K.mouse_wheel, amount)


def _do_stroke(point_strs: list[str], region_str: str) -> None:
    pixels: list[tuple[int, int]] = []
    for pos in point_strs:
        norm_x, norm_y = _parse_pos(pos)
        pixels.append(_resolve_screen_pos(norm_x, norm_y, region_str))
    if len(pixels) < 2:
        _err("stroke needs >=2 points")
    steps: int = max(1, CFG.drag_step_count)
    move, absolute, down, up = K.mouse_move, K.mouse_absolute, K.left_down, K.left_up
    _send_mouse(move | absolute, pixels[0][0], pixels[0][1])
    time.sleep(CFG.click_settle_delay)
    _send_mouse(down | absolute, pixels[0][0], pixels[0][1])
    time.sleep(CFG.click_settle_delay)
    for (x0, y0), (x1, y1) in zip(pixels, pixels[1:]):
        for step_idx in range(1, steps + 1):
            _send_mouse(move | absolute, x0 + (x1 - x0) * step_idx // steps, y0 + (y1 - y0) * step_idx // steps)
            time.sleep(CFG.drag_step_delay)
    time.sleep(CFG.click_settle_delay)
    _send_mouse(up | absolute, pixels[-1][0], pixels[-1][1])


def _do_cursor_pos(region_str: str) -> str:
    point: W.POINT = W.POINT()
    _user32.GetCursorPos(ctypes.byref(point))
    rx1, ry1, rx2, ry2 = _parse_region(region_str)
    norm_x, norm_y = _screen_pixel_to_norm(point.x, point.y, rx1, ry1, rx2, ry2)
    return f"{norm_x},{norm_y}"


_selector_dragging: bool = False
_selector_sx: int = 0
_selector_sy: int = 0
_selector_ex: int = 0
_selector_ey: int = 0
_selector_result: tuple[int, int, int, int] | None = None
_selector_exit_code: int = 2
_selector_wndproc_ref: WNDPROC_TYPE | None = None
_selector_screen_w: int = 0
_selector_screen_h: int = 0
_selector_null_brush: int = 0


def _selector_get_xy(lparam: int) -> tuple[int, int]:
    xval: int = lparam & 0xFFFF
    yval: int = (lparam >> 16) & 0xFFFF
    if xval > 32767:
        xval -= 65536
    if yval > 32767:
        yval -= 65536
    return xval, yval


def _selector_wndproc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
    global _selector_dragging, _selector_sx, _selector_sy
    global _selector_ex, _selector_ey, _selector_result, _selector_exit_code

    if msg == K.wm_erasebkgnd:
        return 1
    if msg == K.wm_keydown:
        if int(wparam) == K.vk_escape:
            _selector_result = None
            _selector_exit_code = 2
            _user32.DestroyWindow(hwnd)
            return 0
    if msg == K.wm_rbuttondown:
        _selector_result = None
        _selector_exit_code = 0
        _user32.DestroyWindow(hwnd)
        return 0
    if msg == K.wm_close:
        _selector_result = None
        _selector_exit_code = 2
        _user32.DestroyWindow(hwnd)
        return 0
    if msg == K.wm_lbuttondown:
        _selector_sx, _selector_sy = _selector_get_xy(lparam)
        _selector_ex, _selector_ey = _selector_sx, _selector_sy
        _selector_dragging = True
        _user32.SetCapture(hwnd)
        _user32.InvalidateRect(hwnd, None, True)
        return 0
    if msg == K.wm_mousemove:
        if _selector_dragging:
            _selector_ex, _selector_ey = _selector_get_xy(lparam)
            _user32.InvalidateRect(hwnd, None, True)
        return 0
    if msg == K.wm_lbuttonup:
        if _selector_dragging:
            _selector_ex, _selector_ey = _selector_get_xy(lparam)
            _selector_dragging = False
            _user32.ReleaseCapture()
            rect_x1: int = min(_selector_sx, _selector_ex)
            rect_y1: int = min(_selector_sy, _selector_ey)
            rect_x2: int = max(_selector_sx, _selector_ex)
            rect_y2: int = max(_selector_sy, _selector_ey)
            if (abs(rect_x2 - rect_x1) > CFG.selector_min_size
                    and abs(rect_y2 - rect_y1) > CFG.selector_min_size):
                _selector_result = (rect_x1, rect_y1, rect_x2, rect_y2)
                _selector_exit_code = 0
                _user32.DestroyWindow(hwnd)
            else:
                _user32.InvalidateRect(hwnd, None, True)
        return 0
    if msg == K.wm_paint:
        paint_struct: _PaintStruct = _PaintStruct()
        hdc: int = _user32.BeginPaint(hwnd, ctypes.byref(paint_struct))
        brush_bg: int = _gdi32.CreateSolidBrush(0x00000000)
        rect_full: W.RECT = W.RECT(0, 0, _selector_screen_w, _selector_screen_h)
        _user32.FillRect(hdc, ctypes.byref(rect_full), brush_bg)
        _gdi32.DeleteObject(brush_bg)
        if _selector_dragging or (_selector_ex != _selector_sx or _selector_ey != _selector_sy):
            draw_x1: int = min(_selector_sx, _selector_ex)
            draw_y1: int = min(_selector_sy, _selector_ey)
            draw_x2: int = max(_selector_sx, _selector_ex)
            draw_y2: int = max(_selector_sy, _selector_ey)
            pen_white: int = _gdi32.CreatePen(K.ps_solid, 3, 0x00FFFFFF)
            pen_green: int = _gdi32.CreatePen(K.ps_dash, 1, 0x0000FF00)
            old_pen: int = _gdi32.SelectObject(hdc, pen_white)
            old_brush: int = _gdi32.SelectObject(hdc, _selector_null_brush)
            _gdi32.SetBkMode(hdc, K.transparent_bk)
            _gdi32.Rectangle(hdc, draw_x1, draw_y1, draw_x2, draw_y2)
            _gdi32.SelectObject(hdc, pen_green)
            _gdi32.Rectangle(hdc, draw_x1 - 2, draw_y1 - 2, draw_x2 + 2, draw_y2 + 2)
            _gdi32.SelectObject(hdc, old_pen)
            _gdi32.SelectObject(hdc, old_brush)
            _gdi32.DeleteObject(pen_white)
            _gdi32.DeleteObject(pen_green)
        _user32.EndPaint(hwnd, ctypes.byref(paint_struct))
        return 0
    if msg == K.wm_destroy:
        _user32.PostQuitMessage(0)
        return 0
    return int(_user32.DefWindowProcW(hwnd, msg, wparam, lparam))


def _do_select_region() -> tuple[str, int]:
    global _selector_wndproc_ref, _selector_result, _selector_exit_code
    global _selector_screen_w, _selector_screen_h, _selector_null_brush
    global _selector_dragging, _selector_sx, _selector_sy
    global _selector_ex, _selector_ey

    _selector_dragging = False
    _selector_sx = 0
    _selector_sy = 0
    _selector_ex = 0
    _selector_ey = 0
    _selector_result = None
    _selector_exit_code = 2

    _selector_screen_w, _selector_screen_h = _screen_size()

    _selector_null_brush = _gdi32.GetStockObject(K.null_brush)

    hinst: int = _kernel32.GetModuleHandleW(None)
    class_name: str = "ScreenRegionSelector"
    _selector_wndproc_ref = WNDPROC_TYPE(_selector_wndproc)

    wnd_class: _WndClassExW = _WndClassExW()
    wnd_class.cbSize = ctypes.sizeof(_WndClassExW)
    wnd_class.style = K.cs_hredraw | K.cs_vredraw
    wnd_class.lpfnWndProc = _selector_wndproc_ref
    wnd_class.cbClsExtra = 0
    wnd_class.cbWndExtra = 0
    wnd_class.hInstance = hinst
    wnd_class.hIcon = 0
    wnd_class.hCursor = _user32.LoadCursorW(None, ctypes.cast(K.idc_cross, W.LPCWSTR))
    wnd_class.hbrBackground = 0
    wnd_class.lpszMenuName = None
    wnd_class.lpszClassName = class_name
    wnd_class.hIconSm = 0

    atom: int = _user32.RegisterClassExW(ctypes.byref(wnd_class))
    if not atom:
        last_err: int = ctypes.get_last_error()
        if last_err != K.error_class_already_exists:
            _err(f"RegisterClassExW failed: {last_err}")

    ex_style: int = K.ws_ex_layered | K.ws_ex_topmost | K.ws_ex_toolwindow
    hwnd: int = _user32.CreateWindowExW(
        ex_style, class_name, "Region Select",
        K.ws_popup | K.ws_visible, 0, 0,
        _selector_screen_w, _selector_screen_h,
        None, None, hinst, None,
    )
    if not hwnd:
        _err("CreateWindowExW failed")

    _user32.SetLayeredWindowAttributes(hwnd, 0, CFG.overlay_alpha, K.lwa_alpha)
    _user32.SetForegroundWindow(hwnd)
    _user32.SetFocus(hwnd)

    msg_struct: W.MSG = W.MSG()
    while True:
        result: int = _user32.GetMessageW(ctypes.byref(msg_struct), None, 0, 0)
        if result == -1 or result == 0:
            break
        _user32.TranslateMessage(ctypes.byref(msg_struct))
        _user32.DispatchMessageW(ctypes.byref(msg_struct))

    if _selector_result is not None:
        px_x1, px_y1, px_x2, px_y2 = _selector_result
        norm_x1, norm_y1 = _screen_pixel_to_norm(px_x1, px_y1, 0, 0, K.norm, K.norm)
        norm_x2, norm_y2 = _screen_pixel_to_norm(px_x2, px_y2, 0, 0, K.norm, K.norm)
        return f"{norm_x1},{norm_y1},{norm_x2},{norm_y2}", 0
    return K.sentinel, _selector_exit_code
