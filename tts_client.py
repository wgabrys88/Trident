from __future__ import annotations
import importlib
import json
import sys
from pathlib import Path
from tts_common import pipe_name, pid_path, pid_record, pipe_ready, running, synthesize_pipe

class TTSClient:
    def __init__(self, variant):
        self.variant = variant; self.pipe = pipe_name(variant); self.pid = pid_path(variant)
    @property
    def ready(self) -> bool: return running(self.pid) and pipe_ready(self.pipe, 0)
    @property
    def contract(self) -> dict | None:
        record = pid_record(self.pid); return (record or {}).get("contract")
    def synthesize(self, text: str, output: str | Path):
        if not self.ready:
            raise RuntimeError(f"{self.variant.name} server is not running; run tts_{self.variant.name}.py once to prepare it")
        return synthesize_pipe(self.pipe, self.pid, Path(output).resolve(), text)

def load_variant(name: str):
    if name not in ("nano","turbo","v3"): raise SystemExit("variant must be nano, turbo, or v3")
    return importlib.import_module(f"tts_{name}").CFG

def main(argv):
    if len(argv) != 4: raise SystemExit("usage: python tts_client.py nano|turbo|v3 OUTPUT.wav TEXT")
    client=TTSClient(load_variant(argv[1])); result=client.synthesize(argv[3],argv[2])
    print(json.dumps({"output":str(Path(argv[2]).resolve()),"wall_s":result.wall_s,"stats":vars(result),"contract":client.contract},indent=2,default=str)); return 0
if __name__ == "__main__": raise SystemExit(main(sys.argv))
