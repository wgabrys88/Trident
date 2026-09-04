import argparse, hashlib, http.client, json, shutil, socket, subprocess, sys, tempfile, threading, time, urllib.request, zipfile
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "tools/runtime/brain"
EXE = RUNTIME / "llama-server.exe"
RUNTIME_REVISION = RUNTIME / "REVISION"
MODEL = ROOT / "models/gemma-4-E2B_q4_0-it.gguf"
MODEL_CARD = MODEL.with_suffix(".md")
LLAMA_REV = "b10621"
ARCHIVE = f"llama-{LLAMA_REV}-bin-win-vulkan-x64.zip"
RUNTIME_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_REV}/{ARCHIVE}"
RUNTIME_SHA = "2672d85bf87c8280d94dee01eb6a86280046878f70a07d786a93637fa9081163"
GEMMA_REV = "675cff42a74c774d6cb76f76d8eacb49b48c9b93"
MODEL_URL = f"https://huggingface.co/google/gemma-4-E2B-it-qat-q4_0-gguf/resolve/{GEMMA_REV}/{MODEL.name}"
MODEL_CARD_URL = f"https://huggingface.co/google/gemma-4-E2B-it-qat-q4_0-gguf/resolve/{GEMMA_REV}/README.md"
MODEL_SIZE = 3_349_516_256
MODEL_SHA = "fa401b55b07ee70a54c6dae3903c783a6e65064312529ea57175cb5f8dec6634"
HOST, PORT = "127.0.0.1", 17932
ALIAS = "gemma"
DEVICE, GPU_LAYERS = "Vulkan0", "all"
CONTEXT, BATCH, UBATCH = 4096, 2048, 512
THREADS = THREADS_BATCH = THREADS_HTTP = 2
POLL = POLL_BATCH = 0
PARALLEL, FLASH_ATTN, REASONING = 1, "off", "off"
STARTUP_TIMEOUT, REQUEST_TIMEOUT = 180.0, 3600.0
CACHE_PROMPT = True
TEMPERATURE, TOP_P, TOP_K, MIN_P = 0.2, 0.95, 64, 0.0
REPEAT_PENALTY, SEED, MAX_TOKENS = 1.0, 42, 1024
SYSTEM_PROMPT = "Produce the spoken reply to the user. Answer directly and correctly. Output only natural speech with short sentences. Do not use markdown, lists, code, URLs, emoji, stage directions, meta-commentary, or reasoning. Keep ordinary answers under sixty spoken words unless the request requires more. Expand numbers and abbreviations when useful for speech. Use the user's language."
SERVER_ARGS = ["--alias", ALIAS, "--host", HOST, "--port", str(PORT), "--offline", "--device", DEVICE,
               "--n-gpu-layers", GPU_LAYERS, "--ctx-size", str(CONTEXT), "--batch-size", str(BATCH),
               "--ubatch-size", str(UBATCH), "--threads", str(THREADS), "--threads-batch", str(THREADS_BATCH),
               "--poll", str(POLL), "--poll-batch", str(POLL_BATCH), "--threads-http", str(THREADS_HTTP),
               "--parallel", str(PARALLEL), "--flash-attn", FLASH_ATTN, "--no-mmproj", "--no-ui",
               "--reasoning", REASONING]
REQUEST_ARGS = {"temperature": TEMPERATURE, "top_p": TOP_P, "top_k": TOP_K, "min_p": MIN_P,
                "repeat_penalty": REPEAT_PENALTY, "seed": SEED, "max_tokens": MAX_TOKENS}
_READY = "llama_server: listening on http://127.0.0.1:"

_PROCESS, _READY_EVENT, _READER, _TAIL = None, None, None, deque(maxlen=80)


