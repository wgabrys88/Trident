import base64
import ctypes
import io
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "endgame-vision-chat"))
import gemma
import winapi

PROOF = ROOT / "desk.proof.txt"
MEDIA = "<__media__>"
PATCH = 48
AREA = 16 * 16 * 3 * 3
TOKENS = 560
SYSTEM = (
    "You see one full desktop screenshot. "
    "Answer with one JSON object and no other text. "
    "see quotes the readable sentences and the numbers. "
    "taskbar lists every bottom-edge icon from left to right, each with name and box_2d. "
    "windows lists each open window with name and box_2d. "
    "box_2d is y_min, x_min, y_max, x_max, integers 0 to 1000, top left origin."
)
USER = "Describe the whole desktop. Quote the chat words. Name each taskbar icon from left to right."


def note(text):
    row = time.strftime("%H:%M:%S") + " " + (text or "").replace("\r\n", "\n").replace("\r", "\n")
    print(row, flush=True)
    with PROOF.open("a", encoding="utf-8") as handle:
        handle.write(row + "\n")


def powershell(command):
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        cwd=ROOT,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return (completed.stdout or "") + (completed.stderr or "")


def watch(tag):
    note(tag)
    note(
        powershell(
            "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'gemma-brain|llama' -or ($_.CommandLine -and $_.CommandLine -match 'gemma-brain|llama-server|llama-cli') } | ForEach-Object { '{0} parent {1} ws_mb {2:N1} {3}' -f $_.ProcessId, $_.ParentProcessId, ($_.WorkingSetSize/1MB), $_.CommandLine }"
        ).rstrip()
    )
    note(
        powershell(
            "$s = (Get-Counter '\\GPU Process Memory(*)\\Dedicated Usage','\\GPU Process Memory(*)\\Shared Usage').CounterSamples | Where-Object { $_.CookedValue -gt 50MB }; $s | ForEach-Object { '{0} {1:N0} {2}' -f ($_.Path -replace '.*\\\\',''), $_.CookedValue, $_.InstanceName }"
        ).rstrip()
    )
    note(powershell("nvidia-smi --query-gpu=memory.total,memory.used,memory.free --format=csv,noheader").rstrip())


def prompt():
    return (
        "<bos><|turn>system\n"
        + SYSTEM
        + "<turn|>\n<|turn>user\n"
        + MEDIA
        + "\n"
        + USER
        + "<turn|>\n<|turn>model\n"
    )


def log_tail():
    text = (ROOT / "gemma.run.err").read_text(encoding="utf-8", errors="replace")
    kept = []
    for line in text.splitlines():
        if (
            line.startswith("cuda[")
            or line.startswith("resident load")
            or line.startswith("clip_encode: copying")
            or line.startswith("image slice encoded")
            or line.startswith("decoding image")
            or line.startswith("image decoded")
            or line.startswith("resident generate")
            or "image_min_pixels" in line
            or "image_max_pixels" in line
        ):
            kept.append(line)
    return "\n".join(kept[-16:])


def windows(sw, sh):
    rows = []
    for item in winapi.enum_windows():
        rect = item["rect"]
        rows.append(
            "%s %d %d %d %d %d,%d,%d,%d"
            % (
                item["title"].replace("\n", " "),
                round(rect["top"] / sh * 1000),
                round(rect["left"] / sw * 1000),
                round(rect["bottom"] / sh * 1000),
                round(rect["right"] / sw * 1000),
                rect["left"],
                rect["top"],
                rect["right"],
                rect["bottom"],
            )
        )
    return "\n".join(rows)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    note("revision 11")
    note(time.strftime("%Y-%m-%d %H:%M:%S"))
    watch("processes before")
    winapi.init_dpi()
    winapi.user32.GetDpiForSystem.restype = ctypes.c_uint
    note("dpi %s" % int(winapi.user32.GetDpiForSystem()))
    pairs = gemma.config_pairs(gemma.config_body())
    for key in (
        "gemma.ctx",
        "gemma.batch",
        "gemma.ubatch",
        "gemma.image-min-tokens",
        "gemma.image-max-tokens",
        "gemma.mtmd-batch",
        "gemma.temp",
        "gemma.top-k",
        "gemma.top-p",
        "gemma.seed",
    ):
        note(key + " " + pairs[key])
    sw, sh = winapi.get_screen_size()
    png, rw, rh = winapi.capture_screenshot_png(sw, sh)
    full = Image.open(io.BytesIO(png)).convert("RGB")
    scale = ((TOKENS * AREA) / float(full.width * full.height)) ** 0.5
    wide = max(PATCH, int(round(full.width * scale / PATCH)) * PATCH)
    high = max(PATCH, int(round(full.height * scale / PATCH)) * PATCH)
    image = full.resize((wide, high), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    data = buf.getvalue()
    note("capture screen %d %d frame %d %d bytes %d sent %d %d bytes %d raw %d %d" % (sw, sh, full.width, full.height, len(png), wide, high, len(data), rw, rh))
    note("windows <<\n" + windows(sw, sh) + "<<")
    text = prompt()
    note("prompt <<\n" + text + "<<")
    pid = gemma.ensure_resident()
    note("ready pid %s" % pid)
    t0 = time.time()
    reply = gemma.resident_generate(text, base64.b64encode(data).decode("ascii"), False)
    note("seconds %.3f" % (time.time() - t0))
    note("log <<\n" + log_tail() + "<<")
    note("gemma <<\n" + reply + "<<")
    watch("processes after")


if __name__ == "__main__":
    main()
