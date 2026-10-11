"""Seat setup for version two. Not part of the wheel."""

import os
import subprocess
import sys
import urllib.request
from pathlib import Path

MODELS = Path(os.path.expandvars(r'%LOCALAPPDATA%\Trident\models'))
FILES = {
    'GLM-OCR-Q8_0.gguf': 'https://huggingface.co/ggml-org/GLM-OCR-GGUF/resolve/main/GLM-OCR-Q8_0.gguf',
    'mmproj-GLM-OCR-Q8_0.gguf': 'https://huggingface.co/ggml-org/GLM-OCR-GGUF/resolve/main/mmproj-GLM-OCR-Q8_0.gguf',
}


def fetch(url, dest):
    request = urllib.request.Request(url, headers={'User-Agent': 'trident-version-two'})
    with urllib.request.urlopen(request) as response, dest.open('wb') as handle:
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            handle.write(block)


def main():
    here = Path(__file__).resolve().parent
    env = here / '.env'
    if not env.exists():
        env.write_text((here / '.env.example').read_text(encoding='utf-8'), encoding='utf-8')
    print(env)
    MODELS.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, '-m', 'pip', 'install', 'pillow'], check=True)
    for name, url in FILES.items():
        dest = MODELS / name
        if dest.exists():
            print(dest)
            continue
        fetch(url, dest)
        print(dest)


main()
