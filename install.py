import os, shutil, subprocess, sys, tempfile, time, urllib.request, venv, zipfile
from pathlib import Path

sys.dont_write_bytecode = True
from core import CFG, ROOT

def download(url, destination):
    print('Downloading '+destination.name, flush=True)
    pending = destination.with_suffix(destination.suffix+'.pending')
    deadline = time.monotonic()+300
    with urllib.request.urlopen(url, timeout=300) as response, pending.open('wb') as output:
        while chunk := response.read(1024*1024):
            if time.monotonic()>=deadline:
                raise TimeoutError('Download deadline exceeded: '+destination.name)
            output.write(chunk)
    pending.replace(destination)

def main():
    if sys.version_info[:2]!=(3, 11) or sys.platform!='win32':
        raise RuntimeError('Install with Python 3.11 on Windows')
    runtime = ROOT/'runtime'; runtime.mkdir(exist_ok=True)
    python = runtime/'python'/'Scripts'/'python.exe'
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(runtime/'python')
    with tempfile.TemporaryDirectory(prefix='install_', dir=runtime) as temporary:
        scratch = Path(temporary).resolve(); scratch.relative_to(runtime.resolve())
        environment = os.environ|dict(TEMP=str(scratch), TMP=str(scratch), HF_HOME=str(scratch/'hf'),
                                     PYTHONDONTWRITEBYTECODE='1')
        subprocess.run([str(python), '-B', '-m', 'pip', 'install', '--no-cache-dir', '-r', str(ROOT/'packages.txt')],
                       env=environment, check=True, timeout=600)
        native = runtime/'llama'; native.mkdir(exist_ok=True)
        for artifact in ('llama-b11435-bin-win-cuda-12.4-x64.zip', 'cudart-llama-bin-win-cuda-12.4-x64.zip'):
            archive = scratch/artifact
            download('https://github.com/ggml-org/llama.cpp/releases/download/b11435/'+artifact, archive)
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(native)
        models = runtime/'models'; models.mkdir(exist_ok=True)
        for name in (CFG['vision_model'], CFG['vision_projector']):
            if not (models/name).exists():
                download('https://huggingface.co/LiquidAI/LFM2.5-VL-3B-GGUF/resolve/'+
                         '6f730e9a2c454e8af9adc29db58e638e01e5957f/'+name, models/name)
        subprocess.run([str(python), '-B', '-c',
            'from faster_whisper.utils import download_model; import sys; download_model(sys.argv[1], output_dir=sys.argv[2])',
            CFG['asr_model'], str(models/'whisper')], env=environment, check=True, timeout=300)
        metadata = models/'whisper'/'.cache'
        if metadata.exists():
            metadata.resolve().relative_to(runtime.resolve()); shutil.rmtree(metadata)
    print('Installed. Start with: python run.py')

if __name__=='__main__':
    main()
