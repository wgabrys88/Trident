import ctypes, hashlib, subprocess, sys, time, urllib.request, venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHATTERBOX_REV = "06770cc53458461646cc9247fe13d22d0c777f7d"
CHATTERBOX = ROOT.parent / "chatterbox.cpp"
MODELS, REF = ROOT / "models", ROOT / "reference.wav"
T3, S3 = MODELS / "chatterbox-t3-v3-q8_0.gguf", MODELS / "chatterbox-s3gen-v3-q4_0.gguf"
STAMP, REV, PID = MODELS / "v3.voice.sha256", MODELS / "v3.rev", MODELS / "v3.pid"
BUILD = CHATTERBOX / "build" / "v3"
BIN = BUILD / "bin"
EXE, BAKE = BIN / "chatterbox-server.exe", BIN / "chatterbox-bake.exe"
PIPE = r"\\.\pipe\chatterbox-v3-" + hashlib.sha256(str(ROOT).encode() + b"v3").hexdigest()[:12]
K32 = ctypes.windll.kernel32
K32.WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
HF = "https://huggingface.co/ResembleAI/chatterbox/resolve/5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
GGML_REV = "7840aaba1989c6deeefede1d77d5aaf8f52b947e"
ASSETS = ("t3_mtl23ls_v3.safetensors", "s3gen.safetensors", "conds.pt", "ve.safetensors", "grapheme_mtl_merged_expanded_v1.json", "Cangjie5_TC.json")
VULKAN = Path("C:/VulkanSDK/1.4.357.0")
CMAKE = "C:/Program Files/CMake/bin/cmake.exe"
DETACH = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW

def run(cmd, **kw):
    subprocess.run(cmd, check=True, **kw)

def git_out(args, repo=CHATTERBOX):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()

def ensure_pin():
    run(["git", "-C", str(CHATTERBOX), "checkout", "v3"])
    if (sha := git_out(["rev-parse", "HEAD"])) != CHATTERBOX_REV:
        raise SystemExit(f"HEAD {sha} != {CHATTERBOX_REV}")

def kill(pid=PID):
    if not pid.is_file():
        return
    h = K32.OpenProcess(1, False, int(pid.read_text(encoding="ascii")))
    if h:
        K32.TerminateProcess(h, 1)
        K32.CloseHandle(h)
    pid.unlink(missing_ok=True)

def running():
    if not PID.is_file():
        return False
    h = K32.OpenProcess(0x1000, False, int(PID.read_text(encoding="ascii")))
    if not h:
        return False
    K32.CloseHandle(h)
    return True

def spawn(out, language):
    PID.write_text(str(subprocess.Popen(
        [str(EXE), str(T3), str(S3), str(out), PIPE, language], cwd=str(BIN),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=DETACH,
    ).pid), encoding="ascii")

def speak(text):
    while running():
        if K32.WaitNamedPipeW(PIPE, 1000):
            break
    else:
        raise RuntimeError("daemon")
    f = open(PIPE, "r+b", buffering=0)
    f.write((text.replace("\r", " ").replace("\n", " ") + "\n").encode())
    ack = f.readline()
    f.close()
    if ack != b"ok\n":
        raise RuntimeError("synthesize")

def main():
    if len(sys.argv) < 3:
        raise SystemExit("language")
    if not REF.is_file():
        raise FileNotFoundError(str(REF))
    language = sys.argv[2].lower()
    ensure_pin()
    MODELS.mkdir(parents=True, exist_ok=True)
    for p in ("server.pid", "turbo.pid"):
        kill(MODELS / p)
    ggml = CHATTERBOX / "ggml"
    if not EXE.is_file() or not BAKE.is_file() or not REV.is_file() or REV.read_text(encoding="ascii") != CHATTERBOX_REV:
        if not (ggml / "CMakeLists.txt").is_file():
            run(["git", "clone", "--filter=blob:none", "https://github.com/ggml-org/ggml.git", str(ggml)])
            run(["git", "-C", str(ggml), "checkout", GGML_REV])
        elif git_out(["rev-parse", "HEAD"], ggml) != GGML_REV:
            raise SystemExit(f"ggml {git_out(['rev-parse', 'HEAD'], ggml)} != {GGML_REV}")
        run([CMAKE, "-S", str(CHATTERBOX), "-B", str(BUILD), "-G", "Visual Studio 17 2022", "-A", "x64",
             "-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_CPU=OFF", "-DGGML_OPENMP=OFF",
             "-DBUILD_SHARED_LIBS=ON", "-DTTS_CPP_BUILD_EXECUTABLES=ON", "-DGGML_BUILD_TESTS=OFF", "-DGGML_BUILD_EXAMPLES=OFF",
             f"-DVulkan_INCLUDE_DIR={VULKAN / 'Include'}", f"-DVulkan_LIBRARY={VULKAN / 'Lib/vulkan-1.lib'}",
             f"-DVulkan_GLSLC_EXECUTABLE={VULKAN / 'Bin/glslc.exe'}"])
        run([CMAKE, "--build", str(BUILD), "--config", "Release", "--target", "chatterbox-server", "--target", "chatterbox-bake", "--parallel"])
        REV.write_text(CHATTERBOX_REV, encoding="ascii")
        kill()
    py = ROOT / ".venv-convert-v3" / "Scripts" / "python.exe"
    if not py.is_file():
        venv.EnvBuilder(with_pip=True).create(ROOT / ".venv-convert-v3")
        pip = [str(py), "-m", "pip", "install", "--disable-pip-version-check"]
        run([*pip, "torch==2.6.0", "--index-url", "https://download.pytorch.org/whl/cpu"])
        run([*pip, "numpy==1.26.4", "gguf==0.19.0", "safetensors==0.5.3", "scipy==1.15.3", "librosa==0.11.0"])
    ckpt = ROOT / ".ckpt-v3"
    ckpt.mkdir(parents=True, exist_ok=True)
    for name in ASSETS:
        dest = ckpt / name
        if not dest.is_file():
            urllib.request.urlretrieve(f"{HF}/{name}", dest)
    converted = False
    for dst, script in ((T3, "convert-t3-v3-to-gguf.py"), (S3, "convert-s3gen-v3-to-gguf.py")):
        if not dst.is_file():
            run([str(py), str(CHATTERBOX / "scripts" / script), str(ckpt), str(dst)])
            converted = True
    voice = hashlib.sha256(REF.read_bytes()).hexdigest()
    stamp = STAMP.read_text(encoding="ascii").strip() if STAMP.is_file() else ""
    out = ROOT / time.strftime("%Y%m%d-%H%M%S-v3.wav")
    if converted or stamp != voice:
        kill()
        run([str(BAKE), str(T3), str(S3), str(REF)], cwd=str(BIN))
        STAMP.write_text(voice, encoding="ascii")
    if not running():
        spawn(out, language)
    speak(sys.argv[1])
    print(out)

if __name__ == "__main__":
    main()
