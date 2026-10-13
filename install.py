import os, shutil, subprocess, sys, tempfile, time, urllib.request, venv, zipfile
from pathlib import Path

sys.dont_write_bytecode = True
from core import CFG, ROOT

def download(url, destination):
    print('Downloading ' + destination.name, flush=True)
    pending = destination.with_name(destination.name + '.pending')
    deadline = time.monotonic() + 300
    with urllib.request.urlopen(url, timeout=300) as response, pending.open('wb') as output:
        while chunk := response.read(1024 * 1024):
            if time.monotonic() >= deadline:
                raise TimeoutError('Download deadline exceeded: ' + destination.name)
            output.write(chunk)
    pending.replace(destination)

def install():
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 11):
        raise RuntimeError('Use Python 3.11 on Windows')
    runtime = ROOT / 'runtime'
    runtime.mkdir(exist_ok=True)
    python = runtime / 'python' / 'Scripts' / 'python.exe'
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(runtime / 'python')
    with tempfile.TemporaryDirectory(prefix='install_', dir=runtime) as temporary:
        scratch = Path(temporary)
        environment = os.environ | dict(TEMP=str(scratch), TMP=str(scratch), HF_HOME=str(scratch / 'hf'),
            PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
        subprocess.run([str(python), '-B', '-m', 'pip', 'install', '--no-cache-dir', '-r', str(ROOT / 'packages.txt')],
                       env=environment, check=True, timeout=600)
        native = runtime / 'llama'
        native.mkdir(exist_ok=True)
        for filename in ('llama-b11435-bin-win-cuda-12.4-x64.zip', 'cudart-llama-bin-win-cuda-12.4-x64.zip'):
            archive = scratch / filename
            download('https://github.com/ggml-org/llama.cpp/releases/download/b11435/' + filename, archive)
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(native)
        models = runtime / 'models'
        models.mkdir(exist_ok=True)
        for filename in (CFG['vision_model'], CFG['vision_projector']):
            if not (models / filename).is_file():
                download('https://huggingface.co/LiquidAI/LFM2.5-VL-3B-GGUF/resolve/' +
                    '6f730e9a2c454e8af9adc29db58e638e01e5957f/' + filename, models / filename)
        subprocess.run([str(python), '-B', '-c',
            'from faster_whisper.utils import download_model; import sys; download_model(sys.argv[1], output_dir=sys.argv[2])',
            CFG['asr_model'], str(models / 'whisper')], env=environment, check=True, timeout=300)
        metadata = models / 'whisper' / '.cache'
        if metadata.exists():
            metadata.resolve().relative_to(runtime.resolve())
            shutil.rmtree(metadata)
    for cache in sorted(ROOT.rglob('__pycache__'), reverse=True): cache.resolve().relative_to(ROOT); shutil.rmtree(cache)
    print('Installed. Start with: python run.py')

if __name__ == '__main__':
    install()
