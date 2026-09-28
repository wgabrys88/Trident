import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "seq_agents.txt"
QUESTIONS = (
    "Reply with one short sentence that contains the word alpha.",
    "Reply with one short sentence that contains the word beta.",
)


def die(message):
    print(message, file=sys.stderr)
    raise SystemExit(2)


def configure_stdio_utf8():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def venv_python():
    path = ROOT / ".venv" / "Scripts" / "python.exe"
    if not path.is_file():
        die("missing .venv python: " + str(path))
    return str(path)


def block(title, body):
    text = body if body.endswith("\n") or body == "" else body + "\n"
    return title + " <<\n" + text + "<<\n"


def write_evidence(parts):
    payload = "".join(parts)
    try:
        EVIDENCE.write_text(payload, encoding="utf-8")
    except OSError as exc:
        die("cannot write seq_agents.txt: " + str(exc))


def run_step(py, number, question):
    print("seq: step " + str(number), file=sys.stderr, flush=True)
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
    print(
        "seq: step " + str(number) + " exit " + str(completed.returncode) + " " + str(ms) + " ms",
        file=sys.stderr,
        flush=True,
    )
    return completed.returncode, completed.stdout or "", ms, err


def main():
    configure_stdio_utf8()
    py = venv_python()
    parts = []
    for number, question in enumerate(QUESTIONS, start=1):
        code, text, ms, err = run_step(py, number, question)
        parts.append("step " + str(number) + "\n")
        parts.append("exit " + str(code) + "\n")
        parts.append("ms " + str(ms) + "\n")
        parts.append(block("question", question + "\n"))
        parts.append(block("text", text))
        if err:
            tail = "\n".join(err.splitlines()[-20:])
            parts.append(block("stderr", tail + "\n"))
        if code != 0 or not text.strip():
            parts.append("result fail\n")
            write_evidence(parts)
            print("seq: fail step " + str(number), file=sys.stderr)
            raise SystemExit(code if code != 0 else 2)
    parts.append("result ok\n")
    write_evidence(parts)
    sys.stdout.write(EVIDENCE.read_text(encoding="utf-8"))
    print("seq: wrote " + str(EVIDENCE), file=sys.stderr)
    raise SystemExit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("seq: stopped", file=sys.stderr)
        raise SystemExit(0)
