import hashlib
import json
import re
import subprocess
import sys
import traceback
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOLS, MODELS, DATA = (ROOT / n for n in ("tools", "models", "data"))
RUNTIMES, DOWNLOADS = TOOLS / "runtime", TOOLS / "downloads"

PARAKEET_VERSION = "0.5.0"
PARAKEET_ZIP_URL = f"https://github.com/mudler/parakeet.cpp/releases/download/v{PARAKEET_VERSION}/parakeet-v{PARAKEET_VERSION}-bin-win-cpu-x64.zip"
PARAKEET_ZIP_NAME = f"parakeet-v{PARAKEET_VERSION}-bin-win-cpu-x64.zip"
PARAKEET_ZIP_SHA = "df25af4095807d83957f6e135950120e7954fd2d4aca8ad0a5de248ada6287e0"
PARAKEET_EXE = "parakeet-cli.exe"
RUNTIME_DIR = RUNTIMES / "parakeet"

PARAKEET_FILE = "nemotron-3.5-asr-streaming-0.6b-q4_k.gguf"
PARAKEET_FILE_URL = "https://huggingface.co/mudler/parakeet-cpp-gguf/resolve/bf0af9f425fa01809cadec671b3cb672709d13e9/" + PARAKEET_FILE
PARAKEET_FILE_SHA = "5ad85eb3f3014c1a300d67b7ccbd23c38c4c952405cbe33a861e19fb2775e84b"
PARAKEET_FILE_SIZE = 718102624

