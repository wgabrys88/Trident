import shutil, subprocess, sys, tempfile, urllib.request, zipfile
from bus import Config

def nvidia_gpu():
    try:
        completed = subprocess.run(['nvidia-smi', '-L'], capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and b'GPU' in completed.stdout

def select_archives(archives, nvidia):
    """Pick CUDA or Vulkan binaries. Runtime argv never names a vendor."""
    if nvidia:
        return dict(archives)
    selected = {}
    for name, url in archives.items():
        if name == 'cudart':
            continue
        if name == 'llama':
            needle, replacement = 'bin-win-cuda-12.4-x64.zip', 'bin-win-vulkan-x64.zip'
        elif name == 'voice':
            needle, replacement = 'x86_64-cuda126.zip', 'x86_64-vulkan.zip'
        else:
            selected[name] = url
            continue
        if needle not in url:
            raise RuntimeError(f'{name} archive URL has no CUDA build name')
        selected[name] = url.replace(needle, replacement, 1)
    return selected

class Installer:
    def __init__(self):
        self.cfg = Config()
        self.destination = self.cfg.root / 'artifacts'
        self.models = self.cfg.path(self.cfg['models']['path'])
    def fetch(self, url, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = path.with_suffix(path.suffix + '.part')
        with urllib.request.urlopen(url, timeout=self.cfg['limits']['install_seconds']) as response, pending.open('wb') as output:
            shutil.copyfileobj(response, output)
            if output.tell() != int(response.headers['Content-Length']): raise RuntimeError('Incomplete artifact')
        pending.replace(path)
    def flatten_voice(self, root):
        if (root / 'crispasr.exe').is_file():
            return
        folders = [path for path in root.iterdir() if path.is_dir()]
        if len(folders) != 1 or not (folders[0] / 'crispasr.exe').is_file():
            raise RuntimeError('Voice archive is not one crispasr directory')
        folder = folders[0]
        for path in list(folder.iterdir()):
            path.rename(root / path.name)
        folder.rmdir()
    def run(self):
        if sys.platform != 'win32' or sys.version_info[:2] != (3, 11) or sys.maxsize <= 2**32: raise RuntimeError('Windows x64 Python 3.11 required')
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(self.cfg.root / 'requirements.txt')], check=True)
        for name, url in self.cfg['downloads']['models'].items():
            path = self.models / name
            if not path.exists(): self.fetch(url, path)
        with tempfile.TemporaryDirectory() as directory:
            for name, url in select_archives(self.cfg['downloads']['archives'], nvidia_gpu()).items():
                path = self.cfg.path(directory) / f'{name}.zip'
                self.fetch(url, path)
                target = self.destination / {'cudart': 'llama'}.get(name, name)
                if name != 'cudart' and target.exists(): shutil.rmtree(target)
                with zipfile.ZipFile(path) as archive: archive.extractall(target)
                if name == 'voice': self.flatten_voice(target)
        subprocess.run([str(self.destination / 'llama' / 'llama-server.exe'), '--version'], check=True)
        subprocess.run([str(self.destination / 'voice' / 'crispasr.exe'), '--version'], check=True)

if __name__ == '__main__': Installer().run()
