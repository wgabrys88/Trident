import json
import traceback
from datetime import datetime
from pathlib import Path


class Journal:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir, self.run_id = run_dir, run_dir.name
        self._events = (run_dir / "events.jsonl").open("a", encoding="utf-8", newline="\n", buffering=1)

    def emit(self, component: str, event: str, **fields) -> None:
        rec = {"schema_version": 2, "run_id": self.run_id,
               "wall_timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
               "component": component, "event": event, **fields}
        self._events.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")

    def transcript(self, role: str, text: str) -> None:
        if text:
            (self.run_dir / f"{role}.txt").open("a", encoding="utf-8").write(text.rstrip() + "\n")

    def failure(self, component: str, error: BaseException) -> None:
        self.emit(component, "failed", type=type(error).__name__, error=str(error))
        (self.run_dir / "failure.txt").open("a", encoding="utf-8").write(
            "".join(traceback.format_exception(type(error), error, error.__traceback__)))

    def close(self) -> None:
        if not self._events.closed:
            self._events.close()
