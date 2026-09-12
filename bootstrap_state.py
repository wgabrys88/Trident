"""Atemporal machine+git derive. Stderr discarded. Stdout only. Exit 1 on BAD."""
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
DEVNULL = subprocess.DEVNULL
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from tts_common import CHATTERBOX, CMAKE, GGML_REV, MODELS, REF, VULKAN, paths, running
from tts_nano import CFG as NANO
from tts_turbo import CFG as TURBO
from tts_v3 import CFG as V3

FAMILIES = (NANO, TURBO, V3)
TRIDENT = ROOT
FAILED = 0


def die(msg):
    global FAILED
    FAILED += 1
    print(f"BAD {msg}")


def ok(msg):
    print(f"OK  {msg}")


def mis(msg):
    print(f"MIS {msg}")


def git(repo, *args):
    p = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=DEVNULL,
        text=True,
        timeout=60,
    )
    if p.returncode != 0:
        die(f"git {' '.join(args)} repo={repo} code={p.returncode}")
        return ""
    return (p.stdout or "").rstrip("\n")


def ls_remote(url, refs, repo=None):
    cmd = ["git"]
    if repo:
        cmd += ["-C", str(repo)]
    cmd += ["ls-remote", url, *refs]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=DEVNULL, text=True, timeout=60)
    if p.returncode != 0:
        die(f"ls-remote {url} code={p.returncode}")
        return {}
    out = {}
    for raw in p.stdout.splitlines():
        sha, ref = raw.split("\t", 1)
        out[ref.rsplit("/", 1)[-1]] = sha
    return out


def local_ref(repo, name):
    p = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "-q", f"refs/heads/{name}"],
        stdout=subprocess.PIPE,
        stderr=DEVNULL,
        text=True,
        timeout=30,
    )
    return (p.stdout or "").strip() if p.returncode == 0 else ""


def porcelain(repo):
    return [ln for ln in git(repo, "status", "--porcelain=v1", "-uno").splitlines() if ln]


def dump_keys(path: Path):
    if not path.is_file():
        return ""
    got = {}
    for raw in path.read_text(encoding="ascii", errors="replace").splitlines():
        for k in ("predicted_count", "dropped_count", "eos"):
            if raw.startswith(k + " "):
                got[k] = raw.split(" ", 1)[1]
    return " ".join(f"{k}={v}" for k, v in got.items())


def csv_stats(path: Path):
    if not path.is_file():
        return ""
    n = sil = 0
    with path.open(encoding="ascii", errors="replace") as f:
        next(f, None)
        for raw in f:
            n += 1
            cols = raw.split(",")
            if len(cols) > 1 and cols[1].strip() == "4299":
                sil += 1
    return f"steps={n} chosen4299={sil}"


def need_file(tag, path: Path):
    if path.is_file():
        ok(f"{tag} bytes={path.stat().st_size}")
        return
    if path.is_dir():
        ok(f"{tag} dir")
        return
    die(f"{tag} missing {path}")


def git_repo(repo, want_branch, remote_heads, name):
    if not Path(repo).is_dir():
        die(f"{name} missing {repo}")
        return "", ""
    branch = git(repo, "branch", "--show-current")
    head = git(repo, "rev-parse", "HEAD")
    if want_branch and branch != want_branch:
        die(f"{name}_branch {branch} want={want_branch}")
    else:
        ok(f"{name}_branch {branch} head={head}")
    dirty = porcelain(repo)
    if dirty:
        die(f"{name}_dirty {len(dirty)} " + " | ".join(dirty[:8]))
    else:
        ok(f"{name}_clean")
    if want_branch:
        tip = remote_heads.get(want_branch, "")
        if not tip:
            die(f"{name}_remote {want_branch} unreachable")
        elif head != tip:
            die(f"{name}_unpushed local={head} origin/{want_branch}={tip}")
        else:
            ok(f"{name}_pushed {want_branch}={head}")
    return branch, head


