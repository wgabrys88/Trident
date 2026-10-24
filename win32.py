import ctypes, struct, time, zlib
from ctypes import wintypes as W

NORM = 1000
SRCCOPY, CAPTUREBLT, HALFTONE = 0x00CC0020, 0x40000000, 4
MOVE, LEFT_DOWN, LEFT_UP, RIGHT_DOWN, RIGHT_UP, WHEEL, HWHEEL, ABSOLUTE = 0x0001, 0x0002, 0x0004, 0x0008, 0x0010, 0x0800, 0x1000, 0x8000
KEYUP, EXTENDED = 0x0002, 0x0001
SETTLE, DOUBLE_GAP, TYPE_DOWN, TYPE_GAP, HOTKEY_GAP, SCROLL_GAP, DRAG_GAP, DRAG_STEPS = 0.03, 0.05, 0.01, 0.02, 0.02, 0.03, 0.016, 25
WM_DESTROY, WM_PAINT, WM_CLOSE, WM_ERASEBKGND, WM_KEYDOWN, WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN = 0x0002, 0x000F, 0x0010, 0x0014, 0x0100, 0x0200, 0x0201, 0x0202, 0x0204
EXTENDED_VKS = frozenset((0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E))
VK = {
    'enter': 0x0D, 'return': 0x0D, 'tab': 0x09, 'escape': 0x1B, 'esc': 0x1B,
    'backspace': 0x08, 'delete': 0x2E, 'del': 0x2E, 'insert': 0x2D,
    'home': 0x24, 'end': 0x23, 'pageup': 0x21, 'pagedown': 0x22,
    'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27,
    'ctrl': 0x11, 'control': 0x11, 'alt': 0x12, 'shift': 0x10,
    'win': 0x5B, 'windows': 0x5B, 'space': 0x20,
} | {f'f{i}': 0x6F + i for i in range(1, 13)} | {c: ord(c.upper()) for c in 'abcdefghijklmnopqrstuvwxyz0123456789'}

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)


class BitmapInfoHeader(ctypes.Structure):
    _fields_ = [('biSize', W.DWORD), ('biWidth', W.LONG), ('biHeight', W.LONG), ('biPlanes', W.WORD), ('biBitCount', W.WORD), ('biCompression', W.DWORD),
                ('biSizeImage', W.DWORD), ('biXPelsPerMeter', W.LONG), ('biYPelsPerMeter', W.LONG), ('biClrUsed', W.DWORD), ('biClrImportant', W.DWORD)]


class PaintStruct(ctypes.Structure):
    _fields_ = [('hdc', W.HDC), ('fErase', W.BOOL), ('rcPaint', W.RECT), ('fRestore', W.BOOL), ('fIncUpdate', W.BOOL), ('rgbReserved', W.BYTE * 32)]


