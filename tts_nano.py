import socket, struct, subprocess, sys, time, urllib.request, venv, wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHATTERBOX_REV = "94b16381640c7fe22481121da153d577213c0ef7"
CHATTERBOX = ROOT.parent / "chatterbox.cpp"
MODELS = ROOT / "models"
T3 = MODELS / "chatterbox-t3-nano-q4_0.gguf"
S3 = MODELS / "chatterbox-s3gen-nano-q4_0.gguf"
REF = ROOT / "reference.wav"
BUILD = CHATTERBOX / "build"
EXE = BUILD / "bin" / "chatterbox-server.exe"
CMAKE = "C:/Program Files/CMake/bin/cmake.exe"
VULKAN = Path("C:/VulkanSDK/1.4.357.0")
HF = "https://huggingface.co/ResembleAI/chatterbox-nano/resolve/71ccd1d0081b430592cea481f4307e764e07bc64"
GGML_REV = "7840aaba1989c6deeefede1d77d5aaf8f52b947e"
PORT = 17933
RATE = 24000
MAGIC, VERSION = 0x32525454, 4
FRAME = struct.Struct("<7I")
ASSETS = (
    "t3_nano_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt",
    "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json",
)


def run(cmd, **kw):
    print("+", *cmd)
    subprocess.run(cmd, check=True, **kw)


def build():
    ggml = CHATTERBOX / "ggml"
    if not (ggml / "CMakeLists.txt").is_file():
        run(["git", "clone", "--filter=blob:none", "https://github.com/ggml-org/ggml.git", str(ggml)])
        run(["git", "-C", str(ggml), "checkout", GGML_REV])
    run([
        CMAKE, "-S", str(CHATTERBOX), "-B", str(BUILD),
        "-G", "Visual Studio 17 2022", "-A", "x64",
        "-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_OPENMP=OFF",
        "-DBUILD_SHARED_LIBS=ON", "-DTTS_CPP_BUILD_EXECUTABLES=ON",
        "-DGGML_BUILD_TESTS=OFF", "-DGGML_BUILD_EXAMPLES=OFF",
        f"-DVulkan_INCLUDE_DIR={VULKAN / 'Include'}",
        f"-DVulkan_LIBRARY={VULKAN / 'Lib/vulkan-1.lib'}",
        f"-DVulkan_GLSLC_EXECUTABLE={VULKAN / 'Bin/glslc.exe'}",
    ])
    run([CMAKE, "--build", str(BUILD), "--config", "Release", "--target", "chatterbox-server", "--parallel"])


def convert():
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
            print("download", name)
            urllib.request.urlretrieve(f"{HF}/{name}", dest)
    MODELS.mkdir(parents=True, exist_ok=True)
    scripts = CHATTERBOX / "scripts"
    if not T3.is_file():
        run([str(py), str(scripts / "convert-t3-nano-to-gguf.py"),
             "--ckpt-dir", str(ckpt), "--out", str(T3), "--quant", "q4_0"])
    if not S3.is_file():
        run([str(py), str(scripts / "convert-s3gen-to-gguf.py"),
             "--ckpt-dir", str(ckpt), "--out", str(S3), "--quant", "q4_0"])


def wait_port(proc):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited {proc.returncode}")
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", PORT)) == 0:
                return
        time.sleep(0.1)
    proc.kill()
    raise TimeoutError("server did not bind")


def synthesize(text: str) -> Path:
    log = (ROOT / "tts.log").open("ab", buffering=0)
    proc = subprocess.Popen(
        [str(EXE), "--run-id", "nano", "--model", str(T3), "--s3gen-gguf", str(S3),
         "--reference", str(REF), "--port", str(PORT)],
        cwd=str(EXE.parent), stdout=log, stderr=subprocess.STDOUT)
    wait_port(proc)
    out = ROOT / "tts_out.wav"
    payload = text.encode("utf-8")
    with socket.create_connection(("127.0.0.1", PORT), timeout=300) as sock, sock.makefile("rb") as reader:
        sock.sendall(FRAME.pack(MAGIC, VERSION, 1, 1, 0, 1, len(payload)) + payload)
        with wave.open(str(out), "wb") as wav:
            wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            while True:
                header = reader.read(FRAME.size)
                kind = FRAME.unpack(header)[2]
                length = FRAME.unpack(header)[6]
                data = reader.read(length)
                if kind == 2:
                    break
                if kind == 4:
                    raise RuntimeError(data.decode("utf-8", errors="replace"))
                wav.writeframesraw(data)
        sock.sendall(FRAME.pack(MAGIC, VERSION, 3, 0, 0, 0, 0))
    proc.kill()
    proc.wait()
    log.close()
    return out


def main():
    text = sys.argv[1]
    if not REF.is_file():
        raise FileNotFoundError(REF)
    build()
    convert()
    wav = synthesize(text)
    print(wav)


if __name__ == "__main__":
    main()
