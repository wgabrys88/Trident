"""Mouth speak entry. Synthesize each TEXT with chatterbox.exe; play on Speakers with one-chunk overlap."""

import argparse
import concurrent.futures
import ctypes
import datetime as dt
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS = ("nano", "turbo", "v3")
DROP_KEYS = ("chatterbox.variant", "chatterbox.language", "chatterbox.play")
SND_FILENAME = 0x00020000
SND_NODEFAULT = 0x0002

# Opt-in diagnostic state (quiet unless --diag-log).
_DIAG = None  # DiagSession | None


class DiagSession:
    """Phase + GPU sampler logs under --diag-log DIR_OR_PATH."""

    def __init__(self, target: Path):
        if target.suffix.lower() in {".log", ".txt"}:
            self.dir = target.parent if str(target.parent) not in ("", ".") else Path.cwd()
            stem = target.stem
        else:
            self.dir = target
            stem = "mouth"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.phase_path = self.dir / f"{stem}_phase_diag.log"
        self.gpu_path = self.dir / f"{stem}_gpu_diag.log"
        self._lock = threading.Lock()
        self._t0_wall = time.time()
        self._t0_mono = time.perf_counter()
        self._gpu_proc = None
        # Truncate / start fresh for this run.
        self.phase_path.write_text("", encoding="utf-8")
        self.gpu_path.write_text("", encoding="utf-8")
        self.phase(
            "RUN_START",
            f"phase_log={self.phase_path}",
            f"gpu_log={self.gpu_path}",
            f"cwd={Path.cwd()}",
        )

    def _stamps(self):
        wall = dt.datetime.now().astimezone().isoformat(timespec="milliseconds")
        mono = time.perf_counter() - self._t0_mono
        return wall, mono

    def phase(self, event: str, *parts: str):
        wall, mono = self._stamps()
        extra = (" " + " ".join(parts)) if parts else ""
        line = f"{wall} mono={mono:.3f}s PHASE {event}{extra}\n"
        with self._lock:
            with self.phase_path.open("a", encoding="utf-8") as fh:
                fh.write(line)

    def start_gpu_sampler(self, interval_s: float = 0.5):
        """Background sampler: Iris Xe GPU Engine util (3D+Compute), every interval_s."""
        ps1 = self.dir / "_gpu_sampler.ps1"
        err = self.dir / "_gpu_sampler.err"
        # Only 3D/Compute — matches Task Manager “3D” load; refresh PIDs each sample.
        # Tabs are real \t characters in the log line.
        ps1.write_text(
            "\n".join(
                [
                    "param([string]$LogPath, [double]$IntervalMs)",
                    "$ErrorActionPreference = 'SilentlyContinue'",
                    "$alive = @{}",
                    "function Refresh-Counters {",
                    "  try {",
                    "    $cat = New-Object System.Diagnostics.PerformanceCounterCategory('GPU Engine')",
                    "    foreach ($n in $cat.GetInstanceNames()) {",
                    "      if ($n -notmatch 'engtype_3D' -and $n -notmatch 'engtype_Compute') { continue }",
                    "      if (-not $alive.ContainsKey($n)) {",
                    "        try {",
                    "          $c = New-Object System.Diagnostics.PerformanceCounter(",
                    "            'GPU Engine', 'Utilization Percentage', $n, $true)",
                    "          [void]$c.NextValue()",
                    "          $alive[$n] = $c",
                    "        } catch {}",
                    "      }",
                    "    }",
                    "  } catch {}",
                    "}",
                    "Refresh-Counters",
                    "$sw = [System.Diagnostics.Stopwatch]::StartNew()",
                    "while ($true) {",
                    "  Refresh-Counters",
                    "  $maxAll = 0.0; $max3d = 0.0; $maxCompute = 0.0; $n = 0",
                    "  $dead = @()",
                    "  foreach ($k in @($alive.Keys)) {",
                    "    try {",
                    "      $v = [double]$alive[$k].NextValue()",
                    "      $n++",
                    "      if ($v -gt $maxAll) { $maxAll = $v }",
                    "      if ($k -match 'engtype_3D') { if ($v -gt $max3d) { $max3d = $v } }",
                    "      if ($k -match 'engtype_Compute') { if ($v -gt $maxCompute) { $maxCompute = $v } }",
                    "    } catch { $dead += $k }",
                    "  }",
                    "  foreach ($k in $dead) { [void]$alive.Remove($k) }",
                    "  $iso = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fffzzz')",
                    "  $mono = ('{0:F3}' -f $sw.Elapsed.TotalSeconds)",
                    '  $line = "{0}`tmono={1}`tmax={2:F1}`t3d={3:F1}`tcompute={4:F1}`tinst={5}" -f '
                    "$iso, $mono, $maxAll, $max3d, $maxCompute, $n",
                    "  [System.IO.File]::AppendAllText($LogPath, $line + [Environment]::NewLine)",
                    "  Start-Sleep -Milliseconds $IntervalMs",
                    "}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        creation = 0
        if sys.platform == "win32":
            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        err_fh = open(err, "w", encoding="utf-8")
        self._gpu_err_fh = err_fh
        self._gpu_proc = subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ps1),
                str(self.gpu_path),
                str(int(interval_s * 1000)),
            ],
            cwd=str(self.dir),
            stdout=subprocess.DEVNULL,
            stderr=err_fh,
            creationflags=creation,
        )
        self.phase(
            "GPU_SAMPLER_START",
            f"interval_s={interval_s}",
            f"pid={self._gpu_proc.pid}",
            f"ps1={ps1.name}",
            "engines=3D+Compute",
        )

    def stop(self):
        self.phase("RUN_END")
        if self._gpu_proc is not None:
            try:
                self._gpu_proc.terminate()
                self._gpu_proc.wait(timeout=3)
            except Exception:
                try:
                    self._gpu_proc.kill()
                except Exception:
                    pass
            self.phase("GPU_SAMPLER_STOP", f"pid={self._gpu_proc.pid}")
            self._gpu_proc = None
        err_fh = getattr(self, "_gpu_err_fh", None)
        if err_fh is not None:
            try:
                err_fh.close()
            except Exception:
                pass
            self._gpu_err_fh = None


