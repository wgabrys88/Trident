from __future__ import annotations
import argparse, http.client, json, shutil, subprocess, sys, tempfile, threading, time, zipfile
from collections import deque
from pathlib import Path

from main import ROOT, _download, _port_in_use, _kill_port, _drain, _wait_ready

RUNTIME = ROOT / "tools/runtime/brain"
EXE = RUNTIME / "llama-server.exe"
RUNTIME_REVISION = RUNTIME / "REVISION"
MODEL = ROOT / "models/gemma-4-E2B_q4_0-it.gguf"
MODEL_CARD = MODEL.with_suffix(".md")
LLAMA_REV = "b10816"
ARCHIVE = f"llama-{LLAMA_REV}-bin-win-vulkan-x64.zip"
RUNTIME_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_REV}/{ARCHIVE}"
RUNTIME_SHA = "ea6704bd058cb37c3d960913638b37b766f66fb5baff37547d0fa95aa0ed7528"
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
               "--reasoning", REASONING, "--lazy-mode", "auto"]
REQUEST_ARGS = {"temperature": TEMPERATURE, "top_p": TOP_P, "top_k": TOP_K, "min_p": MIN_P,
                "repeat_penalty": REPEAT_PENALTY, "seed": SEED, "max_tokens": MAX_TOKENS}
_READY = "llama_server: listening on http://127.0.0.1:"

_PROCESS, _READY_EVENT, _READER, _TAIL = None, None, None, deque(maxlen=80)


def _sha(path: Path) -> str:
    import hashlib
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _install() -> None:
    _kill_port(PORT)
    runtime_ok = EXE.is_file() and RUNTIME_REVISION.is_file() and RUNTIME_REVISION.read_text(encoding="utf-8").strip() == f"{LLAMA_REV} {RUNTIME_SHA}"
    model_ok = MODEL.is_file() and MODEL.stat().st_size == MODEL_SIZE and _sha(MODEL) == MODEL_SHA
    if MODEL.exists() and not model_ok:
        raise RuntimeError(f"Refusing unverified existing model: {MODEL}")
    with tempfile.TemporaryDirectory(prefix=".brain-install-", dir=ROOT) as tmp:
        work = Path(tmp)
        if not runtime_ok:
            archive = work / ARCHIVE
            _download(RUNTIME_URL, archive, RUNTIME_SHA)
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
            _download(MODEL_URL, downloaded, MODEL_SHA)
            MODEL.parent.mkdir(parents=True, exist_ok=True)
            downloaded.replace(MODEL)
        if not MODEL_CARD.is_file():
            _download(MODEL_CARD_URL, MODEL_CARD)


def _stop() -> None:
    global _PROCESS, _READY_EVENT, _READER
    proc, _PROCESS = _PROCESS, None
    if proc is not None:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    if _READER is not None:
        _READER.join(timeout=5)
        _READER = None
    if proc is not None and proc.stdout is not None:
        proc.stdout.close()
    if _READY_EVENT is not None:
        _READY_EVENT.clear()
    _kill_port(PORT)


def _start() -> None:
    global _PROCESS, _READY_EVENT, _READER
    if _PROCESS is not None and _PROCESS.poll() is None:
        return
    if _port_in_use(PORT):
        return
    _PROCESS = subprocess.Popen(
        [str(EXE), "--model", str(MODEL), *SERVER_ARGS],
        cwd=RUNTIME, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0)
    _READY_EVENT = threading.Event()
    _TAIL.clear()
    _READER = threading.Thread(target=_drain, args=(_PROCESS, _READY_EVENT, _READY, _TAIL), daemon=True)
    _READER.start()
    _wait_ready(_PROCESS, _READY_EVENT, _TAIL, STARTUP_TIMEOUT)


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
        _install()
        _start()
        try:
            input("[brain] ready. Press Enter to stop...\n")
        except EOFError:
            while True:
                time.sleep(3600)
        _stop()
        sys.exit(0)
    if args.unload:
        _stop()
        sys.exit(0)
    if args.request is not None:
        started = time.perf_counter()
        with Brain() as brain:
            ready = time.perf_counter()
            first = None
            chunks = []
            for chunk in brain.stream(args.request):
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
        print(answer)
        n_tokens = len(answer.split())
        inf_s = finished - ready
        tps = n_tokens / inf_s if inf_s > 0 else 0.0
        print(f"[brain] startup_s={ready-started:.3f} ttft_s={(first or finished)-ready:.3f} inference_s={inf_s:.3f} tokens={n_tokens} tps={tps:.2f}", file=sys.stderr)
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
        n_tokens = len(answer.split())
        inf_s = finished - ready
        tps = n_tokens / inf_s if inf_s > 0 else 0.0
        print(f"[brain] startup_s={ready-started:.3f} ttft_s={(first or finished)-ready:.3f} inference_s={inf_s:.3f} tokens={n_tokens} tps={tps:.2f}", file=sys.stderr)
