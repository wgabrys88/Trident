"""Two agent steps overlapped, then a join.

seq_agents.py stays sequential. This skeleton starts both steps together.
Each concurrent step runs gemma-brain.exe in its own working directory so
the pair does not share gemma_run.txt or *_gemma_out_*.txt. If either step
fails, the same prompts run on threads with a lock around gemma.py.

No microphone and no playback. Writes parallel_agents.txt and parallel_agents.log.
Exits 0 when both joined steps return text.
"""

import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gemma

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "parallel_agents.txt"
LOG = ROOT / "parallel_agents.log"
RUN_DIR = ROOT / "parallel_run"
AGENTS = (
    (
        "summarize",
        "Summarize this sentence in one short sentence: The local assistant runs Gemma on an NVIDIA card and keeps the microphone unused.",
    ),
    (
        "keywords",
        "List exactly 3 keywords separated by commas and nothing else for this sentence: Parallel agents join their results after overlapping work.",
    ),
)
HELLO_LOCK = threading.Lock()


def die(message):
    log(message)
    raise SystemExit(2)


def configure_stdio_utf8():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def log(message):
    print(message, file=sys.stderr, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except OSError:
        pass


def venv_python():
    path = ROOT / ".venv" / "Scripts" / "python.exe"
    if not path.is_file():
        die("missing .venv python: " + str(path))
    return str(path)


def block(title, body):
    text = body if body.endswith("\n") or body == "" else body + "\n"
    return title + " <<\n" + text + "<<\n"


def gpu_line():
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.free,memory.total",
                "--format=csv,noheader,nounits",
            ],
            cwd=ROOT,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    lines = (completed.stdout or "").strip().splitlines()
    if not lines:
        return ""
    return lines[0].strip()


def gpu_used_mib(line):
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def tail_text(text, limit=12):
    lines = (text or "").splitlines()
    return "\n".join(lines[-limit:])


def spans_overlap(spans):
    if len(spans) < 2:
        return False
    (a0, a1), (b0, b1) = spans[0], spans[1]
    return a0 < b1 and b0 < a1


def results_ok(rows):
    return bool(rows) and all(row["code"] == 0 and (row["text"] or "").strip() for row in rows)


