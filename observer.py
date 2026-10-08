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


if __name__ == "__main__":
    main()
