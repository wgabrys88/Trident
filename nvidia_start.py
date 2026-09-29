"""Start the PE nvidia_worker on 0.0.0.0:8765.

Runs `.venv\\Scripts\\python.exe nvidia_worker.py --host 0.0.0.0 --port 8765`
only when port 8765 is free. If that port is already listening, this script
exits 2 and does not spawn. It does not stop a listener.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOST = "0.0.0.0"
PORT = 8765
LOG = ROOT / "nvidia_worker.run.err"


def die(message):
    print(message, file=sys.stderr)
    raise SystemExit(2)


def run_ps(body):
    script_path = None
    out_path = None
    completed = None
    try:
        script_file = tempfile.NamedTemporaryFile(prefix="trident-pe-", suffix=".ps1", delete=False)
        script_file.close()
        out_file = tempfile.NamedTemporaryFile(prefix="trident-pe-", suffix=".out", delete=False)
        out_file.close()
        script_path = Path(script_file.name)
        out_path = Path(out_file.name)
        out_ps = str(out_path).replace("'", "''")
        script_path.write_text("$out = '" + out_ps + "'\n" + body, encoding="utf-8")
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-File", str(script_path)],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        raw = out_path.read_text(encoding="utf-8")
    except subprocess.TimeoutExpired:
        die("cannot query port " + str(PORT) + ": timed out")
    except OSError as exc:
        die("cannot query port " + str(PORT) + ": " + str(exc))
    finally:
        for path in (script_path, out_path):
            if path is None:
                continue
            try:
                path.unlink()
            except OSError:
                pass
    if completed.returncode != 0:
        err = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
        if err:
            die("cannot query port " + str(PORT) + ": " + err)
        die("cannot query port " + str(PORT))
    return raw


def listen_pids(port):
    body = (
        "$ProgressPreference = 'SilentlyContinue'\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$ids = @(Get-NetTCPConnection -LocalPort " + str(int(port)) + " -State Listen "
        "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)\n"
        "if ($ids.Count -eq 0) { $text = '' } else { "
        "$text = ($ids | ForEach-Object { $_.ToString() }) -join \"`n\" }\n"
        "[System.IO.File]::WriteAllText($out, $text, [System.Text.UTF8Encoding]::new($false))\n"
    )
    raw = run_ps(body)
    pids = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid = int(line)
        except ValueError:
            die("cannot query port " + str(port))
        if pid > 0:
            pids.append(pid)
    return pids


def accepts(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.4)
    try:
        sock.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def port_busy(port):
    if listen_pids(port):
        return True
    return accepts(port)


def venv_python():
    path = ROOT / ".venv" / "Scripts" / "python.exe"
    if not path.is_file():
        die("missing .venv python: " + str(path))
    return str(path)


def log_tail():
    try:
        data = LOG.read_bytes()
    except OSError:
        return ""
    lines = data.decode("utf-8", errors="replace").splitlines()
    return "\n".join(lines[-20:])


def spawn(py):
    try:
        log = open(LOG, "ab", buffering=0)
    except OSError as exc:
        die("cannot write " + str(LOG) + ": " + str(exc))
    argv = [py, str(ROOT / "nvidia_worker.py"), "--host", HOST, "--port", str(PORT)]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
    try:
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(ROOT),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=flags | breakaway,
                close_fds=True,
            )
        except OSError:
            proc = subprocess.Popen(
                argv,
                cwd=str(ROOT),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=flags,
                close_fds=True,
            )
    except OSError as exc:
        log.close()
        die("cannot start nvidia_worker.py: " + str(exc))
    log.close()
    return proc


def wait_bound(proc):
    deadline = time.time() + 20
    seen = 0
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        if accepts(PORT):
            seen += 1
            if seen >= 2:
                return True
        else:
            seen = 0
        time.sleep(0.25)
    return proc.poll() is None and accepts(PORT)


def main():
    if sys.argv[1:] in (["--help"], ["-h"]):
        print(__doc__.strip())
        return
    if sys.argv[1:]:
        die("nvidia start: refused. No arguments. This script does not stop a listener.")
    if port_busy(PORT):
        pids = listen_pids(PORT)
        detail = ""
        if pids:
            detail = " (pid " + " ".join(str(pid) for pid in pids) + ")"
        die(
            "nvidia start: refused. "
            + HOST
            + ":"
            + str(PORT)
            + " is already listening"
            + detail
            + "; leaving it alone"
        )
    proc = spawn(venv_python())
    if not wait_bound(proc):
        tail = log_tail()
        if listen_pids(PORT) or accepts(PORT):
            die("nvidia start: refused. Port became busy; left the existing listener\n" + tail)
        extra = ("\n" + tail) if tail else ""
        die("nvidia start: did not bind " + HOST + ":" + str(PORT) + extra)
    pids = listen_pids(PORT)
    shown = " ".join(str(pid) for pid in pids) if pids else str(proc.pid)
    print(
        "nvidia start: listening http://" + HOST + ":" + str(PORT) + "/ pid " + shown,
        file=sys.stderr,
        flush=True,
    )


if __name__ == "__main__":
    main()
