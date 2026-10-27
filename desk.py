import ctypes, hashlib, io, json, struct, subprocess, time, uuid, zlib
from contextlib import redirect_stdout
from ctypes import wintypes as W
from pathlib import Path

from base import NORM, SET, Fail, read_file, event
from state import atomic, json_file, locked

SRCCOPY, CAPTUREBLT, HALFTONE = 0x00CC0020, 0x40000000, 4
MOVE, LEFT_DOWN, LEFT_UP, RIGHT_DOWN, RIGHT_UP, WHEEL, HWHEEL, ABSOLUTE = 0x0001, 0x0002, 0x0004, 0x0008, 0x0010, 0x0800, 0x1000, 0x8000
KEYUP, EXTENDED = 0x0002, 0x0001
SETTLE, DOUBLE_GAP, TYPE_DOWN, TYPE_GAP, HOTKEY_GAP, SCROLL_GAP, DRAG_GAP, DRAG_STEPS = 0.03, 0.05, 0.01, 0.02, 0.02, 0.03, 0.012, 12
EXTENDED_VKS = frozenset((0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E))
VK = {'enter': 0x0D, 'return': 0x0D, 'tab': 0x09, 'escape': 0x1B, 'esc': 0x1B, 'backspace': 0x08, 'delete': 0x2E, 'del': 0x2E, 'insert': 0x2D, 'home': 0x24, 'end': 0x23, 'pageup': 0x21, 'pagedown': 0x22, 'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27, 'ctrl': 0x11, 'control': 0x11, 'alt': 0x12, 'shift': 0x10, 'win': 0x5B, 'windows': 0x5B, 'space': 0x20} | {f'f{i}': 0x6F + i for i in range(1, 13)} | {c: ord(c.upper()) for c in 'abcdefghijklmnopqrstuvwxyz0123456789'}
GESTURES = frozenset(('fara', 'click', 'rclick', 'dclick', 'tclick', 'move', 'drag', 'scroll', 'wheel', 'hwheel', 'key', 'wait', 'type', 'paste'))


def bind(name, table):
    dll = ctypes.WinDLL(name, use_last_error=True)
    for function, restype, *argtypes in table:
        getattr(dll, function).restype = restype
        getattr(dll, function).argtypes = argtypes
    return dll


INT, PTR = W.INT, ctypes.POINTER
user32 = bind('user32', [('GetDC', W.HDC, W.HWND), ('ReleaseDC', INT, W.HWND, W.HDC), ('GetSystemMetrics', INT, INT), ('GetCursorPos', W.BOOL, PTR(W.POINT)), ('SendInput', ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, INT), ('keybd_event', None, W.BYTE, W.BYTE, W.DWORD, PTR(ctypes.c_ulong)), ('VkKeyScanW', ctypes.c_short, W.WCHAR), ('EnumWindows', W.BOOL, ctypes.c_void_p, W.LPARAM), ('GetForegroundWindow', W.HWND), ('GetWindowTextW', INT, W.HWND, W.LPWSTR, INT), ('GetWindowTextLengthW', INT, W.HWND), ('IsWindowVisible', W.BOOL, W.HWND), ('IsIconic', W.BOOL, W.HWND), ('GetWindowRect', W.BOOL, W.HWND, PTR(W.RECT)), ('GetWindowThreadProcessId', W.DWORD, W.HWND, PTR(W.DWORD)), ('GetWindowLongW', W.LONG, W.HWND, INT), ('ShowWindow', W.BOOL, W.HWND, INT), ('SetWindowPos', W.BOOL, W.HWND, ctypes.c_void_p, INT, INT, INT, INT, W.UINT)])
gdi32 = bind('gdi32', [('CreateCompatibleDC', W.HDC, W.HDC), ('CreateDIBSection', W.HBITMAP, W.HDC, ctypes.c_void_p, W.UINT, PTR(ctypes.c_void_p), W.HANDLE, W.DWORD), ('SelectObject', W.HGDIOBJ, W.HDC, W.HGDIOBJ), ('DeleteObject', W.BOOL, W.HGDIOBJ), ('DeleteDC', W.BOOL, W.HDC), ('BitBlt', W.BOOL, W.HDC, INT, INT, INT, INT, W.HDC, INT, INT, W.DWORD), ('StretchBlt', W.BOOL, W.HDC, INT, INT, INT, INT, W.HDC, INT, INT, INT, INT, W.DWORD), ('SetStretchBltMode', INT, W.HDC, INT), ('SetBrushOrgEx', W.BOOL, W.HDC, INT, INT, ctypes.c_void_p)])
kernel32 = bind('kernel32', [('CreateJobObjectW', W.HANDLE, W.LPVOID, W.LPCWSTR), ('SetInformationJobObject', W.BOOL, W.HANDLE, INT, W.LPVOID, W.DWORD), ('AssignProcessToJobObject', W.BOOL, W.HANDLE, W.HANDLE), ('CloseHandle', W.BOOL, W.HANDLE)])
dwmapi = bind('dwmapi', [('DwmGetWindowAttribute', ctypes.c_long, W.HWND, W.DWORD, ctypes.c_void_p, W.DWORD)])
try:
    ctypes.WinDLL('shcore').SetProcessDpiAwareness(2)
except OSError:
    pass


class BitmapInfoHeader(ctypes.Structure):
    _fields_ = [('biSize', W.DWORD), ('biWidth', W.LONG), ('biHeight', W.LONG), ('biPlanes', W.WORD), ('biBitCount', W.WORD), ('biCompression', W.DWORD), ('biSizeImage', W.DWORD), ('biXPelsPerMeter', W.LONG), ('biYPelsPerMeter', W.LONG), ('biClrUsed', W.DWORD), ('biClrImportant', W.DWORD)]


class MouseInput(ctypes.Structure):
    _fields_ = [('dx', ctypes.c_long), ('dy', ctypes.c_long), ('mouseData', ctypes.c_uint), ('dwFlags', ctypes.c_uint), ('time', ctypes.c_uint), ('dwExtraInfo', ctypes.c_ulonglong)]


class KeyboardInput(ctypes.Structure):
    _fields_ = [('wVk', W.WORD), ('wScan', W.WORD), ('dwFlags', W.DWORD), ('time', W.DWORD), ('dwExtraInfo', ctypes.c_ulonglong)]


class InputData(ctypes.Union):
    _fields_ = [('mi', MouseInput), ('ki', KeyboardInput)]


class Input(ctypes.Structure):
    _anonymous_ = ('data',)
    _fields_ = [('type', ctypes.c_uint), ('data', InputData)]


class JobBasicLimits(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64), ('LimitFlags', W.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t), ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', W.DWORD), ('Affinity', ctypes.c_size_t), ('PriorityClass', W.DWORD), ('SchedulingClass', W.DWORD)]


class JobLimits(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', JobBasicLimits), ('IoInfo', ctypes.c_uint64 * 6), ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]


def screen_size():
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def clamp(value):
    return max(0, min(NORM, int(value)))


def to_pixels(box, width, height):
    return [(v * (height if i % 2 else width) + NORM // 2) // NORM for i, v in enumerate(box)]


def send(flags, px=0, py=0, data=0, absolute=False):
    width, height = screen_size()
    dx = int(px * 65535 / max(1, width - 1)) if absolute else 0
    dy = int(py * 65535 / max(1, height - 1)) if absolute else 0
    packet = Input()
    packet.type = 0
    packet.mi = MouseInput(dx, dy, int(data) & 0xFFFFFFFF, flags | (ABSOLUTE if absolute else 0))
    deliver(packet)


def deliver(packet):
    if user32.SendInput(1, ctypes.byref(packet), ctypes.sizeof(packet)) != 1:
        raise Fail(f'SendInput did not deliver input (Win32 error {ctypes.get_last_error()})')


def swap_red_blue(pixels):
    out = bytearray(pixels)
    count = len(pixels) // 4
    out[0::4], out[2::4], out[3::4] = pixels[2::4], pixels[0::4], b'\xff' * count
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


def blank(pixels):
    count = len(pixels) // 4
    zero = bytes(count)
    return bool(count) and pixels[0::4] == zero and pixels[1::4] == zero and pixels[2::4] == zero


class Desk:
    def __init__(self, work):
        self.work = Path(work)

    def region(self):
        path = self.work / 'region.txt'
        raw = path.read_text(encoding='utf-8').strip() if path.exists() else 'NONE'
        return raw or 'NONE'

    def _box(self):
        if getattr(self, 'action_box', None) is not None:
            return tuple(self.action_box)
        raw = self.region()
        box = (0, 0, NORM, NORM) if raw == 'NONE' else tuple(int(part) for part in raw.split(','))
        return to_pixels(box, *screen_size())

    def point(self, x, y):
        x1, y1, x2, y2 = self._box()
        return x1 + round(max(0, min(NORM, float(x))) * (x2 - x1 - 1) / NORM), y1 + round(max(0, min(NORM, float(y))) * (y2 - y1 - 1) / NORM)

    def position(self):
        at, (width, height) = W.POINT(), screen_size()
        user32.GetCursorPos(ctypes.byref(at))
        x1, y1, x2, y2 = self._box()
        return [max(0, min(NORM, (at.x - x1) * NORM / max(1, x2 - x1 - 1))), max(0, min(NORM, (at.y - y1) * NORM / max(1, y2 - y1 - 1)))]

    def cursor_pixel(self):
        at = W.POINT()
        if not user32.GetCursorPos(ctypes.byref(at)):
            raise Fail('cannot read cursor provenance')
        return [at.x, at.y]

    def move(self, x, y):
        send(MOVE, *self.point(x, y), absolute=True)
        time.sleep(SETTLE)

    def click(self, x, y, right=False, times=1):
        px, py = self.point(x, y)
        send(MOVE, px, py, absolute=True)
        time.sleep(SETTLE)
        down, up = (RIGHT_DOWN, RIGHT_UP) if right else (LEFT_DOWN, LEFT_UP)
        for _ in range(max(1, times)):
            try:
                send(down, px, py, absolute=True)
                time.sleep(SETTLE)
            finally:
                send(up, px, py, absolute=True)
            time.sleep(DOUBLE_GAP if times > 1 else 0)

    def drag(self, points):
        if len(points) < 2:
            raise Fail('drag needs two or more points')
        pixels = [self.point(x, y) for x, y in points]
        send(LEFT_UP)
        send(MOVE, *pixels[0], absolute=True)
        time.sleep(SETTLE)
        try:
            send(LEFT_DOWN, *pixels[0], absolute=True)
            time.sleep(SETTLE)
            for (x0, y0), (x1, y1) in zip(pixels, pixels[1:]):
                for step in range(1, DRAG_STEPS + 1):
                    send(MOVE, x0 + (x1 - x0) * step // DRAG_STEPS, y0 + (y1 - y0) * step // DRAG_STEPS, absolute=True)
                    time.sleep(DRAG_GAP)
            time.sleep(SETTLE)
        finally:
            send(LEFT_UP)

    def wheel(self, amount, x=None, y=None, horizontal=False):
        if x is not None:
            self.move(x, y)
        if amount:
            send(HWHEEL if horizontal else WHEEL, data=int(amount))

    def key(self, combo):
        names = [part.strip().lower() for part in str(combo).replace(',', '+').replace(' ', '+').split('+') if part.strip()]
        if not names:
            raise Fail(f'no key in {combo!r}')
        codes = []
        for name in names:
            if name in VK:
                codes.append(VK[name])
            elif len(name) == 1:
                code = user32.VkKeyScanW(name)
                if code == -1:
                    raise Fail(f'unmappable character: {name!r}')
                codes.append(code & 0xFF)
            else:
                raise Fail(f'unknown key: {name}')
        self._chord(codes, HOTKEY_GAP)

    def _chord(self, codes, delay=0):
        held = []
        try:
            for vk in codes:
                held.append(vk)
                self._key(vk)
                time.sleep(delay)
        finally:
            failures = []
            for vk in reversed(held):
                try:
                    self._key(vk, up=True)
                except Fail as error:
                    failures.append(str(error))
            if failures:
                raise Fail('; '.join(failures))

    def _key(self, vk, up=False):
        packet = Input()
        packet.type = 1
        packet.ki = KeyboardInput(vk, 0, (KEYUP if up else 0) | (EXTENDED if vk in EXTENDED_VKS else 0), 0, 0)
        deliver(packet)

    def type_text(self, value):
        value = str(value).replace('\r\n', '\n').replace('\r', '\n')
        if any(ord(char) > 126 for char in value):
            self.paste(value)
            return
        for char in value:
            if char in ('\n', '\t'):
                self.key('enter' if char == '\n' else 'tab')
                continue
            code = user32.VkKeyScanW(char)
            if code == -1:
                raise Fail(f'unmappable character: {char!r}')
            modifiers = [vk for bit, vk in ((0x200, 0x11), (0x400, 0x12), (0x100, 0x10)) if code & bit]
            self._chord(modifiers + [code & 0xFF], TYPE_DOWN)
            time.sleep(TYPE_GAP)

    def paste(self, value):
        subprocess.run(['powershell.exe', '-NoProfile', '-Command', '[Console]::InputEncoding=[Text.UTF8Encoding]::new(); Set-Clipboard -Value ([Console]::In.ReadToEnd())'], input=str(value).encode(), capture_output=True, check=True, creationflags=0x08000000)
        self.key('ctrl+v')

    def grab(self):
        width, height = screen_size()
        screen = user32.GetDC(0)
        header, bits = BitmapInfoHeader(ctypes.sizeof(BitmapInfoHeader), width, -height, 1, 32), ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(screen, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
        dc = gdi32.CreateCompatibleDC(screen)
        gdi32.SelectObject(dc, bitmap)
        gdi32.BitBlt(dc, 0, 0, width, height, screen, 0, 0, SRCCOPY | CAPTUREBLT)
        pixels = ctypes.string_at(bits, width * height * 4)
        gdi32.DeleteDC(dc)
        gdi32.DeleteObject(bitmap)
        user32.ReleaseDC(0, screen)
        return pixels, width, height

    def stretch(self, image, box, width, height):
        pixels, source_width, source_height = image
        x1, y1, x2, y2 = box
        screen = user32.GetDC(0)
        source_header, source_bits = BitmapInfoHeader(ctypes.sizeof(BitmapInfoHeader), source_width, -source_height, 1, 32), ctypes.c_void_p()
        source_bitmap = gdi32.CreateDIBSection(screen, ctypes.byref(source_header), 0, ctypes.byref(source_bits), None, 0)
        source = gdi32.CreateCompatibleDC(screen)
        gdi32.SelectObject(source, source_bitmap)
        ctypes.memmove(source_bits, pixels, len(pixels))
        header, bits = BitmapInfoHeader(ctypes.sizeof(BitmapInfoHeader), width, -height, 1, 32), ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(screen, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
        dc = gdi32.CreateCompatibleDC(screen)
        gdi32.SelectObject(dc, bitmap)
        gdi32.SetStretchBltMode(dc, HALFTONE)
        gdi32.SetBrushOrgEx(dc, 0, 0, None)
        gdi32.StretchBlt(dc, 0, 0, width, height, source, x1, y1, max(1, x2 - x1), max(1, y2 - y1), SRCCOPY)
        out = ctypes.string_at(bits, width * height * 4)
        gdi32.DeleteDC(dc)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(source)
        gdi32.DeleteObject(source_bitmap)
        user32.ReleaseDC(0, screen)
        return out, width, height

    def capture(self):
        with locked(self.work / 'capture.lock'):
            region, size, windows = self.region(), list(screen_size()), self.windows()
            x1, y1, x2, y2 = self._box()
            image = self.stretch(self.grab(), (x1, y1, x2, y2), max(1, x2 - x1), max(1, y2 - y1))
            if region != self.region() or size != list(screen_size()) or windows != self.windows():
                raise Fail('desktop geometry changed during capture; capture again')
            shots = self.work / 'shots'
            shots.mkdir(exist_ok=True)
            sid = time.strftime('%H%M%S') + '-' + uuid.uuid4().hex
            path = shots / f'{sid}.png'
            data = png(image)
            atomic(path, data)
            body = dict(id=sid, image=str(path), size=list(image[1:]), region=region, box=[x1, y1, x2, y2], screen_size=size,
                        windows=windows, cursor_pixel=self.cursor_pixel(), at=time.time(), sha256=hashlib.sha256(data).hexdigest())
            body['cursor'] = [(value - start) * NORM / max(1, length - 1) for value, start, length in zip(body['cursor_pixel'], (x1, y1), image[1:])]
            if blank(image[0]):
                body['blank'] = True
            json_file(shots / f'{sid}.json', body)
            json_file(self.work / 'screen.json', body)
            event('capture_saved', body)
            return body

    def windows(self):
        found, front, (width, height) = [], user32.GetForegroundWindow(), screen_size()

        def visit(hwnd, _):
            cloaked, length = W.DWORD(), user32.GetWindowTextLengthW(hwnd)
            dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            if user32.IsWindowVisible(hwnd) and not cloaked.value and length:
                title, rect, pid = ctypes.create_unicode_buffer(length + 1), W.RECT(), W.DWORD()
                user32.GetWindowTextW(hwnd, title, length + 1)
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                minimized = bool(user32.IsIconic(hwnd))
                box = None if minimized else [rect.left * NORM // width, rect.top * NORM // height, rect.right * NORM // width, rect.bottom * NORM // height]
                found.append(dict(hwnd=int(hwnd), title=title.value, box=box, pixel_box=None if minimized else [rect.left, rect.top, rect.right, rect.bottom], pid=int(pid.value), minimized=minimized, topmost=bool(user32.GetWindowLongW(hwnd, -20) & 0x00000008), foreground=hwnd == front))
            return True

        user32.EnumWindows(ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)(visit), 0)
        return found

    def place(self, hwnd, left, top, right, bottom):
        width, height = screen_size()
        left, top, right, bottom = (int(float(v)) for v in (left, top, right, bottom))
        if not (0 <= left < right <= NORM and 0 <= top < bottom <= NORM):
            raise Fail('box is left top right bottom on 0-1000')
        user32.ShowWindow(int(hwnd), 9)
        moved = bool(user32.SetWindowPos(int(hwnd), ctypes.c_void_p(-2), left * width // NORM, top * height // NORM, max((right - left) * width // NORM, 1), max((bottom - top) * height // NORM, 1), 0x0040))
        return dict(hwnd=int(hwnd), box=[left, top, right, bottom], moved=moved, err=ctypes.get_last_error())

    def open_job(self, handle=None, name=None):
        job, limits = kernel32.CreateJobObjectW(None, name), JobLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000
        if not (job and kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))):
            self.close_job(job)
            raise Fail('the wake could not open a job')
        try:
            if handle is not None:
                self.assign_job(job, handle)
        except BaseException:
            self.close_job(job)
            raise
        return job

    def assign_job(self, job, handle):
        if not kernel32.AssignProcessToJobObject(job, int(handle)):
            raise Fail('the wake could not keep the agent in its job')

    def close_job(self, job):
        if job:
            kernel32.CloseHandle(job)


class Hand:
    name, flag = 'hands', 'hands_file'
    doc = '''$CALL --hands-file PATH
For desktop actions, copy exactly one Fara program: fara ACTION_ID. The body checks a fresh capture, geometry and target patch before input; refused or consumed records require a new Fara grounding. Do not invent, calculate, or revise coordinates. Legacy gesture/Python files remain compatible but are not the advertised desktop path.
A stroke is a series of short moves with the button down: 12 steps, 12 ms apart, all through one input path. A jump of the cursor does not leave ink.
Python is given desk, time, json, and Path. Do not use hand Python to inspect screenshots, scan pixels, or calculate desktop target coordinates. Desktop perception belongs to Trident's Fara eye.
The call waits, then takes a capture. Ask Fara what changed before choosing the next desktop action.'''

    def run(self, app, path, dry=False):
        src = read_file(path)
        if dry:
            return dict(dry=True, lines=len(src.splitlines()))
        with redirect_stdout(io.StringIO()) as out:
            self._run(app.desk, src)
        time.sleep(float(SET.get('shot_delay') or 1.5))
        shot = app.desk.capture()
        shot['stdout'] = out.getvalue()
        return shot

    def _run(self, desk, src):
        lines = [line.strip() for line in src.splitlines() if line.strip()]
        if not lines:
            raise Fail('the hand file is empty')
        if lines[0].split(None, 1)[0] == 'fara':
            if len(lines) != 1 or len(lines[0].split()) != 2:
                raise Fail('copy exactly one Fara program')
            from grounding import execute
            execute(desk, lines[0].split()[1])
            return
        if lines[0].split(None, 1)[0] not in GESTURES:
            exec(compile(src, '<hands>', 'exec'), dict(desk=desk, time=time, json=json, Path=Path))
            return
        for line in lines:
            verb, _, rest = line.partition(' ')
            if verb in ('type', 'paste'):
                (desk.type_text if verb == 'type' else desk.paste)(rest)
            elif verb == 'key':
                desk.key(rest)
            elif verb == 'wait':
                time.sleep(max(0.0, float(rest or 0)))
            elif verb == 'scroll':
                count, *at = rest.split()
                desk.wheel(int(count) * 120, *map(float, at))
            elif verb in ('wheel', 'hwheel'):
                amount, *at = rest.split()
                desk.wheel(int(amount), *map(float, at), horizontal=verb == 'hwheel')
            elif verb == 'tclick':
                desk.click(*map(float, rest.split()), times=3)
            elif verb in ('click', 'rclick', 'dclick', 'move'):
                desk.move(*map(float, rest.split())) if verb == 'move' else desk.click(*map(float, rest.split()), right=verb == 'rclick', times=2 if verb == 'dclick' else 1)
            elif verb == 'drag':
                nums = list(map(float, rest.split()))
                if len(nums) < 4 or len(nums) % 2:
                    raise Fail('drag needs pairs of points')
                desk.drag(list(zip(nums[::2], nums[1::2])))
            else:
                raise Fail(f'unknown gesture {verb}')


hand = Hand()
