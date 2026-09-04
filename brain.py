import argparse
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from collections import deque

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
HOST = "127.0.0.1"
PORT = 17932
ALIAS = "gemma"
DEVICE = "Vulkan0"
GPU_LAYERS = "all"
CONTEXT = 4096
BATCH = 2048
UBATCH = 512
THREADS = 2
THREADS_BATCH = 2
POLL = 0
POLL_BATCH = 0
THREADS_HTTP = 1
PARALLEL = 1
FLASH_ATTN = "off"
REASONING = "off"
STARTUP_TIMEOUT = 180.0
REQUEST_TIMEOUT = 3600.0
CACHE_PROMPT = True
TEMPERATURE = 0.2
TOP_P = 0.95
TOP_K = 64
MIN_P = 0.0
REPEAT_PENALTY = 1.0
SEED = 42
MAX_TOKENS = 1024
SYSTEM_PROMPT = (
    "Produce the spoken reply to the user. Answer directly and correctly. Output only natural speech with short sentences. "
    "Do not use markdown, lists, code, URLs, emoji, stage directions, meta-commentary, or reasoning. "
    "Keep ordinary answers under sixty spoken words unless the request requires more. Expand numbers and abbreviations when useful for speech. "
    "Use the user's language."
)
SERVER_ARGS = (
    "--alias", ALIAS,
    "--host", HOST,
    "--port", str(PORT),
    "--offline",
    "--device", DEVICE,
    "--n-gpu-layers", GPU_LAYERS,
    "--ctx-size", str(CONTEXT),
    "--batch-size", str(BATCH),
    "--ubatch-size", str(UBATCH),
    "--threads", str(THREADS),
    "--threads-batch", str(THREADS_BATCH),
    "--poll", str(POLL),
    "--poll-batch", str(POLL_BATCH),
    "--threads-http", str(THREADS_HTTP),
    "--parallel", str(PARALLEL),
    "--flash-attn", FLASH_ATTN,
    "--no-mmproj",
    "--no-ui",
    "--reasoning", REASONING,
)
# SERVER_ARGS += ("--cache-type-k", "q8_0", "--cache-type-v", "q8_0")
# SERVER_ARGS += ("--cache-type-k", "q4_0", "--cache-type-v", "q4_0")
# SERVER_ARGS += ("--no-kv-offload",)
# SERVER_ARGS += ("--mlock",)
# SERVER_ARGS += ("--no-mmap",)
# SERVER_ARGS += ("--numa", "isolate")
# SERVER_ARGS += ("--prio", "1", "--prio-batch", "1")
# SERVER_ARGS += ("--cpu-range", "0-2", "--cpu-range-batch", "0-2")
# SERVER_ARGS += ("--cpu-strict", "1", "--cpu-strict-batch", "1")
# SERVER_ARGS += ("--split-mode", "none", "--main-gpu", "0")
# SERVER_ARGS += ("--tensor-split", "1")
# SERVER_ARGS += ("--no-repack",)
# SERVER_ARGS += ("--no-host",)
# SERVER_ARGS += ("--swa-full",)
# SERVER_ARGS += ("--rope-scaling", "none")
# SERVER_ARGS += ("--rope-scale", "1")
# SERVER_ARGS += ("--rope-freq-base", "10000")
# SERVER_ARGS += ("--rope-freq-scale", "1")
# SERVER_ARGS += ("--yarn-orig-ctx", "4096")
# SERVER_ARGS += ("--yarn-ext-factor", "-1")
# SERVER_ARGS += ("--yarn-attn-factor", "-1")
# SERVER_ARGS += ("--yarn-beta-slow", "-1")
# SERVER_ARGS += ("--yarn-beta-fast", "-1")
# SERVER_ARGS += ("--metrics",)
# SERVER_ARGS += ("--slots",)
# SERVER_ARGS += ("--cache-prompt",)
# SERVER_ARGS += ("--log-disable",)
# SERVER_ARGS += ("--log-verbosity", "2")
# SERVER_ARGS += ("--override-tensor", "PATTERN=Vulkan0")
REQUEST_ARGS = {
    "temperature": TEMPERATURE,
    "top_p": TOP_P,
    "top_k": TOP_K,
    "min_p": MIN_P,
    "repeat_penalty": REPEAT_PENALTY,
    "seed": SEED,
    "max_tokens": MAX_TOKENS,
}
# REQUEST_ARGS["presence_penalty"] = 0.0
# REQUEST_ARGS["frequency_penalty"] = 0.0
# REQUEST_ARGS["stop"] = ["<|end|>"]
# REQUEST_ARGS["n_probs"] = 0
# REQUEST_ARGS["min_keep"] = 0
_READY = "llama_server: listening on http://127.0.0.1:"


