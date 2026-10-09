import shutil, subprocess, sys, tempfile, urllib.request, zipfile
from bus import Config

class Installer:
    def __init__(self):
        self.cfg, self.destination = Config(), Config().root / 'artifacts'
    def fetch(self, url, path):
        if path.exists(): return
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = path.with_suffix(path.suffix + '.part')
        with urllib.request.urlopen(url, timeout=self.cfg['limits']['install_seconds']) as response, pending.open('wb') as output:
            shutil.copyfileobj(response, output)
            if output.tell() != int(response.headers['Content-Length']): raise RuntimeError('Incomplete artifact')
        pending.replace(path)
    def run(self):
        if sys.platform != 'win32' or sys.version_info[:2] != (3, 11) or sys.maxsize <= 2**32: raise RuntimeError('Windows x64 Python 3.11 required')
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(self.cfg.root / 'requirements.txt')], check=True)
        for name, url in self.cfg['downloads']['models'].items(): self.fetch(url, self.destination / name)
        with tempfile.TemporaryDirectory() as directory:
            for name, url in self.cfg['downloads']['archives'].items():
                path = self.cfg.path(directory) / f'{name}.zip'
                self.fetch(url, path)
                with zipfile.ZipFile(path) as archive: archive.extractall(self.destination / {'cudart': 'llama'}.get(name, name))
        subprocess.run([str(self.destination / 'llama' / 'llama-server.exe'), '--version'], check=True)

Installer().run()
