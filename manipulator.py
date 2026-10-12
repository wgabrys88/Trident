import asyncio, ctypes, os, time
from ctypes import wintypes as W
from fabric import CFG, RUN, NotAttempted, keys_in, log, serve, token

u = ctypes.WinDLL('user32', use_last_error=True)
u.SetProcessDpiAwarenessContext.argtypes = [W.HANDLE]
u.GetThreadDpiAwarenessContext.restype = W.HANDLE
u.AreDpiAwarenessContextsEqual.argtypes = [W.HANDLE, W.HANDLE]
if not u.SetProcessDpiAwarenessContext(W.HANDLE(-4)) and not u.AreDpiAwarenessContextsEqual(u.GetThreadDpiAwarenessContext(), W.HANDLE(-4)):
    raise OSError('Physical per-monitor DPI initialization failed')
u.GetForegroundWindow.restype = W.HWND
u.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
u.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
u.IsWindow.argtypes = [W.HWND]
u.IsWindowVisible.argtypes = [W.HWND]
u.ShowWindowAsync.argtypes = [W.HWND, ctypes.c_int]
u.SetForegroundWindow.argtypes = [W.HWND]
import pyautogui as gui
import pyperclip
from PIL import ImageGrab
gui.PAUSE = .1

def desktop():
    return [u.GetSystemMetrics(i) for i in (76, 77, 78, 79)]

def foreground(handle=None):
    handle, title, rect = handle or u.GetForegroundWindow(), ctypes.create_unicode_buffer(1024), W.RECT()
    u.GetWindowTextW(handle, title, len(title))
    u.GetWindowRect(handle, ctypes.byref(rect))
    return dict(id=str(handle), title=title.value, rect=[rect.left, rect.top, rect.right, rect.bottom])

def require_window(ident, attempted=False):
    current = foreground()
    if current['id'] != ident or not u.IsWindow(W.HWND(int(ident))):
        raise (OSError if attempted else NotAttempted)('Intended window is not foreground; focus and locate again')
    return current

def shot():
    rectangle = desktop()
    pixels = ImageGrab.grab(all_screens=True)
    if list(pixels.size) != rectangle[2:]:
        raise OSError('Capture dimensions do not match physical desktop')
    path = RUN / (token()+'.png')
    pixels.save(path)
    window = foreground()
    ox, oy, width, height = rectangle
    left, top, right, bottom = window['rect']
    window['normalized_box'] = [max(0, (left-ox)*1000/width), max(0, (top-oy)*1000/height),
                                min(1000, (right-ox)*1000/width), min(1000, (bottom-oy)*1000/height)]
    windows = []
    @ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    def collect(handle, _):
        if u.IsWindowVisible(handle):
            item = foreground(handle)
            if item['title'] and len(windows) < 20:
                windows.append(item)
        return True
    u.EnumWindows(collect, 0)
    return dict(image=str(path), desktop=rectangle, window=window, windows=windows, at=time.time())

def perform(op, args):
    events = []
    before = foreground()['id']
    if op in ('launch', 'browse'):
        try:
            os.startfile(args['app' if op == 'launch' else 'url'])
        except OSError as error:
            raise NotAttempted(str(error)) from error
    elif op == 'focus':
        matches = [window for window in shot()['windows'] if args['title'].casefold() in window['title'].casefold()]
        if not matches:
            raise NotAttempted('Requested window was not found')
        handle = W.HWND(int(matches[0]['id']))
        restored = bool(u.ShowWindowAsync(handle, 9))
        # Release the previous foreground through the window manager, without injecting keys into it.
        previous = u.GetForegroundWindow()
        minimized = bool(previous != handle.value and u.ShowWindowAsync(previous, 6))
        time.sleep(.2)
        accepted = bool(u.SetForegroundWindow(handle))
        deadline = time.monotonic()+2
        while foreground()['id'] != matches[0]['id'] and time.monotonic() < deadline:
            time.sleep(.05)
        events.append(dict(focused=matches[0]['id'], foreground=foreground()['id'], accepted=accepted,
                           restored=restored, previous=str(previous), minimized=minimized))
        if foreground()['id'] != matches[0]['id']:
            return dict(error='Windows did not focus the requested window', outcome='failed', verified=False, events=events, **shot())
    elif op in ('key', 'type'):
        require_window(args['window'])
        if op == 'key':
            keys = keys_in(args['keys'])
            if any(k not in gui.KEYBOARD_KEYS for k in keys):
                raise NotAttempted('Key is unavailable to native input')
            gui.press(keys[0]) if len(keys) == 1 else gui.hotkey(*keys)
            events.append(dict(keys=keys))
        else:
            pyperclip.copy(args['text'])
            require_window(args['window'])
            gui.hotkey('ctrl', 'v')
            events.append(dict(pasted_characters=len(args['text'])))
    elif op in ('click', 'draw'):
        target = args['target']
        current = require_window(target['window']['id'])
        if (time.time()-target['at'] > CFG['target_seconds'] or desktop() != target['desktop']
                or current['rect'] != target['window']['rect']):
            raise NotAttempted('Located geometry changed or expired; locate again')
        ox, oy, width, height = target['desktop']
        x0, y0, x1, y1 = target['box']
        strokes = args.get('strokes', [args.get('points', [[.5, .5]])])
        if op == 'click':
            strokes = [[point] for point in strokes[0]]
        # Map the same captured physical rectangle into the virtual desktop mouse range.
        def move(x, y):
            u.mouse_event(0xC001, round((x-ox)*65535/(width-1)), round((y-oy)*65535/(height-1)), 0, 0)
            point = W.POINT(); u.GetCursorPos(ctypes.byref(point))
            events.append(dict(requested=[x, y], actual=[point.x, point.y]))
            log('mouse_position', action_id=args.get('action_id'), requested=[x, y], actual=[point.x, point.y])
        for points in strokes:
            require_window(target['window']['id'], bool(events))
            pixels = [(ox+round((x0+x*(x1-x0))*(width-1)/1000),
                       oy+round((y0+y*(y1-y0))*(height-1)/1000)) for x, y in points]
            left, top, right, bottom = current['rect']
            if any(not left <= x < right or not top <= y < bottom for x, y in pixels):
                raise (OSError if events else NotAttempted)('Located points are outside the intended window')
            move(*pixels[0]); time.sleep(.08)
            try:
                u.mouse_event(2, 0, 0, 0, 0)
                for point in pixels[1:]:
                    require_window(target['window']['id'], True)
                    move(*point); time.sleep(.12)
            finally:
                u.mouse_event(4, 0, 0, 0, 0)
                log('mouse_released', action_id=args.get('action_id'))
    elif op != 'capture':
        raise ValueError('Unknown native action')
    if op in ('launch', 'browse'):
        deadline = time.monotonic()+4
        while foreground()['id'] == before and time.monotonic() < deadline:
            time.sleep(.1)
    log('native_events', action_id=args.get('action_id'), op=op, events=events)
    time.sleep(.3)
    return dict(accepted=True, verified=False, events=events, **shot())

async def handle(op, args):
    return await asyncio.to_thread(perform, op, args)

if __name__ == '__main__':
    asyncio.run(serve(handle, 'manipulator'))