def main():
    if not CHATTERBOX.is_dir():
        die(f"chatterbox_dir {CHATTERBOX}")
        raise SystemExit(1)

    t_heads = ls_remote("origin", ["refs/heads/v3", "refs/heads/main"], TRIDENT)
    c_heads = ls_remote("origin", ["refs/heads/nano", "refs/heads/turbo", "refs/heads/v3", "refs/heads/main"], CHATTERBOX)
    tracked = [n for n in git(TRIDENT, "ls-tree", "-r", "--name-only", "v3").splitlines() if n]
    ok(f"trident_tracked n={len(tracked)} " + " ".join(tracked))
    git_repo(TRIDENT, "v3", t_heads, "trident")
    if t_heads.get("main"):
        ok(f"trident_main {t_heads['main']}")
    cb_now = git(CHATTERBOX, "branch", "--show-current")
    git_repo(CHATTERBOX, cb_now if cb_now in {f.branch for f in FAMILIES} else "", c_heads, "chatterbox")
    if c_heads.get("main"):
        ok(f"chatterbox_main {c_heads['main']}")
    if cb_now == "main":
        die("chatterbox on frozen main")

    for fam in FAMILIES:
        remote = c_heads.get(fam.branch, "")
        local = local_ref(CHATTERBOX, fam.branch)
        pin = fam.chatterbox_rev
        if not remote:
            die(f"pin_{fam.name} remote unreachable")
            continue
        if pin != remote:
            die(f"pin_{fam.name} launcher={pin} origin/{fam.branch}={remote}")
        else:
            ok(f"pin_{fam.name} {pin}")
        if local and local != remote:
            die(f"cpp_{fam.name}_unpushed local={local} origin={remote}")
        elif local:
            ok(f"cpp_{fam.name}_pushed {local}")
        else:
            mis(f"cpp_{fam.name}_local_branch")

    need_file("cmake", Path(CMAKE))
    need_file("vulkan_include", VULKAN / "Include")
    need_file("vulkan_lib", VULKAN / "Lib" / "vulkan-1.lib")
    need_file("vulkan_glslc", VULKAN / "Bin" / "glslc.exe")
    ggml = CHATTERBOX / "ggml"
    gh = git(ggml, "rev-parse", "HEAD") if (ggml / ".git").exists() or ggml.is_dir() else ""
    if gh != GGML_REV:
        die(f"ggml head={gh or 'absent'} want={GGML_REV}")
    else:
        ok(f"ggml {gh}")
    need_file("reference.wav", REF)

    for fam in FAMILIES:
        t3, s3, stamp, rev, pid, build, bin_dir, exe, bake, pipe = paths(fam)
        need_file(f"{fam.name}_t3", t3)
        need_file(f"{fam.name}_s3", s3)
        need_file(f"{fam.name}_exe", exe)
        need_file(f"{fam.name}_bake", bake)
        rs = rev.read_text(encoding="ascii").strip() if rev.is_file() else ""
        if rs != fam.chatterbox_rev:
            die(f"{fam.name}_rev_stamp stamp={rs or 'absent'} pin={fam.chatterbox_rev}")
        else:
            ok(f"{fam.name}_rev_stamp {rs}")
        if stamp.is_file():
            ok(f"{fam.name}_voice_stamp bytes={stamp.stat().st_size}")
        else:
            mis(f"{fam.name}_voice_stamp")
        knob = MODELS / f"{fam.name}.knobs"
        setv = []
        if knob.is_file():
            for raw in knob.read_text(encoding="ascii", errors="replace").splitlines():
                if "=" in raw:
                    k, v = raw.split("=", 1)
                    if v.strip():
                        setv.append(f"{k}={v.strip()}")
            ok(f"{fam.name}_knobs " + ("header-defaults" if not setv else " ".join(setv)))
        else:
            mis(f"{fam.name}_knobs")
        ok(f"{fam.name}_pid {'live' if running(pid) else 'absent'} {pipe}")
        dtxt = MODELS / f"{fam.name}_t3_dump.txt"
        dcsv = MODELS / f"{fam.name}_sample_dump.csv"
        tk = dump_keys(dtxt)
        cs = csv_stats(dcsv)
        if tk:
            ok(f"{fam.name}_t3dump {tk}")
        else:
            mis(f"{fam.name}_t3dump")
        if cs:
            ok(f"{fam.name}_csv {cs}")
        else:
            mis(f"{fam.name}_csv")
        if fam.needs_language:
            lp = MODELS / f"{fam.name}.language"
            lang = lp.read_text(encoding="ascii").strip() if lp.is_file() else ""
            if lang:
                ok(f"{fam.name}_language {lang}")
            else:
                mis(f"{fam.name}_language")

    p = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='chatterbox-server.exe'\" | ForEach-Object { $_.ProcessId.ToString() + ' ' + $_.CommandLine }",
        ],
        stdout=subprocess.PIPE,
        stderr=DEVNULL,
        text=True,
        timeout=20,
    )
    daemons = [x.strip() for x in (p.stdout or "").splitlines() if x.strip()]
    if daemons:
        for row in daemons:
            ok(f"daemon {row[:200]}")
    else:
        ok("daemon none")

    if (ROOT / ".venv-eval").is_dir():
        ok("eval_venv")
    else:
        mis("eval_venv")
    lock = ROOT / "eval_out" / "requirements.lock"
    if lock.is_file():
        ok(f"eval_lock bytes={lock.stat().st_size}")
    else:
        mis("eval_lock")
    wavs = sorted(ROOT.glob("20??????-??????-*.wav"))
    if wavs:
        ok("dated_wavs n=" + str(len(wavs)) + " " + " ".join(f"{w.name}:{w.stat().st_size}" for w in wavs[-9:]))
    else:
        mis("dated_wavs")
    for name in (
        "knowledgebase.md",
        "bootstrap_state.py",
        "eval_out/benchmark_text.txt",
        "eval_out/report.json",
        "eval_out/short_baseline_analysis.txt",
        "eval_out/baseline-tts-analysis.canvas.tsx",
    ):
        pth = ROOT / name
        if pth.is_file():
            ok(f"{name} bytes={pth.stat().st_size}")
        else:
            mis(name)

    if FAILED:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