def run_brain(workdir, prompt, side_name):
    """One gemma-brain.exe.

    The sidecar stays in the repo root so gemma.model resolves next to gemma.txt.
    cwd is the agent directory, which is where *_gemma_out_*.txt is written.
    """
    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return 2, "", "cannot create " + str(workdir) + ": " + str(exc)
    side = ROOT / side_name
    err_path = workdir / "brain.err"
    try:
        payload = gemma.settings_text(prompt, "")
        side.write_bytes(payload.encode("utf-8"))
    except OSError as exc:
        return 2, "", "cannot write sidecar: " + str(exc)
    before = set(workdir.glob("*_gemma_out_*.txt"))
    try:
        err_handle = err_path.open("w", encoding="utf-8", errors="replace")
    except OSError as exc:
        try:
            side.unlink()
        except OSError:
            pass
        return 2, "", "cannot write stderr: " + str(exc)
    try:
        completed = subprocess.run(
            [str(ROOT / "gemma-brain.exe"), str(side)],
            cwd=workdir,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err_handle,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return 2, "", "gemma-brain timed out"
    except OSError as exc:
        return 2, "", "cannot run gemma-brain.exe: " + str(exc)
    finally:
        err_handle.close()
        try:
            side.unlink()
        except OSError:
            pass
    err = ""
    try:
        err = tail_text(err_path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        err = ""
    if completed.returncode != 0:
        return completed.returncode, "", err or ("gemma-brain exit " + str(completed.returncode))
    new_files = sorted(set(workdir.glob("*_gemma_out_*.txt")) - before)
    if not new_files:
        return 2, "", err or "gemma-brain wrote no output text"
    try:
        text = new_files[-1].read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return 2, "", "cannot read output: " + str(exc)
    return 0, text, err


def finish_tool(workdir, question, text, err, side_name):
    call = gemma.parse_tool_call(text)
    if not call:
        return 0, text, err
    if call[0] != "hello":
        log("parallel: tool skip " + call[0])
        return 0, text, err
    line = gemma.clean_line(call[1].get("line"))
    with HELLO_LOCK:
        gemma.write_hello(line)
    follow = gemma.follow_prompt(question, call[2], line)
    spoken = ""
    follow_err = err
    for _ in range(3):
        code, raw, ferr = run_brain(workdir, follow, side_name)
        if ferr:
            follow_err = ferr
        if code != 0:
            return code, "", follow_err
        spoken = gemma.answer_text(raw)
        if spoken:
            break
    if not spoken:
        log("parallel: tool follow-up empty")
        spoken = "Wrote " + line + " to tool_hello.txt."
    return 0, spoken, follow_err


def concurrent_agents(origin):
    peak = {"mib": None}
    stop = threading.Event()

    def poll():
        while not stop.is_set():
            used = gpu_used_mib(gpu_line())
            if used is not None and (peak["mib"] is None or used > peak["mib"]):
                peak["mib"] = used
            stop.wait(0.4)

    poller = threading.Thread(target=poll, daemon=True)
    poller.start()

    def work(index, name, question):
        log("parallel: concurrent " + name)
        born = time.perf_counter()
        workdir = RUN_DIR / name
        side_name = "parallel_" + name + "_run.txt"
        code, text, err = run_brain(workdir, gemma.gemma_prompt(question, False), side_name)
        if code == 0 and text.strip():
            code, text, err = finish_tool(workdir, question, text, err, side_name)
        if code != 0 and err:
            log("parallel: " + name + " err " + " | ".join(err.splitlines()[-4:]))
        done = time.perf_counter()
        log(
            "parallel: concurrent "
            + name
            + " exit "
            + str(code)
            + " "
            + str(int((done - born) * 1000))
            + " ms"
        )
        return {
            "index": index,
            "name": name,
            "question": question,
            "code": code,
            "text": text,
            "ms": int((done - born) * 1000),
            "err": err,
            "born": born - origin,
            "done": done - origin,
            "gemma": (born - origin, done - origin),
        }

    try:
        with ThreadPoolExecutor(max_workers=len(AGENTS)) as pool:
            futures = [
                pool.submit(work, index, name, question)
                for index, (name, question) in enumerate(AGENTS)
            ]
            rows = [future.result() for future in futures]
    finally:
        stop.set()
        poller.join(timeout=2)
    rows.sort(key=lambda row: row["index"])
    return rows, peak["mib"]


def run_gemma(py, name, question):
    log("parallel: gemma " + name)
    argv = [py, str(ROOT / "gemma.py"), "--", question]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        ms = int((time.perf_counter() - started) * 1000)
        return 2, "", ms, "gemma timed out"
    except OSError as exc:
        ms = int((time.perf_counter() - started) * 1000)
        return 2, "", ms, "cannot run gemma.py: " + str(exc)
    ms = int((time.perf_counter() - started) * 1000)
    err = (completed.stderr or "").strip()
    log(
        "parallel: gemma "
        + name
        + " exit "
        + str(completed.returncode)
        + " "
        + str(ms)
        + " ms"
    )
    return completed.returncode, completed.stdout or "", ms, err


def staggered_agents(py, origin):
    lock = threading.Lock()

    def work(index, name, question):
        born = time.perf_counter()
        with lock:
            held = time.perf_counter()
            code, text, ms, err = run_gemma(py, name, question)
            released = time.perf_counter()
        done = time.perf_counter()
        return {
            "index": index,
            "name": name,
            "question": question,
            "code": code,
            "text": text,
            "ms": ms,
            "err": err,
            "born": born - origin,
            "done": done - origin,
            "gemma": (held - origin, released - origin),
        }

    with ThreadPoolExecutor(max_workers=len(AGENTS)) as pool:
        futures = [
            pool.submit(work, index, name, question)
            for index, (name, question) in enumerate(AGENTS)
        ]
        rows = [future.result() for future in futures]
    rows.sort(key=lambda row: row["index"])
    return rows


def sequential_agents(py):
    rows = []
    wall_start = time.perf_counter()
    for name, question in AGENTS:
        code, text, ms, err = run_gemma(py, "seq-" + name, question)
        rows.append(
            {
                "name": name,
                "question": question,
                "code": code,
                "text": text,
                "ms": ms,
                "err": err,
            }
        )
    wall = int((time.perf_counter() - wall_start) * 1000)
    return rows, wall


def one_line(text):
    body = gemma.answer_text(text) or " ".join((text or "").split())
    if len(body) > 500:
        body = body[:500].rstrip()
    return body


def cleanup_run_dir():
    if RUN_DIR.exists():
        shutil.rmtree(RUN_DIR, ignore_errors=True)


def write_evidence(parts):
    payload = "".join(parts)
    try:
        EVIDENCE.write_text(payload, encoding="utf-8")
    except OSError as exc:
        die("cannot write parallel_agents.txt: " + str(exc))
    return payload


def agent_blocks(rows):
    parts = []
    for row in rows:
        parts.append("agent " + row["name"] + "\n")
        parts.append("exit " + str(row["code"]) + "\n")
        parts.append("ms " + str(row["ms"]) + "\n")
        parts.append(block("question", row["question"] + "\n"))
        parts.append(block("text", row["text"]))
        err = row.get("err") or ""
        if err:
            parts.append(block("stderr", tail_text(err, 20) + "\n"))
    return parts


def wall_ms(rows):
    return int((max(row["done"] for row in rows) - min(row["born"] for row in rows)) * 1000)


def main():
    configure_stdio_utf8()
    try:
        LOG.write_text("", encoding="utf-8")
    except OSError as exc:
        print("cannot write parallel_agents.log: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
    if not (ROOT / "gemma-brain.exe").is_file():
        die("missing gemma-brain.exe")
    py = venv_python()
    gpu = gpu_line()
    log("parallel: gpu " + (gpu or "unknown"))
    cleanup_run_dir()
    origin = time.perf_counter()
    log("parallel: concurrent start")
    try:
        rows, peak = concurrent_agents(origin)
    except OSError as exc:
        log("parallel: concurrent failed " + str(exc))
        rows, peak = [], None
    cleanup_run_dir()
    note = "isolated gemma-brain.exe"
    if peak is not None:
        note += " peak_used_mib " + str(peak)
        log("parallel: peak_used_mib " + str(peak))
    if results_ok(rows):
        mode = "concurrent"
        log("parallel: concurrent ok")
    else:
        mode = "staggered"
        why = " ".join(row["name"] + "=" + str(row["code"]) for row in rows) or "no rows"
        note += " fallback " + why
        log("parallel: staggered gemma.py (" + why + ")")
        rows = staggered_agents(py, time.perf_counter())
    joined_ok = results_ok(rows)
    parallel_wall = wall_ms(rows) if rows else 0
    thread_overlap = spans_overlap([(row["born"], row["done"]) for row in rows]) if rows else False
    gemma_overlap = spans_overlap([row["gemma"] for row in rows]) if rows else False
    seq_rows = []
    seq_wall = 0
    if joined_ok:
        log("parallel: sequential baseline")
        seq_rows, seq_wall = sequential_agents(py)
    parts = []
    parts.append("mode " + mode + "\n")
    parts.append("gpu " + (gpu or "unknown") + "\n")
    parts.append(block("note", note + "\n"))
    parts.append("thread_overlap " + ("yes" if thread_overlap else "no") + "\n")
    parts.append("gemma_overlap " + ("yes" if gemma_overlap else "no") + "\n")
    parts.append("parallel_wall_ms " + str(parallel_wall) + "\n")
    parts.extend(agent_blocks(rows))
    join = "\n".join(row["name"] + ": " + one_line(row["text"]) for row in rows) + "\n"
    parts.append(block("join", join))
    if seq_rows:
        parts.append("sequential_wall_ms " + str(seq_wall) + "\n")
        parts.append("sequential\n")
        parts.extend(agent_blocks(seq_rows))
    parts.append("result " + ("ok" if joined_ok else "fail") + "\n")
    payload = write_evidence(parts)
    sys.stdout.write(payload)
    log("parallel: wrote " + str(EVIDENCE))
    log(
        "parallel: mode "
        + mode
        + " result "
        + ("ok" if joined_ok else "fail")
        + " parallel_wall_ms "
        + str(parallel_wall)
        + " sequential_wall_ms "
        + str(seq_wall)
    )
    raise SystemExit(0 if joined_ok else 2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("parallel: stopped")
        raise SystemExit(0)