def die(message):
    print(message, file=sys.stderr)
    raise SystemExit(2)


def physical_lines(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def kept_lines(raw):
    lines = physical_lines(raw)
    if lines and lines[-1] == "":
        lines = lines[:-1]
    kept = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.lstrip(" \t")
        if not stripped or stripped.startswith("#"):
            kept.append(line)
            index += 1
            continue
        if len(stripped) >= 2 and stripped.endswith("<<"):
            key = stripped[:-2].rstrip(" \t")
            if key == "chatterbox.text":
                index += 1
                while index < len(lines) and lines[index] != "<<":
                    index += 1
                if index < len(lines):
                    index += 1
                continue
        key = stripped.split(" ", 1)[0]
        if key in DROP_KEYS or key == "chatterbox.text":
            index += 1
            continue
        kept.append(line)
        index += 1
    return kept


def settings_text(model, lang, sentence, play):
    source = ROOT / "chatterbox.txt"
    if not source.is_file():
        die("missing chatterbox.txt")
    raw = source.read_text(encoding="utf-8-sig")
    body = "\n".join(kept_lines(raw))
    if body:
        body += "\n"
    block = "\n".join(physical_lines(sentence))
    body += (
        "chatterbox.variant " + model + "\n"
        "chatterbox.language " + lang + "\n"
        "chatterbox.play " + play + "\n"
        "chatterbox.text <<\n"
        + block
        + "\n<<\n"
    )
    return body


def play_wav(path, chunk_index=None):
    path = Path(path).resolve()
    if not path.is_file():
        die("missing wav: " + str(path))
    if _DIAG is not None:
        _DIAG.phase(
            "PLAY_START",
            f"chunk={chunk_index}",
            f"wav={path.name}",
        )
    t0 = time.perf_counter()
    ok = ctypes.windll.winmm.PlaySoundW(str(path), None, SND_FILENAME | SND_NODEFAULT)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    if _DIAG is not None:
        _DIAG.phase(
            "PLAY_END",
            f"chunk={chunk_index}",
            f"wav={path.name}",
            f"play_ms={elapsed_ms:.1f}",
            f"ok={bool(ok)}",
        )
    if not ok:
        die("PlaySoundW failed: " + str(path))


def synthesize(model, lang, sentence, chunk_index=None):
    mouth = ROOT / "mouth.txt"
    payload = settings_text(model, lang, sentence, "off")
    try:
        mouth.write_bytes(payload.encode("utf-8"))
    except OSError as exc:
        die("cannot write mouth.txt: " + str(exc))
    before = set(ROOT.glob("*_chatterbox_out_*.txt"))
    if _DIAG is not None:
        _DIAG.phase(
            "SPAWN_START",
            f"chunk={chunk_index}",
            "exe=.\\chatterbox.exe",
            "args=mouth.txt",
            f"text_len={len(sentence)}",
        )
        _DIAG.phase(
            "SYNTH_START",
            f"chunk={chunk_index}",
            "note=cold_process_load_plus_synth",
        )
    t0 = time.perf_counter()
    try:
        # Per-chunk cold process: Popen so diag can log PID; wait = full teardown.
        proc = subprocess.Popen(
            [".\\chatterbox.exe", "mouth.txt"],
            cwd=ROOT,
            shell=False,
        )
    except OSError as exc:
        die("cannot run chatterbox.exe: " + str(exc))
    if _DIAG is not None:
        _DIAG.phase(
            "SPAWN_PID",
            f"chunk={chunk_index}",
            f"pid={proc.pid}",
            "note=fresh_chatterbox_exe",
        )
    returncode = proc.wait()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    still_alive = proc.poll() is None
    if _DIAG is not None:
        _DIAG.phase(
            "SPAWN_END",
            f"chunk={chunk_index}",
            f"pid={proc.pid}",
            f"returncode={returncode}",
            f"synth_ms={elapsed_ms:.1f}",
            f"poll_alive={still_alive}",
            "note=process_exit_unload",
        )
        _DIAG.phase(
            "SYNTH_END",
            f"chunk={chunk_index}",
            f"pid={proc.pid}",
            f"synth_ms={elapsed_ms:.1f}",
            f"returncode={returncode}",
        )
        _DIAG.phase(
            "UNLOAD",
            f"chunk={chunk_index}",
            f"pid={proc.pid}",
            f"poll_alive={still_alive}",
            "note=chatterbox.exe_exited_clean_teardown",
        )
    if returncode != 0:
        raise SystemExit(returncode)
    after = set(ROOT.glob("*_chatterbox_out_*.txt"))
    new_files = sorted(after - before)
    if not new_files:
        die("chatterbox wrote no output text")
    out_txt = new_files[-1]
    name = out_txt.read_text(encoding="utf-8").strip()
    if not name:
        die("empty chatterbox output text")
    wav = ROOT / name
    if not wav.is_file():
        die("missing wav named by chatterbox: " + name)
    if _DIAG is not None:
        _DIAG.phase(
            "WAV_READY",
            f"chunk={chunk_index}",
            f"wav={wav.name}",
            f"synth_ms={elapsed_ms:.1f}",
        )
    return wav


def main():
    global _DIAG
    parser = argparse.ArgumentParser(prog="mouth.py")
    parser.add_argument("text", nargs="*")
    parser.add_argument("--model", default="nano")
    parser.add_argument("--lang", default=None)
    parser.add_argument(
        "--diag-log",
        default=None,
        metavar="DIR_OR_PATH",
        help="opt-in phase+GPU diag logs under DIR (or PATH stem); default quiet",
    )
    args = parser.parse_args()
    if not args.text:
        die(
            "usage: mouth.py [--model nano|turbo|v3] [--lang TAG] "
            "[--diag-log DIR_OR_PATH] TEXT [TEXT ...]"
        )
    if args.model not in MODELS:
        die("unknown model")
    if args.lang is None:
        lang = "pl" if args.model == "v3" else "en"
    elif args.lang.strip() == "":
        die("empty language")
    else:
        lang = args.lang
    for sentence in args.text:
        if sentence.strip() == "":
            die("empty text")
        if any(line == "<<" for line in physical_lines(sentence)):
            die("text line is only <<")

    os.chdir(ROOT)
    if args.diag_log:
        target = Path(args.diag_log)
        if not target.is_absolute():
            target = (ROOT / target).resolve()
        _DIAG = DiagSession(target)
        _DIAG.start_gpu_sampler(0.5)
        # Brief settle so first GPU sample exists before chunk0 synth.
        time.sleep(0.6)

    chunks = list(args.text)
    try:
        if _DIAG is not None:
            _DIAG.phase("IDLE_BEFORE_CHUNK0_SYNTH", f"chunks={len(chunks)}")
        ready = synthesize(args.model, lang, chunks[0], chunk_index=0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            for index, sentence in enumerate(chunks):
                if _DIAG is not None:
                    _DIAG.phase(
                        "OVERLAP_LOOP",
                        f"play_chunk={index}",
                        f"next_synth_chunk={(index + 1) if index + 1 < len(chunks) else 'none'}",
                    )
                play_future = pool.submit(play_wav, ready, index)
                if index + 1 < len(chunks):
                    if _DIAG is not None:
                        _DIAG.phase(
                            "IDLE_END_START_NEXT_SYNTH",
                            f"while_playing_chunk={index}",
                            f"synth_chunk={index + 1}",
                        )
                    ready = synthesize(args.model, lang, chunks[index + 1], chunk_index=index + 1)
                    if _DIAG is not None:
                        if play_future.done():
                            _DIAG.phase(
                                "WAIT_NONE_PLAY_ALREADY_DONE",
                                f"after_synth_chunk={index + 1}",
                                f"play_chunk={index}",
                            )
                        else:
                            _DIAG.phase(
                                "WAIT_PLAY_AFTER_SYNTH",
                                f"after_synth_chunk={index + 1}",
                                f"still_playing_chunk={index}",
                                "note=synth_finished_early_gpu_may_idle",
                            )
                play_t0 = time.perf_counter()
                play_future.result()
                if _DIAG is not None and index + 1 < len(chunks):
                    waited_ms = (time.perf_counter() - play_t0) * 1000.0
                    _DIAG.phase(
                        "PLAY_JOIN",
                        f"play_chunk={index}",
                        f"join_wait_ms={waited_ms:.1f}",
                    )
    finally:
        if _DIAG is not None:
            _DIAG.stop()
            _DIAG = None
    raise SystemExit(0)


if __name__ == "__main__":
    main()
