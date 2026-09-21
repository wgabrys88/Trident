import subprocess, sys
from pathlib import Path
from host import Host, Venv
from settings import VARIANTS

if __name__ == "__main__":
    py = Venv().ensure()
    if Path(sys.executable).resolve() != py.resolve():
        raise SystemExit(subprocess.call([str(py), *sys.argv]))
    argv = sys.argv
    if len(argv) < 3:
        raise SystemExit("query is required")
    name = Host().parser().parse_known_args(argv[1:2])[0].variant
    Host().run(VARIANTS[name], [argv[0], *argv[2:]])
