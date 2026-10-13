import asyncio, ctypes, os, time
from ctypes import wintypes as W
from fabric import CFG, RUN, NotAttempted, serve, token
u = ctypes.WinDLL('user32', use_last_error=True)
u.SetProcessDpiAwarenessContext.argtypes = [W.HANDLE]
u.GetThreadDpiAwarenessContext.restype = W.HANDLE
u.AreDpiAwarenessContextsEqual.argtypes = [W.HANDLE, W.HANDLE]
if not u.SetProcessDpiAwarenessContext(W.HANDLE(-4)) and not u.AreDpiAwarenessContextsEqual(u.GetThreadDpiAwarenessContext(), W.HANDLE(-4)):
    raise OSError('Physical per-monitor DPI initialization failed')
u.GetForegroundWindow.restype = W.HWND
u.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
u.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
u.SetForegroundWindow.argtypes = [W.HWND]
u.ShowWindowAsync.argtypes = [W.HWND, ctypes.c_int]
import pyautogui as gui
import pyperclip
from PIL import ImageGrab
gui.PAUSE = .05

def desktop():
    return [u.GetSystemMetrics(i) for i in (76, 77, 78, 79)]

def foreground(handle=None):
    handle, title, rect = handle or u.GetForegroundWindow(), ctypes.create_unicode_buffer(1024), W.RECT()
    u.GetWindowTextW(handle, title, len(title)); u.GetWindowRect(handle, ctypes.byref(rect))
    return dict(id=str(handle), title=title.value, rect=[rect.left, rect.top, rect.right, rect.bottom])

def require(ident):
    if ident != foreground()['id']:
        raise NotAttempted('Intended window is not foreground; focus it before input')

def shot():
    rectangle = desktop(); pixels = ImageGrab.grab(all_screens=True)
    if list(pixels.size) != rectangle[2:]:
        raise OSError('Capture and physical desktop dimensions differ')
    path = RUN/(token()+'.png'); pixels.save(path)
    windows = []
    @ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    def collect(handle, _):
        if u.IsWindowVisible(handle):
            item = foreground(handle)
            if item['title']:
                windows.append(item)
        return True
    u.EnumWindows(collect, 0)
    return dict(image=str(path), desktop=rectangle, window=foreground(), windows=windows, at=time.time())

def perform(args):
    events, intended = [], args.get('window')
    raw_press,raw_hotkey = gui.press,gui.hotkey
    class Keyboard:
        def press(self,*keys,**options):
            require(intended); events.append(dict(press=keys)); return raw_press(*keys,**options)
        def hotkey(self,*keys,**options):
            require(intended); events.append(dict(hotkey=keys)); return raw_hotkey(*keys,**options)
    keyboard = Keyboard()
    def focus(ident):
        nonlocal intended
        handle = W.HWND(int(ident)); u.ShowWindowAsync(handle, 9)
        raw_press('alt'); accepted = bool(u.SetForegroundWindow(handle))
        deadline = time.monotonic()+2
        while foreground()['id'] != ident and time.monotonic()<deadline:
            time.sleep(.05)
        events.append(dict(focus=ident, accepted=accepted, foreground=foreground()))
        require(ident); intended=ident
    def paste(text):
        require(intended); pyperclip.copy(text); raw_hotkey('ctrl','v')
        events.append(dict(pasted_characters=len(text)))
    def stroke(target, points, click=False):
        require(target['window']['id']); current = foreground()
        if (time.time()-target['at']>CFG['target_seconds'] or desktop()!=target['desktop'] or current['rect']!=target['window']['rect']):
            raise NotAttempted('Target geometry changed or expired')
        ox,oy,width,height = target['desktop']; x0,y0,x1,y1 = target['box']
        actual = []
        try:
            for index,(x,y) in enumerate(points):
                require(target['window']['id'])
                if not 0<=x<=1 or not 0<=y<=1:
                    raise ValueError('Stroke coordinates must be relative to target')
                px,py = ox+round((x0+x*(x1-x0))*(width-1)/1000), oy+round((y0+y*(y1-y0))*(height-1)/1000)
                left,top,right,bottom = current['rect']
                if not left<=px<right or not top<=py<bottom:
                    raise ValueError('Point is outside intended window')
                u.mouse_event(0xC001, round((px-ox)*65535/(width-1)), round((py-oy)*65535/(height-1)),0,0)
                position = W.POINT(); u.GetCursorPos(ctypes.byref(position)); actual.append([position.x,position.y])
                if index==0 or click:
                    u.mouse_event(2,0,0,0,0)
                time.sleep(.08)
                if click:
                    u.mouse_event(4,0,0,0,0)
        finally:
            u.mouse_event(4,0,0,0,0); events.append(dict(stroke=actual, click=click, released=True))
    namespace = dict(os=os,gui=keyboard,paste=paste,focus=focus,stroke=stroke,foreground=foreground,
                     desktop=desktop,targets=args.get('targets',{}),events=events)
    attempted = False
    try:
        if intended:
            require(intended)
        attempted = True
        exec(compile(args['source'],'<native>','exec'),namespace)
        result = dict(outcome='completed',value=namespace.get('result'))
    except Exception as error:
        result = dict(error=f'{type(error).__name__}: {error}',outcome='unknown' if attempted else 'not_attempted')
    finally:
        u.mouse_event(4,0,0,0,0)
    time.sleep(5)
    return result | dict(events=events,verified=False,**shot())

async def handle(op,args):
    return await asyncio.to_thread(shot if op=='capture' else perform, *([] if op=='capture' else [args]))

if __name__=='__main__':
    asyncio.run(serve(handle,'manipulator'))
