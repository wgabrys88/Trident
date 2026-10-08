import sys
import time
from pathlib import Path

import i2c


def snapshot(root, run):
    root = Path(root)
    run = Path(run)
    log = run / "bus.log"
    lines = i2c.read_file(log).splitlines() if log.is_file() else []
    frames = []
    wire = root / "wire"
    if wire.is_dir():
        for path in sorted(item for item in wire.rglob("*") if item.is_file()):
            frames.append(path.relative_to(wire).as_posix() + " " + i2c.read_file(path))
    return lines, frames


def main():
    root, cfg = i2c.load()
    run = Path(sys.argv[1])
    poll = float(cfg["bus"]["poll"])
    seen = 0
    prev = None
    while True:
        lines, frames = snapshot(root, run)
        for line in lines[seen:]:
            print(line, flush=True)
        seen = len(lines)
        text = "\n".join(frames)
        if text != prev:
            for line in frames:
                print(line, flush=True)
            prev = text
        time.sleep(poll)


def test():
    import tempfile

    root = Path(tempfile.mkdtemp())
    run = root / "RUN_test"
    run.mkdir()
    (run / "bus.log").write_text("t 10 11 ack 1 S 11 W A 01 A P\n", encoding="utf-8")
    inbox = root / "wire" / "11" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "q-10-1").write_text("S 11 W A 01 A P\n", encoding="utf-8")
    before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
    saved = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    lines, frames = snapshot(root, run)
    assert lines == ["t 10 11 ack 1 S 11 W A 01 A P"]
    assert any(item.startswith("11/inbox/q-10-1 ") for item in frames)
    after = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
    assert before == after
    for path, data in saved.items():
        assert path.read_bytes() == data
    assert list(root.rglob("alive")) == []
    import shutil
    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test() if "--test" in sys.argv else main()
