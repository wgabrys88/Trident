from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "trident.txt"


def read_cfg():
    out = {}
    lines = CFG.read_text(encoding="utf-8-sig").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#"):
            continue
        if line.endswith("<<"):
            key = line[:-2].strip()
            body = []
            while i < len(lines) and lines[i] != "<<":
                body.append(lines[i])
                i += 1
            i += 1
            out[key] = "\n".join(body)
            continue
        if " " not in line:
            out[line] = ""
        else:
            key, value = line.split(" ", 1)
            out[key] = value
    return out
