import sys
import time
from pathlib import Path

import i2c


def tail(run):
    log = Path(run) / "bus.log"
    return i2c.read_file(log).splitlines() if log.is_file() else []


def main():
    _root, cfg = i2c.load()
    run = Path(sys.argv[1])
    poll = float(cfg["bus"]["poll"])
    seen = 0
    while True:
        lines = tail(run)
        for line in lines[seen:]:
            print(line, flush=True)
        seen = len(lines)
        time.sleep(poll)


def test():
    import tempfile

    root = Path(tempfile.mkdtemp())
    run = root / "RUN_test"
    run.mkdir()
    (run / "bus.log").write_text("t 10 11 ack 1 S 11 W A 01 A P\n", encoding="utf-8")
    inbox = root / "wire" / "11" / "inbox"
    inbox.mkdir(parents=True)
    frame = inbox / "q-10-1"
    frame.write_text("S 11 W A 01 A P\n", encoding="utf-8")
    before = frame.read_bytes()
    lines = tail(run)
    assert lines == ["t 10 11 ack 1 S 11 W A 01 A P"]
    assert frame.read_bytes() == before
    assert list(root.rglob("alive")) == []
    import shutil
    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test() if "--test" in sys.argv else main()
