from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path


def _bootstrap() -> None:
    if sys.version_info < (3, 11):
        raise RuntimeError("Trident requires Python 3.11 or newer")
    if os.name != "nt" and not sys.platform.startswith("linux"):
        raise RuntimeError(f"Trident supports Windows and Linux, not {sys.platform}")
    if sys.argv[1:]:
        raise RuntimeError("Trident takes no command-line arguments; run: python main.py")
    root = Path(__file__).resolve().parent
    env = root / ".venv"
    builder = venv.EnvBuilder(with_pip=True)
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    requirements = root / "requirements.txt"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = env / ".trident-runtime"
    if not python.is_file():
        builder.create(env)
    if not marker.is_file() or marker.read_text(encoding="ascii").strip() != digest:
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(requirements)], check=True)
        marker.write_text(digest + "\n", encoding="ascii")
    if Path(sys.prefix).resolve() != env.resolve():
        os.execv(str(python), [str(python), "-X", "utf8", str(Path(__file__).resolve())])


def main() -> int:
    from installer import install
    from log import finish, start_run, write_meta
    from ui import launch

    paths = start_run("install")
    outcome = "error"
    try:
        install(paths.models_dir, paths.data_dir)
        outcome = "ok"
    finally:
        write_meta(paths, command="install", outcome=outcome)
        finish(paths, outcome)
    launch()
    return 0


if __name__ == "__main__":
    _bootstrap()
    raise SystemExit(main())
