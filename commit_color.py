import subprocess
import time
from pathlib import Path

import organism

work = Path(r'C:\Users\eb-wjt\Downloads\Jarvis\Trident')
organism.configure(str(work), '20261029_220922', '40', '1793308162.9878514', '1793310562.9878514')
app = organism.App(str(work))
focus = str(work / '_focus.ps1')


def hex_state():
    out = subprocess.run(['powershell.exe', '-NoProfile', '-STA', '-File', focus], capture_output=True, text=True)
    text = ((out.stdout or '') + (out.stderr or '')).strip()
    print(text)
    return text


print('before')
state = hex_state()
if 'NOHEX' in state or 'RECT' not in state:
    app.desk.click_pixel(1641, 146)
    time.sleep(0.45)
    print('opened')
    state = hex_state()
nums = state.split('RECT', 1)[1].split('FOCUS')[0].strip().split(',')
x, y, w, h = [int(float(v)) for v in nums]
app.desk.click_pixel(x + w // 2, y + h // 2)
time.sleep(0.15)
print('after click')
state = hex_state()
if 'FOCUS True' not in state:
    raise SystemExit('hex field did not take focus')
app.desk.key('ctrl+a')
time.sleep(0.05)
app.desk.type_text('#1e4aa8')
time.sleep(0.08)
app.desk.key('enter')
time.sleep(0.35)
print('after enter')
hex_state()
image = app.desk.grab()
crop = app.desk.stretch(image, (1050, 160, 1650, 560), 600, 400)
(work / 'color_commit.png').write_bytes(organism.png(crop))
print('saved')
