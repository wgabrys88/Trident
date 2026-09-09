from __future__ import annotations
import json, subprocess, sys, venv, wave
from pathlib import Path

from main import LOG_DIR, ROOT, TRIDENT_LOG, TTS_LOG, TTS_RATE, _run_logged, jsonl

VENV = ROOT / ".venv"
REPORT = LOG_DIR / "report"
PACKAGES = ("numpy==2.2.6", "pandas==2.2.3", "pyarrow==19.0.1", "matplotlib==3.10.1",
            "seaborn==0.13.2", "plotly==5.24.1")


def _python() -> Path:
    return VENV / "Scripts/python.exe"


def install() -> None:
    py = _python()
    marker = VENV / "Lib/site-packages/plotly"
    if py.is_file() and marker.is_dir():
        return
    if not py.is_file():
        venv.EnvBuilder(with_pip=True).create(VENV)
    _run_logged([str(py), "-m", "pip", "--isolated", "install", "--no-cache-dir",
                 "--disable-pip-version-check", "--progress-bar", "off", "--no-input", *PACKAGES],
                step="pip-analyze")


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
    pieces["kind"] = np_where(pieces)
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


def np_where(pieces):
    kind = []
    for text, chars in zip(pieces["text"].astype(str), pieces["chars"]):
        if text.startswith("#"):
            kind.append("heading")
        elif chars < 20:
            kind.append("short")
        else:
            kind.append("speech")
    return kind


def _wave_rms(wav: Path, hop: int = 960):
    import numpy as np
    with wave.open(str(wav), "rb") as fh:
        n, width = fh.getnframes(), fh.getsampwidth()
        pcm = np.frombuffer(fh.readframes(n), dtype={1: np.int8, 2: np.int16}[width]).astype(np.float32)
    pcm /= 32768.0
    frames = pcm[: len(pcm) // hop * hop].reshape(-1, hop)
    t = (np.arange(len(frames)) * hop + hop / 2) / TTS_RATE
    return t, np.sqrt((frames ** 2).mean(axis=1))


def _charts(wav: Path, df) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import plotly.express as px
    import seaborn as sns
    out = REPORT / wav.stem
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "pieces.csv", index=False)
    df.to_parquet(out / "pieces.parquet", index=False)
    color = {"heading": "#c0392b", "short": "#e67e22", "speech": "#2980b9"}
    hover = [c for c in ("text", "chars", "t0", "t1", "n_speech_tok", "stop") if c in df.columns]
    bars = px.bar(
        df, x="dur", y="piece", base="t0", orientation="h", color="kind",
        hover_data=hover, color_discrete_map=color,
        title=f"{wav.name} — hover, zoom, pan",
    )
    bars.update_yaxes(autorange="reversed", title="piece")
    bars.update_xaxes(title="seconds")
    t, rms = _wave_rms(wav)
    env = px.line(x=t, y=rms, labels={"x": "seconds", "y": "rms"}, title="waveform envelope")
    for _, row in df.iterrows():
        env.add_vline(x=row.t0, line_color=color[row.kind], line_width=1)
    from plotly.subplots import make_subplots
    page = make_subplots(rows=2, cols=1, subplot_titles=("pieces", "envelope"), shared_xaxes=True)
    for trace in bars.data:
        page.add_trace(trace, row=1, col=1)
    for trace in env.data:
        page.add_trace(trace, row=2, col=1)
    page.update_layout(height=820, barmode="overlay",
                       title=f"{wav.name} — open listen.html")
    page.write_html(out / "listen.html", include_plotlyjs=True)
    fig, ax = plt.subplots(figsize=(14, 4))
    ax.plot(t, rms, color="#2c3e50", linewidth=0.6)
    for _, row in df.iterrows():
        ax.axvline(row.t0, color=color[row.kind], linewidth=0.7, alpha=0.85)
    ax.set_xlabel("seconds"); ax.set_ylabel("rms"); ax.set_title(wav.name)
    fig.tight_layout(); fig.savefig(out / "envelope.png", dpi=140); plt.close(fig)
    fig, ax = plt.subplots(figsize=(8, 5))
    sns.scatterplot(data=df, x="chars", y="dur", hue="kind", palette=color, s=42, ax=ax)
    ax.set_title("tiny text still costing a full utterance is isolation")
    fig.tight_layout(); fig.savefig(out / "chars_vs_time.png", dpi=140); plt.close(fig)
    fig, ax = plt.subplots(figsize=(8, 4))
    sns.histplot(data=df, x="dur", hue="kind", palette=color, multiple="stack", ax=ax)
    ax.set_title("how long each kind of piece occupies the ear")
    fig.tight_layout(); fig.savefig(out / "duration_hist.png", dpi=140); plt.close(fig)
    jsonl("analyze.done", wav=wav.name, dir=str(out.relative_to(ROOT)),
          html="listen.html", pieces=int(len(df)),
          headings=int((df.kind == "heading").sum()), shorts=int((df.kind == "short").sum()))
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    path = Path(sys.argv[1])
    _charts(path, _load(path))
