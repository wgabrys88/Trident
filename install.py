import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from i2c import Configuration


class Installer:
    def __init__(self):
        self.cfg = Configuration()

    def download(self, url, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=self.cfg["limits"]["install_timeout"]) as source, path.open("wb") as output:
            shutil.copyfileobj(source, output)

    def install(self):
        if sys.platform != "win32" or sys.version_info[:2] != (3, 11) or sys.maxsize <= 2**32:
            raise RuntimeError("64-bit Python 3.11 on Windows is required")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(self.cfg.root / "requirements.txt")], check=True)
        for filename, url in self.cfg["downloads"]["models"].items():
            self.download(url, self.cfg.root / "artifacts" / filename)
        with tempfile.TemporaryDirectory() as temporary:
            archive = self.cfg.path(temporary) / "archive.zip"
            for name, url in self.cfg["downloads"]["archives"].items():
                self.download(url, archive)
                with zipfile.ZipFile(archive) as files:
                    files.extractall(self.cfg.path(self.cfg["downloads"]["destinations"][name]))


if __name__ == "__main__":
    Installer().install()
