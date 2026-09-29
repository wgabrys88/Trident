"""Stop the PE nvidia_worker listener on port 8765.

Allowed only for an owner or PM intentional cutover. Digs, scouts, and
cleanup must not run this. Without --cutover the script exits 2 and does
not stop anything.

--cutover stops python processes that are nvidia_worker.py and that own
the listen socket on port 8765. It does not stop gemma-brain.exe.
"""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOST = "0.0.0.0"
PORT = 8765
REFUSED = (
    "nvidia stop: refused. Stop is allowed only for an owner or PM intentional cutover. "
    "Pass --cutover. Digs, scouts, and cleanup must not stop a healthy :8765."
)


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


def process_rows():
    body = (
        "$ProgressPreference = 'SilentlyContinue'\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$procs = @(Get-CimInstance Win32_Process -Filter \"Name = 'python.exe' OR Name = 'pythonw.exe'\" | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine)\n"
        "$json = ConvertTo-Json -InputObject @($procs) -Compress -Depth 4\n"
        "if (-not $json) { $json = '[]' }\n"
        "[System.IO.File]::WriteAllText($out, $json, [System.Text.UTF8Encoding]::new($false))\n"
    )
    raw = run_ps(body).strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        die("cannot read process list: " + str(exc))
    if not data:
        return []
    if isinstance(data, dict):
        data = [data]
    rows = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            pid = int(item.get("ProcessId") or 0)
            parent = int(item.get("ParentProcessId") or 0)
        except (TypeError, ValueError):
            continue
        name = item.get("Name") or ""
        command = item.get("CommandLine") or ""
        if pid > 0:
            rows.append({"pid": pid, "parent": parent, "name": str(name), "command": str(command)})
    return rows


def is_worker(row):
    name = row["name"].lower()
    if name not in ("python.exe", "pythonw.exe"):
        return False
    command = row["command"].lower()
    if "nvidia_worker.py" not in command:
        return False
    if "gemma-brain" in command or "gemma.py" in command:
        return False
    return True


def worker_chain(listen_pid, by_pid):
    chain = []
    pid = listen_pid
    seen = set()
    while pid and pid not in seen:
        seen.add(pid)
        row = by_pid.get(pid)
        if row is None or not is_worker(row):
            break
        chain.append(pid)
        pid = row["parent"]
    return chain


def terminate(pid):
    completed = subprocess.run(
        ["taskkill", "/PID", str(pid), "/F"],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        text = (completed.stdout or b"").decode("utf-8", errors="replace").strip()
        print(
            "nvidia stop: taskkill " + str(pid) + " exit " + str(completed.returncode) + " " + text,
            file=sys.stderr,
        )
    return completed.returncode == 0


def wait_free():
    deadline = time.time() + 8
    while time.time() < deadline:
        if not listen_pids(PORT):
            return True
        time.sleep(0.25)
    return not listen_pids(PORT)


def main():
    if sys.argv[1:] in (["--help"], ["-h"]):
        print(__doc__.strip())
        return
    if sys.argv[1:] != ["--cutover"]:
        die(REFUSED)
    pids = listen_pids(PORT)
    if not pids:
        print("nvidia stop: " + HOST + ":" + str(PORT) + " is not listening", file=sys.stderr)
        return
    by_pid = {row["pid"]: row for row in process_rows()}
    ordered = []
    for listen_pid in pids:
        chain = worker_chain(listen_pid, by_pid)
        if not chain:
            die(
                "nvidia stop: refused. pid "
                + str(listen_pid)
                + " on :"
                + str(PORT)
                + " is not nvidia_worker.py; leaving it alone"
            )
        for pid in chain:
            if pid not in ordered:
                ordered.append(pid)
    for pid in ordered:
        row = by_pid[pid]
        if not is_worker(row):
            die("nvidia stop: refused. pid " + str(pid) + " is not nvidia_worker.py; leaving it alone")
        if pid < 10:
            die("nvidia stop: refused. pid " + str(pid))
    print(
        "nvidia stop: stopping nvidia_worker pid " + " ".join(str(pid) for pid in ordered),
        file=sys.stderr,
        flush=True,
    )
    for pid in ordered:
        terminate(pid)
    if not wait_free():
        die("nvidia stop: " + HOST + ":" + str(PORT) + " is still listening")
    print("nvidia stop: " + HOST + ":" + str(PORT) + " is free", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
