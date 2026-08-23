"""Apply deterministic source transforms to a pristine chatterbox.cpp checkout."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_patch import OPS


def apply(root: Path) -> int:
    applied = 0
    for rel, pairs in OPS.items():
        path = root / rel
        raw = path.read_bytes()
        crlf = b"\r\n" in raw
        text = raw.decode("utf-8").replace("\r\n", "\n") if crlf else raw.decode("utf-8")
        for old, new in pairs:
            count = text.count(old)
            if count != 1:
                raise RuntimeError(
                    f"native patch anchor found {count} times (want 1) in {rel}:\n{old[:400]}"
                )
            text = text.replace(old, new, 1)
            applied += 1
        if crlf:
            text = text.replace("\n", "\r\n")
        path.write_bytes(text.encode("utf-8"))
    return applied


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    print(f"native patch: {apply(root)} transforms over {len(OPS)} files")
