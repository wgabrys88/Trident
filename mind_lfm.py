import asyncio
import base64
import json
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL = ROOT / "artifacts" / "LFM2.5-VL-3B-Q4_K_M.gguf"
PROJECTOR = ROOT / "artifacts" / "mmproj-LFM2.5-VL-3B-Q8_0.gguf"
SERVER = ROOT / "artifacts" / "llama" / "llama-server.exe"
URL = "http://127.0.0.1:8080"
FETCH = {
    "LFM2.5-VL-3B-Q4_K_M.gguf": "https://huggingface.co/LiquidAI/LFM2.5-VL-3B-GGUF/resolve/main/LFM2.5-VL-3B-Q4_K_M.gguf",
    "mmproj-LFM2.5-VL-3B-Q8_0.gguf": "https://huggingface.co/LiquidAI/LFM2.5-VL-3B-GGUF/resolve/main/mmproj-LFM2.5-VL-3B-Q8_0.gguf",
}
ARCHIVES = (
    ("llama", "https://github.com/ggml-org/llama.cpp/releases/download/b11435/llama-b11435-bin-win-cuda-12.4-x64.zip"),
    ("llama", "https://github.com/ggml-org/llama.cpp/releases/download/b11435/cudart-llama-bin-win-cuda-12.4-x64.zip"),
)
NEED = (
    "artifacts/LFM2.5-VL-3B-Q4_K_M.gguf",
    "artifacts/mmproj-LFM2.5-VL-3B-Q8_0.gguf",
    "artifacts/llama/llama-server.exe",
    "artifacts/llama/cudart64_12.dll",
)

_proc = None
_drain = None
_err = bytearray()
_count = 0


def check():
    for path in (MODEL, PROJECTOR, SERVER):
        if not path.is_file():
            raise RuntimeError(f"LFM file is missing: {path.name}")


def argv():
    return [
        str(SERVER), "--model", str(MODEL), "--mmproj", str(PROJECTOR),
        "--mmproj-offload", "--mmproj-device", "CUDA0", "--image-max-tokens", "256",
        "--alias", "lfm", "--host", "127.0.0.1", "--port", "8080", "--jinja",
        "--ctx-size", "32768", "--device", "CUDA0", "--n-gpu-layers", "999",
        "--parallel", "1", "--flash-attn", "on", "--cache-type-k", "q8_0",
        "--cache-type-v", "q8_0", "--no-context-shift", "--no-webui", "--fit", "off",
    ]


def healthy():
    try:
        with urllib.request.urlopen(URL + "/health", timeout=2) as response:
            return response.status == 200
    except urllib.error.HTTPError as error:
        if error.code == 503:
            return False
        raise
    except urllib.error.URLError:
        return False


def complete(prompt, images):
    content = [{"type": "text", "text": prompt}]
    for path in images:
        raw = Path(path).read_bytes()
        kind = "png" if str(path).lower().endswith(".png") else "jpeg"
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/{kind};base64,{base64.b64encode(raw).decode()}"},
        })
    body = json.dumps({
        "model": "lfm", "temperature": 0.2, "top_k": 50, "max_tokens": 1200,
        "messages": [{"role": "user", "content": content if images else prompt}],
    }).encode()
    request = urllib.request.Request(
        URL + "/v1/chat/completions", data=body, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        data = json.loads(response.read().decode())
    text = data["choices"][0]["message"]["content"]
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("LFM returned no answer")
    return text


async def drain():
    while True:
        block = await _proc.stderr.read(4096)
        if not block:
            return
        _err.extend(block)
        del _err[:-4000]


async def ensure():
    global _proc, _drain
    if _proc is not None and _proc.returncode is None:
        return
    _proc = await asyncio.create_subprocess_exec(
        *argv(), cwd=str(SERVER.parent),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _drain = asyncio.create_task(drain())
    while _proc.returncode is None:
        if await asyncio.to_thread(healthy):
            return
        await asyncio.sleep(0.2)
    tail = bytes(_err).decode("utf-8", "replace").strip().splitlines()
    raise RuntimeError("LFM exited " + str(_proc.returncode) + (": " + tail[-1] if tail else ""))


def split(text):
    calls, rest = [], []
    for line in text.splitlines():
        if line.startswith("tool "):
            calls.append(line)
        else:
            rest.append(line)
    return calls, "\n".join(rest).strip()


def write_log(run_dir, prompt, images, answer):
    global _count
    record = {
        "args": argv(),
        "stdin": prompt,
        "images": [str(path) for path in images],
        "stream": [answer],
        "stderr": bytes(_err).decode("utf-8", "replace"),
        "exit": 0 if _proc is None else _proc.returncode,
    }
    path = Path(run_dir) / str(_count)
    _count += 1
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def turn(task, transcript, send, act, card, life, run_dir, images):
    await ensure()
    brief = (ROOT / "brief.txt").read_text(encoding="utf-8")
    prompt = brief + "\n" + card + f"\nLife folder: {life}\n\nTask:\n" + task + "\n\nTranscript:\n" + (transcript or "(empty)")
    shots = list(images)
    seen = []
    await send(task)
    while True:
        answer = await asyncio.to_thread(complete, prompt, shots)
        write_log(run_dir, prompt, shots, answer)
        calls, rest = split(answer)
        if not calls:
            return rest or answer, seen
        prompt += "\n" + answer
        for line in calls:
            await send(line)
            result = await asyncio.to_thread(act, line)
            await send(result)
            seen.append(line)
            if Path(result).is_file() and Path(result).suffix.lower() in {".png", ".jpg", ".jpeg"}:
                shots.append(result)
            prompt += "\n" + result
        count = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))["life"]["recent_images"]
        shots = shots[-int(count):] if int(count) > 0 else []


async def stop():
    global _proc, _drain
    if _proc is not None and _proc.returncode is None:
        _proc.kill()
        await _proc.wait()
    if _drain is not None:
        await _drain
        _drain = None