def _sha(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _download(url: str, path: Path, size: int = 0, sha: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.unlink(missing_ok=True)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "TridentBrain/1"})
        with urllib.request.urlopen(request, timeout=3600) as source, partial.open("wb") as target:
            shutil.copyfileobj(source, target, 4 << 20)
        if size and partial.stat().st_size != size:
            raise RuntimeError(f"Download size mismatch: {path.name}")
        if sha and _sha(partial) != sha:
            raise RuntimeError(f"Download checksum mismatch: {path.name}")
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


class BrainInstaller:
    def install(self) -> None:
        runtime_ok = EXE.is_file() and RUNTIME_REVISION.is_file() and RUNTIME_REVISION.read_text(encoding="utf-8").strip() == f"{LLAMA_REV} {RUNTIME_SHA}"
        model_ok = MODEL.is_file() and MODEL.stat().st_size == MODEL_SIZE and _sha(MODEL) == MODEL_SHA
        if MODEL.exists() and not model_ok:
            raise RuntimeError(f"Refusing unverified existing model: {MODEL}")
        with tempfile.TemporaryDirectory(prefix=".brain-install-", dir=ROOT) as temporary:
            work = Path(temporary)
            if not runtime_ok:
                archive = work / ARCHIVE
                _download(RUNTIME_URL, archive, sha=RUNTIME_SHA)
                if RUNTIME.exists():
                    shutil.rmtree(RUNTIME)
                RUNTIME.mkdir(parents=True)
                with zipfile.ZipFile(archive) as bundle:
                    members = [name for name in bundle.namelist() if Path(name).name == EXE.name]
                    if len(members) != 1:
                        raise RuntimeError(f"Expected one {EXE.name} in verified runtime archive")
                    parent = Path(members[0]).parent
                    files = [name for name in bundle.namelist() if name and not name.endswith("/") and Path(name).parent == parent]
                    for name in files:
                        with bundle.open(name) as source, (RUNTIME / Path(name).name).open("wb") as target:
                            shutil.copyfileobj(source, target)
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


class Brain:
    def __init__(self, system_prompt: str = SYSTEM_PROMPT) -> None:
        self.system_prompt = system_prompt
        self.process = None
        self.reader = None
        self.ready = threading.Event()
        self.tail = deque(maxlen=80)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.close()

    def _drain(self) -> None:
        for line in self.process.stdout:
            text = line.rstrip()
            self.tail.append(text)
            if _READY in text:
                self.ready.set()

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        if not EXE.is_file() or not RUNTIME_REVISION.is_file() or RUNTIME_REVISION.read_text(encoding="utf-8").strip() != f"{LLAMA_REV} {RUNTIME_SHA}":
            raise RuntimeError("Brain runtime missing; run python brain.py")
        if not MODEL.is_file() or MODEL.stat().st_size != MODEL_SIZE:
            raise RuntimeError("Gemma model missing; run python brain.py")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            if probe.connect_ex((HOST, PORT)) == 0:
                raise RuntimeError(f"Brain port {PORT} is already occupied")
        command = [str(EXE), "--model", str(MODEL), *SERVER_ARGS]
        self.process = subprocess.Popen(
            command,
            cwd=RUNTIME,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0,
        )
        self.reader = threading.Thread(target=self._drain, daemon=True)
        self.reader.start()
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while not self.ready.wait(0.05):
            if self.process.poll() is not None:
                raise RuntimeError(f"llama-server exited with {self.process.returncode}\n" + "\n".join(self.tail))
            if time.monotonic() >= deadline:
                self.close()
                raise TimeoutError("llama-server startup timed out\n" + "\n".join(self.tail))

    def _payload(self, request: str) -> dict:
        return {
            "model": ALIAS,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": request},
            ],
            "stream": True,
            "cache_prompt": CACHE_PROMPT,
            "chat_template_kwargs": {"enable_thinking": False},
            **REQUEST_ARGS,
        }

    def stream(self, request: str):
        request = request.strip()
        if not request:
            raise ValueError("request must not be empty")
        if self.process is None or self.process.poll() is not None:
            self.start()
        connection = http.client.HTTPConnection(HOST, PORT, timeout=REQUEST_TIMEOUT)
        try:
            body = json.dumps(self._payload(request), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            connection.request("POST", "/v1/chat/completions", body=body, headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError(f"Gemma HTTP {response.status}: {response.read().decode('utf-8', 'replace')[-2000:]}")
            while line := response.readline():
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
            connection.close()

    def ask(self, request: str) -> str:
        answer = "".join(self.stream(request)).replace("\r", "").strip()
        marker = "Assistant:\n"
        return answer.rsplit(marker, 1)[-1].strip() if marker in answer else answer

    def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        if process.poll() is None:
            process.kill()
        process.wait()
        if self.reader is not None:
            self.reader.join(timeout=5)
            self.reader = None
        if process.stdout is not None:
            process.stdout.close()
        self.ready.clear()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--request")
    args = parser.parse_args()
    if args.request is None:
        BrainInstaller().install()
    else:
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
