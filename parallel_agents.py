"""Two agent steps overlapped, then a join.

seq_agents.py stays sequential. This skeleton starts both steps together.
Gemma is still one gemma-brain.exe per call, and gemma.py shares gemma_run.txt
plus *_gemma_out_*.txt. A short concurrent probe uses two sidecar files. If
that pair does not both return text, the same prompts run on threads with a
lock around gemma.py (staggered model calls).

No microphone and no playback. Writes parallel_agents.txt and parallel_agents.log.
Exits 0 when both joined steps return text.
"""

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
# Probe-only markers so two sidecar outputs can be told apart.
PROBE_MARK = {"summarize": "harbor", "keywords": "keel"}
PROBE_SECONDS = 90


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
    return (completed.stdout or "").strip().splitlines()[0].strip()


def gpu_used_mib(line):
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def tail_file(path, limit=6000):
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > limit:
                handle.seek(size - limit)
            data = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(data.splitlines()[-12:])


def snapshot_outs():
    return set(ROOT.glob("*_gemma_out_*.txt"))


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


def spans_overlap(spans):
    if len(spans) < 2:
        return False
    (a0, a1), (b0, b1) = spans[0], spans[1]
    return a0 < b1 and b0 < a1


def probe_question(name, question):
    mark = PROBE_MARK[name]
    return question + " Your reply must contain the word " + mark + "."


def probe_concurrent():
    """Two gemma-brain.exe processes, distinct sidecars. Does not call gemma.py."""
    exe = ROOT / "gemma-brain.exe"
    if not exe.is_file():
        return {"ok": False, "note": "missing gemma-brain.exe", "peak": None, "spans": []}
    before = snapshot_outs()
    started = []
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
    note = "concurrent probe"
    texts = {}
    try:
        for name, question in AGENTS:
            side = ROOT / ("parallel_probe_" + name + ".txt")
            err_path = ROOT / ("parallel_probe_" + name + ".err")
            payload = gemma.settings_text(gemma.gemma_prompt(probe_question(name, question), False), "")
            payload += "gemma.n-predict 48\n"
            side.write_bytes(payload.encode("utf-8"))
            err_handle = err_path.open("w", encoding="utf-8", errors="replace")
            row = {
                "name": name,
                "proc": None,
                "err_handle": err_handle,
                "err_path": err_path,
                "side": side,
                "born": time.perf_counter(),
                "done": None,
            }
            started.append(row)
            row["proc"] = subprocess.Popen(
                [".\\gemma-brain.exe", side.name],
                cwd=ROOT,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=err_handle,
            )
        deadline = time.perf_counter() + PROBE_SECONDS
        for row in started:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                row["proc"].kill()
                row["proc"].wait(timeout=15)
            else:
                try:
                    row["proc"].wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    row["proc"].kill()
                    row["proc"].wait(timeout=15)
            row["done"] = time.perf_counter()
            row["err_handle"].close()
        spans = [(row["born"], row["done"]) for row in started]
        codes = []
        tails = []
        for row in started:
            code = row["proc"].returncode
            codes.append(row["name"] + "=" + str(code))
            tail = tail_file(row["err_path"])
            if tail:
                tails.append(row["name"] + " stderr <<\n" + tail + "\n<<")
            log(
                "parallel: probe "
                + row["name"]
                + " exit "
                + str(code)
                + " "
                + str(int((row["done"] - row["born"]) * 1000))
                + " ms"
            )
        new_files = sorted(snapshot_outs() - before, key=lambda path: path.name)
        fresh = []
        for path in new_files:
            try:
                fresh.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                fresh.append("")
        assigned = {}
        for text in fresh:
            hits = [name for name, mark in PROBE_MARK.items() if mark in text.lower()]
            if len(hits) == 1 and hits[0] not in assigned and text.strip():
                assigned[hits[0]] = text
        both_codes = all(row["proc"].returncode == 0 for row in started)
        overlap = spans_overlap(spans)
        attributed = set(assigned) == {name for name, _question in AGENTS}
        note = "codes " + " ".join(codes) + " overlap " + ("yes" if overlap else "no")
        if peak["mib"] is not None:
            note += " peak_used_mib " + str(peak["mib"])
        if tails:
            note += "\n" + "\n".join(tails)
        ok = both_codes and overlap and attributed
        if ok:
            texts = assigned
        elif both_codes and overlap and not attributed:
            note += "\nunattributed outputs " + str(len(fresh))
        return {"ok": ok, "note": note, "peak": peak["mib"], "spans": spans, "texts": texts}
    except OSError as exc:
        return {"ok": False, "note": "probe failed: " + str(exc), "peak": peak["mib"], "spans": [], "texts": {}}
    finally:
        stop.set()
        poller.join(timeout=2)
        for row in started:
            proc = row.get("proc")
            if proc is not None and proc.poll() is None:
                proc.kill()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    pass
            handle = row.get("err_handle")
            if handle is not None and not handle.closed:
                handle.close()
            for key in ("side", "err_path"):
                path = row.get(key)
                if path is not None:
                    try:
                        path.unlink()
                    except OSError:
                        pass


