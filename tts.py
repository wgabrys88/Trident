import os, sys
from pathlib import Path
from host import Host, Venv
from settings import VARIANTS

if __name__ == "__main__":
    py = Venv().ensure()
    if Path(sys.executable).resolve() != py.resolve():
        os.execv(str(py), [str(py), *sys.argv])
    argv = sys.argv
    if len(argv) == 1:
        name, tail = "nano", ["--listen"]
    else:
        name = Host().parser().parse_known_args(argv[1:2])[0].variant
        tail = argv[2:]
    print(Host().run(VARIANTS[name], [argv[0], *tail]), flush=True)