def _sha(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _download(url: str, path: Path, size: int = 0, sha: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.unlink(missing_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TridentBrain/1"})
        with urllib.request.urlopen(req, timeout=3600) as src, partial.open("wb") as dst:
            shutil.copyfileobj(src, dst, 4 << 20)
        if size and partial.stat().st_size != size:
            raise RuntimeError(f"Download size mismatch: {path.name}")
        if sha and _sha(partial) != sha:
            raise RuntimeError(f"Download checksum mismatch: {path.name}")
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


def _port_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex((HOST, PORT)) == 0


def _install() -> None:
    runtime_ok = EXE.is_file() and RUNTIME_REVISION.is_file() and RUNTIME_REVISION.read_text(encoding="utf-8").strip() == f"{LLAMA_REV} {RUNTIME_SHA}"
    model_ok = MODEL.is_file() and MODEL.stat().st_size == MODEL_SIZE and _sha(MODEL) == MODEL_SHA
    if MODEL.exists() and not model_ok:
        raise RuntimeError(f"Refusing unverified existing model: {MODEL}")
    with tempfile.TemporaryDirectory(prefix=".brain-install-", dir=ROOT) as tmp:
        work = Path(tmp)
        if not runtime_ok:
            archive = work / ARCHIVE
            _download(RUNTIME_URL, archive, sha=RUNTIME_SHA)
            if RUNTIME.exists():
                shutil.rmtree(RUNTIME)
            RUNTIME.mkdir(parents=True)
            with zipfile.ZipFile(archive) as z:
                members = [n for n in z.namelist() if Path(n).name == EXE.name]
                if len(members) != 1:
                    raise RuntimeError(f"Expected one {EXE.name} in verified runtime archive")
                parent = Path(members[0]).parent
                for name in z.namelist():
                    if name and not name.endswith("/") and Path(name).parent == parent:
                        with z.open(name) as src, (RUNTIME / Path(name).name).open("wb") as dst:
                            shutil.copyfileobj(src, dst)
            if not EXE.is_file():
                raise RuntimeError(f"{EXE.name} missing after runtime installation")
            RUNTIME_REVISION.write_text(f"{LLAMA_REV} {RUNTIME_SHA}\n", encoding="utf-8")
        if not model_ok:
            downloaded = work / MODEL.name
            _download(MODEL_URL, downloaded, MODEL_SIZE, MODEL_SHA)
            MODEL.parent.mkdir(parents=True, exist_ok=True)
            downloaded.replace(MODEL)
        if not MODEL_CARD.is_file():
            _download(MODEL_CARD_URL, MODEL_CARD)


def _drain() -> None:
    global _READY_EVENT
    for line in _PROCESS.stdout:
        _TAIL.append(line.rstrip())
        if _READY in line:
            _READY_EVENT.set()


def _start() -> None:
    global _PROCESS, _READY_EVENT, _READER
    if _PROCESS is not None and _PROCESS.poll() is None:
        return
    if not EXE.is_file() or not RUNTIME_REVISION.is_file() or RUNTIME_REVISION.read_text(encoding="utf-8").strip() != f"{LLAMA_REV} {RUNTIME_SHA}":
        raise RuntimeError("Brain runtime missing; run python brain.py")
    if not MODEL.is_file() or MODEL.stat().st_size != MODEL_SIZE:
        raise RuntimeError("Gemma model missing; run python brain.py")
    if _port_in_use():
        raise RuntimeError(f"Brain port {PORT} is already occupied")
    _PROCESS = subprocess.Popen(
        [str(EXE), "--model", str(MODEL), *SERVER_ARGS],
        cwd=RUNTIME, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0)
    _READY_EVENT = threading.Event()
    _READER = threading.Thread(target=_drain, daemon=True)
    _READER.start()
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while not _READY_EVENT.wait(0.05):
        if _PROCESS.poll() is not None:
            raise RuntimeError(f"llama-server exited with {_PROCESS.returncode}\n" + "\n".join(_TAIL))
        if time.monotonic() >= deadline:
            _stop()
            raise TimeoutError("llama-server startup timed out\n" + "\n".join(_TAIL))


def _stop() -> None:
    global _PROCESS, _READY_EVENT, _READER
    proc, _PROCESS = _PROCESS, None
    if proc is None:
        return
    if proc.poll() is None:
        proc.kill()
    proc.wait()
    if _READER is not None:
        _READER.join(timeout=5)
        _READER = None
    if proc.stdout is not None:
        proc.stdout.close()
    if _READY_EVENT is not None:
        _READY_EVENT.clear()


class Brain:
    def __init__(self, system_prompt: str = SYSTEM_PROMPT) -> None:
        self.system_prompt = system_prompt

    def start(self) -> Brain:
        _start()
        return self

    def __enter__(self):
        return self.start()

    def __exit__(self, *_) -> None:
        pass

    def stream(self, request: str):
        request = request.strip()
        if not request:
            raise ValueError("request must not be empty")
        _start()
        conn = http.client.HTTPConnection(HOST, PORT, timeout=REQUEST_TIMEOUT)
        try:
            body = json.dumps({
                "model": ALIAS,
                "messages": [{"role": "system", "content": self.system_prompt},
                             {"role": "user", "content": request}],
                "stream": True, "cache_prompt": CACHE_PROMPT,
                "chat_template_kwargs": {"enable_thinking": False}, **REQUEST_ARGS,
            }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            conn.request("POST", "/v1/chat/completions", body=body,
                         headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
            resp = conn.getresponse()
            if resp.status != 200:
                raise RuntimeError(f"Gemma HTTP {resp.status}: {resp.read().decode('utf-8', 'replace')[-2000:]}")
            while line := resp.readline():
                if not line.startswith(b"data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == b"[DONE]":
                    return
                data = json.loads(chunk)
                text = str((data.get("choices") or [{}])[0].get("delta", {}).get("content") or "")
                if text:
                    yield text
        finally:
            conn.close()

    def ask(self, request: str) -> str:
        ans = "".join(self.stream(request)).replace("\r", "").strip()
        marker = "Assistant:\n"
        return ans.rsplit(marker, 1)[-1].strip() if marker in ans else ans


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--load", action="store_true")
    p.add_argument("--unload", action="store_true")
    p.add_argument("--request")
    args = p.parse_args()
    if args.load:
        if _port_in_use():
            sys.exit(0)
        _install()
        _start()
        sys.exit(0)
    if args.unload:
        _stop()
        sys.exit(0)
    if args.request is not None:
        started = time.perf_counter()
        with Brain() as brain:
            ready = time.perf_counter()
            first = None
            for chunk in brain.stream(args.request):
                if first is None:
                    first = time.perf_counter()
                print(chunk, end="", flush=True)
            finished = time.perf_counter()
        print()
        print(f"startup_s={ready-started:.3f} ttft_s={(first or finished)-ready:.3f} inference_s={finished-ready:.3f}", file=sys.stderr)
    else:
        _install()
        text = (ROOT / "pipe_in.txt").read_text(encoding="utf-8")
        started = time.perf_counter()
        with Brain() as brain:
            ready = time.perf_counter()
            first = None
            chunks = []
            for chunk in brain.stream(text):
                if first is None:
                    first = time.perf_counter()
                chunks.append(chunk)
            finished = time.perf_counter()
        answer = "".join(chunks).replace("\r", "").strip()
        marker = "Assistant:\n"
        if marker in answer:
            answer = answer.rsplit(marker, 1)[-1].strip()
        if not answer:
            raise RuntimeError("Brain produced no spoken reply")
        (ROOT / "brain_out.txt").write_text(answer, encoding="utf-8")
        print(f"[brain] startup_s={ready-started:.3f} ttft_s={(first or finished)-ready:.3f} inference_s={finished-ready:.3f}", file=sys.stderr)