def staggered(py, origin):
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
        results = [future.result() for future in futures]
    results.sort(key=lambda row: row["index"])
    return results


def sequential(py):
    rows = []
    wall_start = time.perf_counter()
    for name, question in AGENTS:
        code, text, ms, err = run_gemma(py, "seq-" + name, question)
        rows.append({"name": name, "code": code, "text": text, "ms": ms, "err": err})
    wall = int((time.perf_counter() - wall_start) * 1000)
    return rows, wall


def one_line(text):
    body = " ".join((text or "").split())
    if len(body) > 500:
        body = body[:500].rstrip()
    return body


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
            tail = "\n".join(err.splitlines()[-20:])
            parts.append(block("stderr", tail + "\n"))
    return parts


def main():
    configure_stdio_utf8()
    try:
        LOG.write_text("", encoding="utf-8")
    except OSError as exc:
        print("cannot write parallel_agents.log: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
    py = venv_python()
    gpu = gpu_line()
    log("parallel: gpu " + (gpu or "unknown"))
    log("parallel: probe start")
    origin = time.perf_counter()
    probe = probe_concurrent()
    log("parallel: probe ok " + ("yes" if probe["ok"] else "no"))
    mode = "concurrent" if probe["ok"] else "staggered"
    if probe["ok"]:
        rows = []
        for index, (name, question) in enumerate(AGENTS):
            text = probe["texts"][name]
            span = probe["spans"][index]
            rows.append(
                {
                    "index": index,
                    "name": name,
                    "question": probe_question(name, question),
                    "code": 0,
                    "text": text,
                    "ms": int((span[1] - span[0]) * 1000),
                    "err": "",
                    "born": span[0] - origin,
                    "done": span[1] - origin,
                    "gemma": (span[0] - origin, span[1] - origin),
                }
            )
        parallel_wall = int((max(row["done"] for row in rows) - min(row["born"] for row in rows)) * 1000)
    else:
        log("parallel: staggered gemma.py")
        stagger_origin = time.perf_counter()
        rows = staggered(py, stagger_origin)
        parallel_wall = int((max(row["done"] for row in rows) - min(row["born"] for row in rows)) * 1000)
    joined_ok = all(row["code"] == 0 and row["text"].strip() for row in rows)
    thread_overlap = spans_overlap([(row["born"], row["done"]) for row in rows])
    gemma_overlap = spans_overlap([row["gemma"] for row in rows])
    seq_rows = []
    seq_wall = 0
    if joined_ok:
        log("parallel: sequential baseline")
        seq_rows, seq_wall = sequential(py)
    parts = []
    parts.append("mode " + mode + "\n")
    parts.append("gpu " + (gpu or "unknown") + "\n")
    parts.append(block("probe", (probe.get("note") or "") + "\n"))
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
    log("parallel: mode " + mode + " result " + ("ok" if joined_ok else "fail"))
    raise SystemExit(0 if joined_ok else 2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("parallel: stopped")
        raise SystemExit(0)
