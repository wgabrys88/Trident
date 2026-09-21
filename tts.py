import sys
from host import Host
from settings import VARIANTS

if __name__ == "__main__":
    argv = sys.argv
    name = Host().parser().parse_known_args(argv[1:2])[0].variant
    print(Host().run(VARIANTS[name], [argv[0], *argv[2:]]), flush=True)
