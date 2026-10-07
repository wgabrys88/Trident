import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import win32event
import win32job

ROOT = Path(os.environ.get("TRIDENT_ROOT", Path(__file__).resolve().parent.parent
            if Path(__file__).name == "resume.py" else Path(__file__).resolve().parent)).resolve()


def read(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def main():
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
        # A stable entry point survives a consultant deleting/changing launch.py.
        resume = ROOT / "runs" / "resume.py"
        state = read(folder / "session.json", {})
        if not state.get("repair") and Path(__file__).resolve() != resume:
            shutil.copyfile(Path(__file__), resume)

        def run(script, config=None):
            job = win32job.CreateJobObject(None, "")
            process = None
            try:
                limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
                limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
                environment = {**os.environ, "TRIDENT_ROOT": str(ROOT), "TRIDENT_LAUNCH": str(folder),
                               "PYTHONDONTWRITEBYTECODE": "1"}
                environment.pop("TRIDENT_CONFIG", None)
                if config is not None:
                    environment["TRIDENT_CONFIG"] = str(config)
                process = subprocess.Popen([sys.executable, "-B", str(script), str(folder)], cwd=ROOT,
                    env=environment, stdin=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
                win32job.AssignProcessToJobObject(job, process._handle)
                process.stdin.write(b"start")
                process.stdin.close()
                return process.wait()
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait()
                try:
                    win32job.TerminateJobObject(job, 1)
                    deadline = time.monotonic() + 10
                    while win32job.QueryInformationJobObject(job, win32job.JobObjectBasicAccountingInformation)["ActiveProcesses"]:
                        if time.monotonic() >= deadline:
                            raise RuntimeError("Child resources did not stop; repair refused")
                        time.sleep(0.05)
                finally:
                    job.Close()

        while True:
            state = read(folder / "session.json", {})
            if state.get("repair"):
                if not state.get("body_stopped"):
                    raise RuntimeError("Unconfirmed body cleanup; repair refused")
                request = state["repair"]
                coordinator = folder / request.setdefault("coordinator", "coordinator-" + uuid.uuid4().hex)
                if coordinator.parent != folder:
                    raise ValueError("Invalid coordinator path")
                if not coordinator.exists():
                    if request.get("phase") in ("editing", "auditing", "restoring"):
                        raise RuntimeError("Saved repair coordinator missing; refuse damaged source")
                    stage = coordinator.with_name(coordinator.name + ".preparing-" + uuid.uuid4().hex)
                    stage.mkdir()
                    for directory, directories, files in os.walk(ROOT):
                        directories[:] = [entry for entry in directories if entry not in (".git", "__pycache__")
                                          and (Path(directory) != ROOT or entry not in ("runs", "artifacts"))]
                        for entry in directories:
                            if (Path(directory) / entry).lstat().st_file_attributes & 1024:
                                raise RuntimeError("Coordinator directory alias refused")
                        for entry in files:
                            source = Path(directory) / entry
                            if source.lstat().st_file_attributes & 1024 or source.stat().st_nlink != 1:
                                raise RuntimeError("Coordinator source alias refused")
                            target = stage / source.relative_to(ROOT)
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(source, target)
                    stage.replace(coordinator)
                write(folder / "session.json", state)
                if run(coordinator / "repair.py", coordinator / "config.toml"):
                    raise RuntimeError("Repair stopped safely. Resume with runs/resume.py; audit remains in the saved life")
            code = run(ROOT / "trident.py")
            if code not in (75, 76):
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
