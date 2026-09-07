"""Remove generated data; keep Git, tracked source, models and ref-trump.wav."""

import argparse
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    tracked = subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "-z"], text=True
    ).split("\0")
    if not (root / ".git").exists() or "main.py" not in tracked:
        raise RuntimeError("Cannot identify the Trident checkout.")
    keep = {".git", "models", "clean.py", "ref-trump.wav"}
    keep.update(name.split("/")[0] for name in tracked if name)

    if not args.dry_run:
        subprocess.run(
            [sys.executable, str(root / "main.py"), "--unload"],
            cwd=root, check=True,
        )
    targets = [path for path in root.iterdir() if path.name not in keep]

    # Check every target before deletion; never traverse junctions or symlinks.
    def check(path):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise RuntimeError(f"Refusing linked path: {path}")

    def walk_error(error):
        raise error

    for path in targets:
        check(path)
        if path.resolve().parent != root:
            raise RuntimeError(f"Outside workspace: {path}")
        if path.is_dir():
            for directory, folders, files in os.walk(path, onerror=walk_error):
                for name in folders + files:
                    check(Path(directory) / name)

    for path in targets:
        print(f"Remove {path}")
        if not args.dry_run:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    print("Source, models and ref-trump.wav preserved. Next: python main.py")


if __name__ == "__main__":
    main()
