import sys
from pathlib import Path

text = " ".join(sys.argv[1:]).strip()
if not text:
    raise SystemExit(2)
path = Path(__file__).resolve().parent / "ear.inject.txt"
prev = path.read_text(encoding="utf-8") if path.is_file() else ""
path.write_text(prev + text + "\n", encoding="utf-8")
