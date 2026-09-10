import subprocess, sys, urllib.request, venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHATTERBOX_REV = "339053f3318a0d2468e21df746cc488ad34cfe19"
CHATTERBOX = ROOT.parent / "chatterbox.cpp"
MODELS = ROOT / "models"
T3 = MODELS / "chatterbox-t3-nano-q8_0.gguf"
S3 = MODELS / "chatterbox-s3gen-nano-q4_0.gguf"
REF = ROOT / "reference.wav"
BUILD = CHATTERBOX / "build"
EXE = BUILD / "bin" / "chatterbox-server.exe"
CMAKE = "C:/Program Files/CMake/bin/cmake.exe"
VULKAN = Path("C:/VulkanSDK/1.4.357.0")
HF = "https://huggingface.co/ResembleAI/chatterbox-nano/resolve/71ccd1d0081b430592cea481f4307e764e07bc64"
GGML_REV = "7840aaba1989c6deeefede1d77d5aaf8f52b947e"
ASSETS = (
    "t3_nano_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt",
    "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json",
)

def run(cmd, **kw):
    subprocess.run(cmd, check=True, **kw)

def main():
    ggml = CHATTERBOX / "ggml"
    run(["git", "-C", str(CHATTERBOX), "checkout", CHATTERBOX_REV])
    if not (ggml / "CMakeLists.txt").is_file():
        run(["git", "clone", "--filter=blob:none", "https://github.com/ggml-org/ggml.git", str(ggml)])
        run(["git", "-C", str(ggml), "checkout", GGML_REV])
    run([
        CMAKE, "-S", str(CHATTERBOX), "-B", str(BUILD),
        "-G", "Visual Studio 17 2022", "-A", "x64",
        "-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_CPU=OFF", "-DGGML_OPENMP=OFF",
        "-DBUILD_SHARED_LIBS=ON", "-DTTS_CPP_BUILD_EXECUTABLES=ON",
        "-DGGML_BUILD_TESTS=OFF", "-DGGML_BUILD_EXAMPLES=OFF",
        f"-DVulkan_INCLUDE_DIR={VULKAN / 'Include'}",
        f"-DVulkan_LIBRARY={VULKAN / 'Lib/vulkan-1.lib'}",
        f"-DVulkan_GLSLC_EXECUTABLE={VULKAN / 'Bin/glslc.exe'}",
    ])
    run([CMAKE, "--build", str(BUILD), "--config", "Release", "--target", "chatterbox-server", "--parallel"])
    venv_dir = ROOT / ".venv-convert"
    py = venv_dir / "Scripts" / "python.exe"
    if not py.is_file():
        venv.EnvBuilder(with_pip=True).create(venv_dir)
        pip = [str(py), "-m", "pip", "install", "--disable-pip-version-check"]
        run([*pip, "torch==2.6.0", "--index-url", "https://download.pytorch.org/whl/cpu"])
        run([*pip, "numpy==1.26.4", "gguf==0.19.0", "safetensors==0.5.3", "scipy==1.15.3", "librosa==0.11.0"])
    ckpt = ROOT / ".ckpt"
    ckpt.mkdir(parents=True, exist_ok=True)
    for name in ASSETS:
        dest = ckpt / name
        if not dest.is_file():
            urllib.request.urlretrieve(f"{HF}/{name}", dest)
    MODELS.mkdir(parents=True, exist_ok=True)
    scripts = CHATTERBOX / "scripts"
    if not T3.is_file():
        run([str(py), str(scripts / "convert-t3-nano-to-gguf.py"), str(ckpt), str(T3)])
    if not S3.is_file():
        run([str(py), str(scripts / "convert-s3gen-to-gguf.py"), str(ckpt), str(S3)])
    out = ROOT / "tts_out.wav"
    run([str(EXE), str(T3), str(S3), str(REF), str(out), sys.argv[1]], cwd=str(EXE.parent))
    print(out)

if __name__ == "__main__":
    main()
