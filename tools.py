import json
import subprocess
from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def run(command):
    done = subprocess.run(command, shell=True, capture_output=True, text=True)
    text = (done.stdout or "") + (done.stderr or "")
    if done.returncode:
        return text + f"\nexit {done.returncode}"
    return text


def shot(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen; "
        "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height; "
        "$g=[System.Drawing.Graphics]::FromImage($bmp); "
        "$g.CopyFromScreen($b.X,$b.Y,0,0,$bmp.Size); "
        f"$bmp.Save('{path}',[System.Drawing.Imaging.ImageFormat]::Png)"
    )
    done = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True)
    if done.returncode or not path.is_file():
        raise RuntimeError((done.stderr or "shot failed").strip())
    return str(path)


def act(line):
    text = line[5:] if line.startswith("tool ") else line
    name, raw = text.split(" ", 1)
    args = json.loads(raw)
    if name == "read":
        return read(args["path"])
    if name == "run":
        return run(args["command"])
    if name == "shot":
        return shot(args["path"])
    raise RuntimeError("Unknown tool " + name)


def card():
    return "\n".join((
        'tool read {"path": "C:\\\\file"}',
        'tool run {"command": "dir"}',
        'tool shot {"path": "C:\\\\screen.png"}',
        "A tool line is the word tool, the name, and one JSON object. "
        "shot writes a PNG of the whole desktop. "
        "Pictures use a 0 to 1000 grid, y down and x right. A point on the picture is not a desktop pixel.",
    ))


if __name__ == "__main__":
    print(act("read " + json.dumps({"path": str(Path(__file__).resolve())})).splitlines()[0])
