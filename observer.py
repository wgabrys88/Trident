import sys
import time
from pathlib import Path

from i2c import Configuration


class Observer:
    def follow(self, run):
        poll = Configuration()["bus"]["poll"]
        with (Path(run) / "bus.log").open(encoding="utf-8") as journal:
            while True:
                position = journal.tell()
                if (line := journal.readline()).endswith("\n"):
                    print(line, end="", flush=True)
                else:
                    journal.seek(position)
                    time.sleep(poll)


if __name__ == "__main__":
    Observer().follow(sys.argv[1])