class WndClassExW(ctypes.Structure):
    _fields_ = [('cbSize', W.UINT), ('style', W.UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', ctypes.c_int), ('cbWndExtra', ctypes.c_int), ('hInstance', W.HINSTANCE),
                ('hIcon', W.HANDLE), ('hCursor', W.HANDLE), ('hbrBackground', W.HBRUSH), ('lpszMenuName', W.LPCWSTR), ('lpszClassName', W.LPCWSTR), ('hIconSm', W.HANDLE)]


class MouseInput(ctypes.Structure):
    _fields_ = [('dx', ctypes.c_long), ('dy', ctypes.c_long), ('mouseData', ctypes.c_uint), ('dwFlags', ctypes.c_uint), ('time', ctypes.c_uint), ('dwExtraInfo', ctypes.c_ulonglong)]


class Input(ctypes.Structure):
    _fields_ = [('type', ctypes.c_uint), ('mi', MouseInput)]


class JobBasicLimits(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64), ('LimitFlags', W.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t), ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', W.DWORD), ('Affinity', ctypes.c_size_t), ('PriorityClass', W.DWORD), ('SchedulingClass', W.DWORD)]


class JobLimits(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', JobBasicLimits), ('IoInfo', ctypes.c_uint64 * 6), ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]


def bind(name, table):
    dll = ctypes.WinDLL(name, use_last_error=True)
    for function, restype, *argtypes in table:
        getattr(dll, function).restype = restype
        getattr(dll, function).argtypes = argtypes
    return dll


INT, P = W.INT, ctypes.POINTER
user32 = bind('user32', [
    ('GetDC', W.HDC, W.HWND), ('ReleaseDC', INT, W.HWND, W.HDC), ('GetSystemMetrics', INT, INT),
    ('SetCursorPos', W.BOOL, INT, INT), ('GetCursorPos', W.BOOL, P(W.POINT)),
    ('mouse_event', None, W.DWORD, W.DWORD, W.DWORD, ctypes.c_long, ctypes.c_ulong), ('SendInput', ctypes.c_uint, ctypes.c_uint, P(Input), INT),
    ('keybd_event', None, W.BYTE, W.BYTE, W.DWORD, P(ctypes.c_ulong)), ('VkKeyScanW', ctypes.c_short, W.WCHAR),
    ('LoadCursorW', W.HANDLE, W.HINSTANCE, W.LPCWSTR), ('RegisterClassExW', W.ATOM, P(WndClassExW)),
    ('CreateWindowExW', W.HWND, W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, INT, INT, INT, INT, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID),
    ('SetLayeredWindowAttributes', W.BOOL, W.HWND, W.DWORD, W.BYTE, W.DWORD), ('DefWindowProcW', LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM),
    ('BeginPaint', W.HDC, W.HWND, P(PaintStruct)), ('EndPaint', W.BOOL, W.HWND, P(PaintStruct)), ('InvalidateRect', W.BOOL, W.HWND, ctypes.c_void_p, W.BOOL),
    ('DestroyWindow', W.BOOL, W.HWND), ('PostQuitMessage', None, INT), ('GetMessageW', INT, P(W.MSG), W.HWND, W.UINT, W.UINT),
    ('TranslateMessage', W.BOOL, P(W.MSG)), ('DispatchMessageW', LRESULT, P(W.MSG)), ('SetCapture', W.HWND, W.HWND), ('ReleaseCapture', W.BOOL),
    ('SetForegroundWindow', W.BOOL, W.HWND), ('SetFocus', W.HWND, W.HWND), ('FillRect', W.BOOL, W.HDC, P(W.RECT), W.HBRUSH),
    ('EnumWindows', W.BOOL, ctypes.c_void_p, W.LPARAM), ('GetForegroundWindow', W.HWND), ('GetWindowTextW', INT, W.HWND, W.LPWSTR, INT),
    ('GetWindowTextLengthW', INT, W.HWND), ('IsWindowVisible', W.BOOL, W.HWND), ('IsIconic', W.BOOL, W.HWND), ('GetWindowRect', W.BOOL, W.HWND, P(W.RECT)),
    ('GetWindowThreadProcessId', W.DWORD, W.HWND, P(W.DWORD)), ('GetWindowLongW', W.LONG, W.HWND, INT),
    ('ShowWindow', W.BOOL, W.HWND, INT), ('SetWindowPos', W.BOOL, W.HWND, ctypes.c_void_p, INT, INT, INT, INT, W.UINT),
])
gdi32 = bind('gdi32', [
    ('CreateCompatibleDC', W.HDC, W.HDC), ('CreateDIBSection', W.HBITMAP, W.HDC, ctypes.c_void_p, W.UINT, P(ctypes.c_void_p), W.HANDLE, W.DWORD),
    ('SelectObject', W.HGDIOBJ, W.HDC, W.HGDIOBJ), ('DeleteObject', W.BOOL, W.HGDIOBJ), ('DeleteDC', W.BOOL, W.HDC),
    ('BitBlt', W.BOOL, W.HDC, INT, INT, INT, INT, W.HDC, INT, INT, W.DWORD),
    ('StretchBlt', W.BOOL, W.HDC, INT, INT, INT, INT, W.HDC, INT, INT, INT, INT, W.DWORD),
    ('SetStretchBltMode', INT, W.HDC, INT), ('SetBrushOrgEx', W.BOOL, W.HDC, INT, INT, ctypes.c_void_p),
    ('CreateSolidBrush', W.HBRUSH, W.DWORD), ('CreatePen', W.HGDIOBJ, INT, INT, W.DWORD), ('Rectangle', W.BOOL, W.HDC, INT, INT, INT, INT),
    ('SetBkMode', INT, W.HDC, INT), ('GetStockObject', W.HGDIOBJ, INT),
])
kernel32 = bind('kernel32', [
    ('GetModuleHandleW', W.HANDLE, W.LPCWSTR), ('OpenProcess', W.HANDLE, W.DWORD, W.BOOL, W.DWORD), ('CloseHandle', W.BOOL, W.HANDLE),
    ('CreateJobObjectW', W.HANDLE, W.LPVOID, W.LPCWSTR), ('SetInformationJobObject', W.BOOL, W.HANDLE, INT, W.LPVOID, W.DWORD), ('AssignProcessToJobObject', W.BOOL, W.HANDLE, W.HANDLE),
])
dwmapi = bind('dwmapi', [('DwmGetWindowAttribute', ctypes.c_long, W.HWND, W.DWORD, ctypes.c_void_p, W.DWORD)])
ctypes.WinDLL('shcore').SetProcessDpiAwareness(2)


def screen_size():
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def to_pixels(box, width, height):
    return [(v * (height if i % 2 else width) + NORM // 2) // NORM for i, v in enumerate(box)]


def region_pixels(region):
    return to_pixels((0, 0, NORM, NORM) if region == 'NONE' else map(int, region.split(',')), *screen_size())


def clamp(value):
    return max(0, min(NORM, value))


def point(x, y, region):
    x1, y1, x2, y2 = region_pixels(region)
    return x1 + (clamp(x) * (x2 - x1 - 1) + NORM // 2) // NORM, y1 + (clamp(y) * (y2 - y1 - 1) + NORM // 2) // NORM


def to_norm(px, py, region):
    x1, y1, x2, y2 = region_pixels(region)
    return clamp(((px - x1) * NORM + (x2 - x1) // 2) // (x2 - x1)), clamp(((py - y1) * NORM + (y2 - y1) // 2) // (y2 - y1))


def windows():
    found, front, (width, height) = [], user32.GetForegroundWindow(), screen_size()

    def visit(hwnd, _):
        cloaked, length = W.DWORD(), user32.GetWindowTextLengthW(hwnd)
        dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if user32.IsWindowVisible(hwnd) and not cloaked.value and length:
            title, rect, pid = ctypes.create_unicode_buffer(length + 1), W.RECT(), W.DWORD()
            user32.GetWindowTextW(hwnd, title, length + 1)
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append(dict(hwnd=hwnd, title=title.value, box=[rect.left * NORM // width, rect.top * NORM // height, rect.right * NORM // width, rect.bottom * NORM // height],
                              pid=pid.value, minimized=bool(user32.IsIconic(hwnd)), topmost=bool(user32.GetWindowLongW(hwnd, -20) & 0x00000008), foreground=hwnd == front))
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)(visit), 0)
    return found


def place(hwnd, left, top, right, bottom):
    width, height = screen_size()
    left, top, right, bottom = (int(float(v)) for v in (left, top, right, bottom))
    if not (0 <= left < right <= NORM and 0 <= top < bottom <= NORM): raise ValueError('box is left top right bottom on 0-1000')
    user32.ShowWindow(int(hwnd), 9)
    moved = bool(user32.SetWindowPos(int(hwnd), ctypes.c_void_p(-2), left * width // NORM, top * height // NORM, max((right - left) * width // NORM, 1), max((bottom - top) * height // NORM, 1), 0x0040))
    return dict(hwnd=int(hwnd), box=[left, top, right, bottom], moved=moved, err=ctypes.get_last_error())


def dib(screen, width, height):
    header, bits = BitmapInfoHeader(ctypes.sizeof(BitmapInfoHeader), width, -height, 1, 32), ctypes.c_void_p()
    bitmap = gdi32.CreateDIBSection(screen, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
    dc = gdi32.CreateCompatibleDC(screen)
    gdi32.SelectObject(dc, bitmap)
    return dc, bitmap, bits.value


def free(dc, bitmap):
    gdi32.DeleteDC(dc)
    gdi32.DeleteObject(bitmap)


def grab():
    width, height = screen_size()
    screen = user32.GetDC(0)
    dc, bitmap, bits = dib(screen, width, height)
    gdi32.BitBlt(dc, 0, 0, width, height, screen, 0, 0, SRCCOPY | CAPTUREBLT)
    pixels = ctypes.string_at(bits, width * height * 4)
    free(dc, bitmap)
    user32.ReleaseDC(0, screen)
    return pixels, width, height


def stretch(image, box, width, height):
    pixels, source_width, source_height = image
    x1, y1, x2, y2 = box
    screen = user32.GetDC(0)
    source, source_bitmap, source_bits = dib(screen, source_width, source_height)
    ctypes.memmove(source_bits, pixels, len(pixels))
    dc, bitmap, bits = dib(screen, width, height)
    gdi32.SetStretchBltMode(dc, HALFTONE)
    gdi32.SetBrushOrgEx(dc, 0, 0, None)
    gdi32.StretchBlt(dc, 0, 0, width, height, source, x1, y1, x2 - x1, y2 - y1, SRCCOPY)
    pixels = ctypes.string_at(bits, width * height * 4)
    free(dc, bitmap)
    free(source, source_bitmap)
    user32.ReleaseDC(0, screen)
    return pixels, width, height


def capture(region):
    x1, y1, x2, y2 = box = region_pixels(region)
    return stretch(grab(), box, x2 - x1, y2 - y1)


def swap_red_blue(pixels):
    out = bytearray(pixels)
    out[0::4], out[2::4], out[3::4] = pixels[2::4], pixels[0::4], b'\xff' * (len(pixels) // 4)
    return bytes(out)


def png(image):
    pixels, width, height = image
    rgba, stride = swap_red_blue(pixels), width * 4
    rows = b''.join(b'\0' + rgba[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))

    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows, 6)) + chunk(b'IEND', b'')


def unpng(data):
    (width, height), (size,) = struct.unpack('>II', data[16:24]), struct.unpack('>I', data[33:37])
    rows, stride = zlib.decompress(data[41:41 + size]), width * 4 + 1
    return swap_red_blue(b''.join(rows[y * stride + 1:(y + 1) * stride] for y in range(height))), width, height


def move(px, py):
    user32.SetCursorPos(px, py)


def mouse(flags, data=0):
    user32.mouse_event(flags, 0, 0, data, 0)


def send_mouse(flags, px, py):
    width, height = screen_size()
    packet = Input(0, MouseInput(int(px * 65535 / max(1, width - 1)), int(py * 65535 / max(1, height - 1)), 0, flags))
    user32.SendInput(1, ctypes.byref(packet), ctypes.sizeof(packet))


def key(vk, up=False):
    user32.keybd_event(vk, 0, (KEYUP if up else 0) | (EXTENDED if vk in EXTENDED_VKS else 0), None)


def scan(char):
    code = user32.VkKeyScanW(char)
    if code == -1: raise SystemExit(f'unmappable character: {char!r}')
    return code


def hover(x, y, region):
    move(*point(x, y, region))
    time.sleep(SETTLE)


def click(x, y, region, right=False):
    hover(x, y, region)
    mouse(RIGHT_DOWN if right else LEFT_DOWN)
    time.sleep(SETTLE)
    mouse(RIGHT_UP if right else LEFT_UP)


def double_click(x, y, region):
    click(x, y, region)
    time.sleep(DOUBLE_GAP)
    click(x, y, region)


def scroll(x, y, region, direction, clicks):
    if not clicks: return
    hover(x, y, region)
    for _ in range(clicks):
        mouse(WHEEL, direction * 120)
        time.sleep(SCROLL_GAP)


def wheel(x, y, region, amount, horizontal):
    if not amount: return
    hover(x, y, region)
    mouse(HWHEEL if horizontal else WHEEL, amount)


def stroke(points, region):
    pixels = [point(x, y, region) for x, y in points]
    send_mouse(MOVE | ABSOLUTE, *pixels[0])
    time.sleep(SETTLE)
    send_mouse(LEFT_DOWN | ABSOLUTE, *pixels[0])
    time.sleep(SETTLE)
    for (x0, y0), (x1, y1) in zip(pixels, pixels[1:]):
        for step in range(1, DRAG_STEPS + 1):
            send_mouse(MOVE | ABSOLUTE, x0 + (x1 - x0) * step // DRAG_STEPS, y0 + (y1 - y0) * step // DRAG_STEPS)
            time.sleep(DRAG_GAP)
    time.sleep(SETTLE)
    send_mouse(LEFT_UP | ABSOLUTE, *pixels[-1])


def cursor(region):
    at = W.POINT()
    user32.GetCursorPos(ctypes.byref(at))
    return to_norm(at.x, at.y, region)


def type_text(text):
    for char in text:
        code = scan(char)
        modifiers = [vk for bit, vk in ((0x200, 0x11), (0x400, 0x12), (0x100, 0x10)) if code & bit]
        for vk in modifiers: key(vk)
        key(code & 0xFF)
        time.sleep(TYPE_DOWN)
        key(code & 0xFF, True)
        for vk in reversed(modifiers): key(vk, True)
        time.sleep(TYPE_GAP)


def hotkey(keys):
    names = [part.strip().lower() for part in keys.replace(',', '+').replace(' ', '+').split('+') if part.strip()]
    if not names: raise SystemExit(f'hotkey: no valid key components in: {keys}')
    codes = []
    for name in names:
        if name in VK: codes.append(VK[name])
        elif len(name) == 1: codes.append(scan(name) & 0xFF)
        else: raise SystemExit(f'unknown hotkey component: {name}')
    for vk in codes:
        key(vk)
        time.sleep(HOTKEY_GAP)
    for vk in reversed(codes):
        key(vk, True)
        time.sleep(HOTKEY_GAP)


def select_region():
    width, height = screen_size()
    state = dict(dragging=False, start=(0, 0), end=(0, 0), box=None, code=2)
    null_brush = gdi32.GetStockObject(5)

    def xy(lparam):
        return ctypes.c_short(lparam).value, ctypes.c_short(lparam >> 16).value

    def box():
        (sx, sy), (ex, ey) = state['start'], state['end']
        return min(sx, ex), min(sy, ey), max(sx, ex), max(sy, ey)

    def proc(hwnd, msg, wparam, lparam):
        if msg == WM_ERASEBKGND: return 1
        if msg == WM_KEYDOWN and wparam == 0x1B or msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_RBUTTONDOWN:
            state['code'] = 0
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_LBUTTONDOWN:
            state.update(start=xy(lparam), end=xy(lparam), dragging=True)
            user32.SetCapture(hwnd)
            user32.InvalidateRect(hwnd, None, True)
            return 0
        if msg == WM_MOUSEMOVE:
            if state['dragging']:
                state['end'] = xy(lparam)
                user32.InvalidateRect(hwnd, None, True)
            return 0
        if msg == WM_LBUTTONUP:
            if state['dragging']:
                state.update(end=xy(lparam), dragging=False)
                user32.ReleaseCapture()
                x1, y1, x2, y2 = box()
                if x2 - x1 > 5 and y2 - y1 > 5:
                    state.update(box=(x1, y1, x2, y2), code=0)
                    user32.DestroyWindow(hwnd)
                else: user32.InvalidateRect(hwnd, None, True)
            return 0
        if msg == WM_PAINT:
            paint = PaintStruct()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(paint))
            black = gdi32.CreateSolidBrush(0)
            user32.FillRect(hdc, ctypes.byref(W.RECT(0, 0, width, height)), black)
            gdi32.DeleteObject(black)
            if state['dragging'] or state['end'] != state['start']:
                x1, y1, x2, y2 = box()
                white, green = gdi32.CreatePen(0, 3, 0x00FFFFFF), gdi32.CreatePen(1, 1, 0x0000FF00)
                old_pen, old_brush = gdi32.SelectObject(hdc, white), gdi32.SelectObject(hdc, null_brush)
                gdi32.SetBkMode(hdc, 1)
                gdi32.Rectangle(hdc, x1, y1, x2, y2)
                gdi32.SelectObject(hdc, green)
                gdi32.Rectangle(hdc, x1 - 2, y1 - 2, x2 + 2, y2 + 2)
                gdi32.SelectObject(hdc, old_pen)
                gdi32.SelectObject(hdc, old_brush)
                gdi32.DeleteObject(white)
                gdi32.DeleteObject(green)
            user32.EndPaint(hwnd, ctypes.byref(paint))
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    wndproc, instance = WNDPROC(proc), kernel32.GetModuleHandleW(None)
    window_class = WndClassExW(ctypes.sizeof(WndClassExW), 0x0003, wndproc, 0, 0, instance, None, user32.LoadCursorW(None, ctypes.cast(32515, W.LPCWSTR)), None, None, 'ScreenRegionSelector', None)
    user32.RegisterClassExW(ctypes.byref(window_class))
    hwnd = user32.CreateWindowExW(0x00080000 | 0x00000008 | 0x00000080, 'ScreenRegionSelector', 'Region Select', 0x80000000 | 0x10000000, 0, 0, width, height, None, None, instance, None)
    if not hwnd: raise SystemExit('CreateWindowExW failed')
    user32.SetLayeredWindowAttributes(hwnd, 0, 90, 0x00000002)
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(hwnd)
    msg = W.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) not in (0, -1):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    if state['box']:
        x1, y1, x2, y2 = state['box']
        return '%d,%d,%d,%d' % (*to_norm(x1, y1, 'NONE'), *to_norm(x2, y2, 'NONE')), 0
    return 'NONE', state['code']


def alive(pid):
    handle = kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle: return False
    kernel32.CloseHandle(handle)
    return True


def kill_on_close(handle):
    job, limits = kernel32.CreateJobObjectW(None, None), JobLimits()
    limits.BasicLimitInformation.LimitFlags = 0x2000
    if not (job and kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) and kernel32.AssignProcessToJobObject(job, int(handle))):
        raise ctypes.WinError(ctypes.get_last_error())
    return lambda: kernel32.CloseHandle(job)
