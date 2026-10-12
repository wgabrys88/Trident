import asyncio, ctypes, os, time, winreg
from fabric import RUN, execute, serve

ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
import pyautogui as gui
import pyperclip
from PIL import ImageGrab

gui.PAUSE = .1

def shot():
    from fabric import token
    path = RUN / (token() + '.png')
    ImageGrab.grab().save(path)
    return str(path)

def perform(op, args):
    if op == 'launch':
        execute(['powershell.exe', '-NoProfile', '-Command',
                 'Start-Process -FilePath $env:TRIDENT_APP'], 5,
                env=os.environ | {'TRIDENT_APP': args['app']})
    elif op == 'browse':
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe') as key:
            browser = winreg.QueryValue(key, None)
        execute(['powershell.exe', '-NoProfile', '-Command',
                 'Start-Process -FilePath $env:TRIDENT_BROWSER -ArgumentList $env:TRIDENT_URL'], 5,
                env=os.environ | {'TRIDENT_BROWSER': browser, 'TRIDENT_URL': args['url']})
    elif op == 'focus':
        execute(['powershell.exe', '-NoProfile', '-Command',
                 "$s=New-Object -ComObject WScript.Shell; if (-not $s.AppActivate($env:TRIDENT_TITLE)) { throw 'Window not found' }"],
                5, env=os.environ | {'TRIDENT_TITLE': args['title']})
    elif op == 'key':
        keys = args['keys']
        if isinstance(keys, str):
            keys = keys.lower().split('+')
        if not keys or any(k not in gui.KEYBOARD_KEYS for k in keys):
            raise ValueError('Unknown real key')
        gui.hotkey(*keys)
    elif op == 'type':
        pyperclip.copy(args['text'])
        gui.hotkey('ctrl', 'v')
    elif op in ('click', 'draw'):
        from fabric import CFG
        target = args['target']
        if time.time() - target['at'] > CFG['target_seconds']:
            raise ValueError('Target expired: locate it again')
        x0, y0, x1, y1 = target['box']
        width, height = gui.size()
        strokes = args.get('strokes', [args.get('points', [[.5, .5]])])
        for points in strokes:
            if not points or not all(0 <= v <= 1 for point in points for v in point):
                raise ValueError('Points must be relative X,Y in 0..1')
            pixels = [(round((x0 + x * (x1-x0)) * (width-1) / 1000),
                       round((y0 + y * (y1-y0)) * (height-1) / 1000)) for x, y in points]
            gui.moveTo(*pixels[0])
            if op == 'click':
                gui.click()
            else:
                gui.mouseDown()
                try:
                    for point in pixels[1:]:
                        gui.moveTo(*point, duration=.12)
                finally:
                    gui.mouseUp()
    elif op != 'capture':
        raise ValueError(op)
    time.sleep(.3)
    return dict(accepted=True, verified=False, image=shot(), at=time.time())

async def handle(op, args):
    return await asyncio.to_thread(perform, op, args)

if __name__ == '__main__':
    asyncio.run(serve(handle, 'manipulator'))
