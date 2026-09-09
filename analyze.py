from __future__ import annotations
import json, subprocess, sys, venv, wave
from pathlib import Path

from main import LOG_DIR, ROOT, TRIDENT_LOG, TTS_LOG, TTS_RATE, _run_logged, jsonl

VENV = ROOT / ".venv"
REPORT = LOG_DIR / "report"


def _python() -> Path:
    return VENV / "Scripts/python.exe"


def install() -> None:
    py = _python()
    marker = VENV / "Lib/site-packages/matplotlib"
    if py.is_file() and marker.is_dir():
        return
    if not py.is_file():
        venv.EnvBuilder(with_pip=True).create(VENV)
    _run_logged([str(py), "-m", "pip", "--isolated", "install", "--no-cache-dir",
                 "--disable-pip-version-check", "--progress-bar", "off", "--no-input",
                 "numpy==2.2.6", "pandas==2.2.3", "matplotlib==3.10.1"], step="pip-analyze")


def report(wav: Path) -> None:
    wav = Path(wav)
    if not wav.is_file():
        raise FileNotFoundError(wav)
    install()
    subprocess.run([str(_python()), str(Path(__file__).resolve()), str(wav.resolve())], check=True)


def _load(wav: Path):
    import pandas as pd
    events = [json.loads(line) for line in TRIDENT_LOG.read_text(encoding="utf-8").splitlines()]
    done = [e for e in events if e.get("event") == "synth.complete" and e.get("wav") == wav.name]
    if not done:
        raise RuntimeError(f"no synth.complete for {wav.name}")
    rid = done[-1]["response"]
    pieces = pd.DataFrame([e for e in events if e.get("event") == "synth.piece" and e.get("response") == rid])
    if pieces.empty:
        raise RuntimeError("synth.piece missing")
    if "t0" not in pieces.columns:
        pieces["t0"] = pieces["sample_start"] / TTS_RATE
        pieces["t1"] = pieces["sample_end"] / TTS_RATE
    pieces["dur"] = pieces["t1"] - pieces["t0"]
    pieces["heading"] = pieces["text"].astype(str).str.startswith("#")
    pieces["short"] = pieces["chars"] < 20
    native = []
    if TTS_LOG.is_file():
        for line in TTS_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("{") and '"piece"' in line:
                native.append(json.loads(line))
    if native:
        nd = pd.DataFrame(native)
        nd = nd[nd["response"] == rid][["piece", "n_speech_tok", "stop", "t3_ms", "s3_ms"]]
        pieces = pieces.merge(nd, on="piece", how="left")
    return pieces


def _wave_rms(wav: Path, hop: int = 960):
    import numpy as np
    with wave.open(str(wav), "rb") as fh:
        n, width, rate = fh.getnframes(), fh.getsampwidth(), fh.getframerate()
        pcm = np.frombuffer(fh.readframes(n), dtype={1: np.int8, 2: np.int16}[width]).astype(np.float32)
    pcm /= 32768.0
    frames = pcm[: len(pcm) // hop * hop].reshape(-1, hop)
    t = (np.arange(len(frames)) * hop + hop / 2) / rate
    return t, np.sqrt((frames ** 2).mean(axis=1))


def _charts(wav: Path, df) -> Path:
    import matplotlib.pyplot as plt
    out = REPORT / wav.stem
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "pieces.csv", index=False)
    colors = ["#c0392b" if h else "#e67e22" if s else "#2980b9" for h, s in zip(df.heading, df.short)]
    fig, ax = plt.subplots(figsize=(14, 4))
    ax.barh(df.piece, df.dur, left=df.t0, color=colors, height=0.9)
    ax.set_xlabel("seconds"); ax.set_ylabel("piece")
    ax.set_title("red = markdown heading   orange = under 20 chars   blue = the rest")
    fig.tight_layout(); fig.savefig(out / "timeline.png", dpi=120); plt.close(fig)
    fig, ax = plt.subplots(figsize=(14, 4))
    t, rms = _wave_rms(wav)
    ax.plot(t, rms, color="#2c3e50", linewidth=0.6)
    for _, row in df.iterrows():
        ax.axvline(row.t0, color="#c0392b" if row.heading else "#95a5a6", linewidth=0.6, alpha=0.8)
    ax.set_xlabel("seconds"); ax.set_ylabel("rms")
    ax.set_title(wav.name)
    fig.tight_layout(); fig.savefig(out / "envelope.png", dpi=120); plt.close(fig)
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.scatter(df.chars, df.dur, c=colors, s=36)
    ax.set_xlabel("chars"); ax.set_ylabel("seconds")
    ax.set_title("tiny text still costing a full utterance is isolation")
    fig.tight_layout(); fig.savefig(out / "chars_vs_time.png", dpi=120); plt.close(fig)
    window = df[(df.t1 >= 100) & (df.t0 <= 120)]
    fig, ax = plt.subplots(figsize=(14, 3 + 0.28 * max(len(window), 1)))
    if not window.empty:
        ax.barh(window.text.str.slice(0, 72), window.dur, left=window.t0, color=[
            "#c0392b" if h else "#e67e22" if s else "#2980b9" for h, s in zip(window.heading, window.short)])
    ax.set_xlabel("seconds"); ax.set_title("1:40–2:00 (the Three. / count collision lives here)")
    fig.tight_layout(); fig.savefig(out / "at_1m49.png", dpi=120); plt.close(fig)
    jsonl("analyze.done", wav=wav.name, dir=str(out.relative_to(ROOT)), pieces=int(len(df)),
          headings=int(df.heading.sum()), shorts=int(df.short.sum()))
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    path = Path(sys.argv[1])
    _charts(path, _load(path))
