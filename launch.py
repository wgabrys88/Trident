import hashlib
import json
import mmap
import os
import shutil
import subprocess
import sys
import time
import tomllib
import uuid
from pathlib import Path

import win32event
import win32job

ROOT = Path(os.environ.get("TRIDENT_ROOT", Path(__file__).resolve().parent.parent
            if Path(__file__).name == "resume.py" else Path(__file__).resolve().parent)).resolve()
os.environ["TRIDENT_ROOT"] = str(ROOT)
sys.path.insert(0, str(ROOT))

from models import bind_calls, decision, require_cursor, stop_if_capped
from store import ROOT as STORE_ROOT, read, save, write


def snapshot():
    output = subprocess.check_output(["git", "ls-files", "-z", "-c", "-o", "--exclude-standard"], cwd=ROOT)
    blobs = {}
    for name in output.split(b"\0"):
        if not name:
            continue
        relative = name.decode("utf-8")
        path = ROOT / relative
        blobs[relative] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return blobs


def restore(before):
    current = snapshot()
    for name in sorted(set(before) | set(current), key=lambda item: len(Path(item).parts), reverse=True):
        if before.get(name) == current.get(name):
            continue
        path = ROOT / name
        if name not in before or before[name] is None:
            if path.is_file():
                path.unlink()
            continue
        subprocess.check_call(["git", "checkout", "HEAD", "--", name], cwd=ROOT)
        if hashlib.sha256(path.read_bytes()).hexdigest() != before[name]:
            raise RuntimeError(f"Could not restore {name}")


def validate():
    for name in snapshot():
        path = ROOT / name
        if not path.is_file():
            continue
        if path.suffix == ".py":
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        elif path.suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
        elif path.suffix == ".toml":
            tomllib.loads(path.read_text(encoding="utf-8"))
    config = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    luna = config["luna"]
    if luna["model"] != "gpt-5.6-luna" or luna["reasoning"] != "none" or "screen" in config:
        raise ValueError("Luna must stay gpt-5.6-luna with reasoning none, and no screen watcher")


def repair(folder):
    import asyncio
    state = read(folder / "session.json")
    request = state["repair"]
    before = snapshot()
    instruction = (ROOT / "instructions.txt").read_text(encoding="utf-8") + (
        "\nThis is the repair visit. The live body is stopped. Edit general mechanisms in the source tree. "
        "Do not run Trident, do not commit, do not push, and do not switch branches. Preserve the saved life and task. "
        "Use agent mode and report what you changed."
    )
    failure = None
    result = None
    try:
        result = asyncio.run(decision(instruction, {
            "repair": request, "current_task": state["task"], "source": str(ROOT), "life": state["life"],
        }, ROOT))
    except Exception as error:
        failure = f"{type(error).__name__}: {error}"
    after = snapshot()
    changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
    if not failure and changed:
        try:
            validate()
        except Exception as error:
            failure = str(error)
    elif not failure:
        failure = "Repair made no tree change"
    restored = False
    if failure and changed:
        restore(before)
        validate()
        restored = True
    if failure:
        print(f"Repair failed: {failure}", file=sys.stderr, flush=True)
    state = read(folder / "session.json")
    state["repair"] = None
    state["attention"] = True
    state["waiting"] = False
    report = result[:2000] if isinstance(result, str) else None
    state.setdefault("history", []).append(
        {"repair": {"changed": changed, "error": failure, "restored": restored, "report": report}})
    state["history"] = state["history"][-40:]
    save(folder, state)
    stop_if_capped()


def main():
    if STORE_ROOT != ROOT:
        raise RuntimeError("Supervisor root does not match the source tree")
    require_cursor()
    os.environ["TRIDENT_CALLS"] = f"Local\\TridentCalls{os.getpid()}"
    bind_calls(mmap.mmap(-1, 4, tagname=os.environ["TRIDENT_CALLS"], access=mmap.ACCESS_WRITE))
    name = hashlib.sha256(str(ROOT).casefold().encode()).hexdigest()[:24]
    mutex = win32event.CreateMutex(None, False, "Local\\Trident-" + name)
    acquired = False
    try:
        acquired = win32event.WaitForSingleObject(mutex, 0) in (0, 128)
        if not acquired:
            raise RuntimeError("This Trident life already has a supervisor")
        current = ROOT / "runs" / "current.json"
        folder = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
        if folder is None:
            previous = read(current, {}).get("folder")
            if previous:
                candidate = Path(previous).resolve()
                if candidate.parent != ROOT / "runs":
                    raise ValueError("Invalid saved life path")
                state = read(candidate / "session.json", {})
                if not state.get("shutdown", False) or state.get("repair"):
                    folder = candidate
        folder = folder or ROOT / "runs" / uuid.uuid4().hex
        if folder.parent != ROOT / "runs":
            raise ValueError("Life must be a direct child of runs")
        folder.mkdir(parents=True, exist_ok=True)
        write(current, {"folder": str(folder)})
        resume = ROOT / "runs" / "resume.py"
        state = read(folder / "session.json", {})
        if not state.get("repair") and Path(__file__).resolve() != resume:
            shutil.copyfile(Path(__file__), resume)

        def run(script):
            job = win32job.CreateJobObject(None, "")
            process = None
            try:
                limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
                limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
                environment = {**os.environ, "TRIDENT_ROOT": str(ROOT), "TRIDENT_LAUNCH": str(folder),
                               "PYTHONDONTWRITEBYTECODE": "1"}
                environment.pop("TRIDENT_CONFIG", None)
                process = subprocess.Popen([sys.executable, "-B", str(script), str(folder)], cwd=ROOT,
                    env=environment, stdin=subprocess.PIPE)
                win32job.AssignProcessToJobObject(job, process._handle)
                process.stdin.write(b"start")
                process.stdin.close()
                return process.wait()
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait()
                win32job.TerminateJobObject(job, 1)
                deadline = time.monotonic() + 10
                while win32job.QueryInformationJobObject(job, win32job.JobObjectBasicAccountingInformation)["ActiveProcesses"]:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Child resources did not stop")
                    time.sleep(0.05)
                job.Close()

        while True:
            state = read(folder / "session.json", {})
            if state.get("repair"):
                repair(folder)
            code = run(ROOT / "trident.py")
            if code != 75:
                return code
    finally:
        if acquired:
            win32event.ReleaseMutex(mutex)
        mutex.Close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
