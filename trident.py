import os, sys
from contextlib import ExitStack
from agent import Trident
from core import ROOT, STATE, timestamp

if __name__ == "__main__":
    STATE.mkdir(parents=True, exist_ok=True)
    folder = ROOT / ("run_" + timestamp())
    folder.mkdir()
    with ExitStack() as resources:
        trident = Trident(folder, resources)
        try:
            trident.serve()
        except Exception as error:
            if trident.line.owner:
                trident.line.send(f"TRIDENT -> OWNER\n\n{type(error).__name__}: {error}", direction="blocked")
            raise
    if trident.restart:
        os.execv(sys.executable, [sys.executable, str(ROOT / "trident.py")])