_EOU_RE = re.compile(r" \[(EOU|EOB) @ (\d+\.\d+)s\]")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch(url: str, dest: Path, *, expected_sha: str = "", expected_size: int = 0) -> Path:
    if dest.is_file() and (not expected_size or dest.stat().st_size == expected_size) \
            and (not expected_sha or _sha256(dest) == expected_sha):
        return dest
    if dest.exists():
        raise RuntimeError(f"refusing to overwrite unverified artifact: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "Trident/1"})
    with urllib.request.urlopen(request, timeout=1800) as response, tmp.open("wb") as out:
        shutil = __import__("shutil")
        shutil.copyfileobj(response, out, 1 << 20)
    if expected_size and tmp.stat().st_size != expected_size:
        tmp.unlink()
        raise RuntimeError(f"size mismatch for {dest.name}: {tmp.stat().st_size} != {expected_size}")
    if expected_sha:
        got = _sha256(tmp)
        if got != expected_sha:
            tmp.unlink()
            raise RuntimeError(f"sha256 mismatch for {dest.name}")
    tmp.replace(dest)
    return dest


class Journal:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir, self.run_id = run_dir, run_dir.name
        self._events = (run_dir / "events.jsonl").open("a", encoding="utf-8", newline="\n", buffering=1)

    def emit(self, component: str, event: str, **fields) -> None:
        rec = {"schema_version": 2, "run_id": self.run_id,
               "wall_timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
               "component": component, "event": event, **fields}
        self._events.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")

    def transcript(self, role: str, text: str) -> None:
        if text:
            (self.run_dir / f"{role}.txt").open("a", encoding="utf-8").write(text.rstrip() + "\n")

    def failure(self, component: str, error: BaseException) -> None:
        self.emit(component, "failed", type=type(error).__name__, error=str(error))
        (self.run_dir / "failure.txt").open("a", encoding="utf-8").write(
            "".join(traceback.format_exception(type(error), error, error.__traceback__)))

    def close(self) -> None:
        if not self._events.closed:
            self._events.close()


def _new_run(command: str) -> Journal:
    run_dir = DATA / "runs" / f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-{command}"
    run_dir.mkdir(parents=True)
    print(f"trident.run {run_dir}", flush=True)
    return Journal(run_dir)


def install() -> int:
    if sys.platform != "win32":
        raise RuntimeError("Trident requires Windows")
    if sys.version_info < (3, 11):
        raise RuntimeError("Trident needs Python 3.11+")
    journal = _new_run("install")
    try:
        journal.emit("install", "start", model=PARAKEET_FILE, runtime=PARAKEET_ZIP_NAME)
        MODELS.mkdir(parents=True, exist_ok=True)
        RUNTIMES.mkdir(parents=True, exist_ok=True)
        DOWNLOADS.mkdir(parents=True, exist_ok=True)
        archive = _fetch(PARAKEET_ZIP_URL, DOWNLOADS / PARAKEET_ZIP_NAME, expected_sha=PARAKEET_ZIP_SHA)
        journal.emit("install", "archive.ready", name=archive.name, sha256=PARAKEET_ZIP_SHA, size=archive.stat().st_size)
        exe = RUNTIME_DIR / PARAKEET_EXE
        if not exe.is_file():
            if RUNTIME_DIR.exists():
                import shutil
                shutil.rmtree(RUNTIME_DIR)
            RUNTIME_DIR.mkdir(parents=True)
            with zipfile.ZipFile(archive) as z:
                z.extractall(RUNTIME_DIR)
            inner = next((p for p in RUNTIME_DIR.iterdir() if p.is_dir()), None)
            if inner is not None and not exe.is_file():
                import shutil
                for child in inner.iterdir():
                    shutil.move(str(child), str(RUNTIME_DIR / child.name))
                inner.rmdir()
            extra = RUNTIME_DIR / "parakeet-server.exe"
            if extra.is_file():
                extra.unlink()
        if not exe.is_file():
            raise RuntimeError(f"{PARAKEET_EXE} missing after extract")
        journal.emit("install", "runtime.ready", executable=str(exe))
        model = _fetch(PARAKEET_FILE_URL, MODELS / PARAKEET_FILE,
                       expected_sha=PARAKEET_FILE_SHA, expected_size=PARAKEET_FILE_SIZE)
        journal.emit("install", "model.ready", path=str(model), size=model.stat().st_size, sha256=PARAKEET_FILE_SHA)
        journal.emit("install", "completed")
        print("trident.done", flush=True)
        return 0
    except BaseException as error:
        journal.failure("install", error)
        print(f"trident.fail {type(error).__name__}: {error}", flush=True)
        return 1
    finally:
        journal.close()


def asr(wavs: list[Path], lang: str) -> int:
    if not wavs:
        print("usage: python main.py asr --wav FILE [--wav FILE ...] [--lang en]", file=sys.stderr)
        return 2
    for wav in wavs:
        if not wav.is_file():
            print(f"trident.fail missing: {wav}", file=sys.stderr)
            return 2
    journal = _new_run("asr")
    try:
        exe, model = RUNTIME_DIR / PARAKEET_EXE, MODELS / PARAKEET_FILE
        if not exe.is_file():
            raise RuntimeError(f"{PARAKEET_EXE} missing; run: python main.py install")
        if not model.is_file():
            raise RuntimeError(f"model missing: {model}; run: python main.py install")
        journal.emit("asr", "start", model=model.name, lang=lang, count=len(wavs), files=[str(w) for w in wavs])
        for index, wav in enumerate(wavs, 1):
            journal.emit("asr", "wav.start", utterance_id=index, wav=str(wav))
            args = [str(exe), "transcribe", "--model", str(model), "--input", str(wav),
                    "--stream", "--lang", lang]
            journal.emit("asr", "exec.start", command=" ".join(args))
            started = datetime.now()
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, encoding="utf-8", errors="replace")
            final_text, partial, body = "", [], []
            for raw in process.stdout:
                line = raw.rstrip("\n")
                if line.startswith("[stream:final] "):
                    final_text = line[len("[stream:final] "):]
                    continue
                if line.startswith("[stream] "):
                    body.append(line[len("[stream] "):])
                    continue
                body.append(line)
            exit_code = process.wait()
            elapsed = (datetime.now() - started).total_seconds()
            stream_body = "".join(body)
            eou_times = [float(m.group(2)) for m in _EOU_RE.finditer(stream_body) if m.group(1) == "EOU"]
            eob_times = [float(m.group(2)) for m in _EOU_RE.finditer(stream_body) if m.group(1) == "EOB"]
            text = final_text
            journal.emit("asr", "completed", utterance_id=index, exit_code=exit_code, text=text,
                         partial_chars=len(stream_body), eou_count=len(eou_times), eou_times_s=eou_times,
                         eob_count=len(eob_times), eob_times_s=eob_times, elapsed_s=round(elapsed, 3))
            if exit_code:
                stderr = process.stderr.read() if process.stderr else ""
                raise RuntimeError(f"parakeet-cli exit={exit_code}: {stderr.strip()[-400:]}")
            if text:
                journal.transcript("user", text)
                print(f"user: {text}", flush=True)
        journal.emit("asr", "summary", count=len(wavs))
        print("trident.done", flush=True)
        return 0
    except BaseException as error:
        journal.failure("asr", error)
        print(f"trident.fail {type(error).__name__}: {error}", flush=True)
        return 1
    finally:
        journal.close()
