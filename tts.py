import sys
from host import Host
from settings import VARIANTS

def main():
    argv = list(sys.argv)
    selection, _ = Host().parser().parse_known_args(argv[1:2])
    name = selection.variant
    print(Host().run(VARIANTS[name], [argv[0], *argv[2:]]), flush=True)

if __name__ == "__main__":
    main()
