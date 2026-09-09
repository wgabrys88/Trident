from __future__ import annotations
import json, subprocess, sys, venv, wave
from pathlib import Path

from main import (LOG_DIR, ROOT, TRIDENT_LOG, TTS_LOG, TTS_RATE, _compound_numbers,
                  _repair_forensic_json, _run_logged, jsonl)

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


def finalize_run(run_dir: Path, wav_paths: list[Path], spec: dict, run_ctx: dict,
                 chatterbox_rev: str, sampler_fn) -> None:
    install()
    events_path = run_dir / "events.jsonl"
    if events_path.is_file():
        subprocess.run([str(_python()), "-c",
                        f"import pandas as pd; "
                        f"pd.read_json(r'{events_path}', lines=True)"
                        f".to_parquet(r'{run_dir / 'events.parquet'}', index=False)"],
                       check=True)
    audit_dir = spec.get("audit_dir")
    if audit_dir:
        audit_dir = str(Path(audit_dir).relative_to(ROOT) if Path(audit_dir).is_absolute() else audit_dir)
    manifest = {
        "run_id": run_ctx["run_id"],
        "family": spec["family"],
        "wav_paths": [p.name for p in wav_paths],
        "audit_dir": audit_dir or None,
        "knobs": spec["knobs"],
        "sampler": sampler_fn(spec["knobs"]),
        "chatterbox_rev": chatterbox_rev,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def report(wav: Path) -> None:
    wav = Path(wav)
    if not wav.is_file():
        raise FileNotFoundError(wav)
    install()
    subprocess.run([str(_python()), str(Path(__file__).resolve()), str(wav.resolve())], check=True)


def _read_events() -> list[dict]:
    if not TRIDENT_LOG.is_file():
        return []
    return [json.loads(line) for line in TRIDENT_LOG.read_text(encoding="utf-8").splitlines()]


def spoken_sequence(asr) -> list[str]:
    import pandas as pd
    if isinstance(asr, pd.DataFrame):
        words = asr["word_norm"].tolist() if "word_norm" in asr.columns else asr["word"].str.lower().str.strip(".,").tolist()
    else:
        words = list(asr)
    return _compound_numbers(words)


def load_run(run_id: str) -> dict:
    import pandas as pd
    run_dir = LOG_DIR / "runs" / run_id
    parquet = run_dir / "events.parquet"
    jsonl_path = run_dir / "events.jsonl"
    if parquet.is_file():
        events = pd.read_parquet(parquet)
    elif jsonl_path.is_file():
        events = pd.read_json(jsonl_path, lines=True)
    else:
        events = pd.DataFrame(_read_events())
        if "run_id" in events.columns:
            events = events[events.run_id == run_id]
    if events.empty:
        raise RuntimeError(f"no events for run_id {run_id}")
    if "run_id" in events.columns:
        events = events[events.run_id == run_id]
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    t3_steps = events[events.event == "t3.step"].copy()
    if t3_steps.empty and manifest.get("audit_dir"):
        audit_steps = ROOT / manifest["audit_dir"] / "04-t3-step.jsonl"
        if audit_steps.is_file():
            lines = [_repair_forensic_json(line) for line in audit_steps.read_text(encoding="utf-8").splitlines()
                     if line.strip()]
            t3_steps = pd.read_json("\n".join(lines), lines=True)
            t3_steps = t3_steps[t3_steps.event == "t3.step"] if "event" in t3_steps.columns else t3_steps
    return {
        "events": events,
        "pieces": events[events.event == "synth.piece"].copy(),
        "asr_words": events[events.event == "asr.word"].copy(),
        "asr_diffs": events[events.event == "asr.diff"].copy(),
        "t3_steps": t3_steps,
        "manifest": manifest,
    }


def compare_runs(run_id_a: str, run_id_b: str) -> dict:
    import pandas as pd
    a, b = load_run(run_id_a), load_run(run_id_b)
    rows = []
    for label, run in (("a", a), ("b", b)):
        manifest = run["manifest"]
        for wav in manifest.get("wav_paths", []):
            diff_rows = run["asr_diffs"]
            diff = diff_rows[diff_rows.wav == wav].iloc[-1] if not diff_rows.empty and "wav" in diff_rows.columns else None
            native = run["events"][(run["events"].event == "synth.native")]
            if not native.empty and "wav" not in native.columns:
                complete = run["events"][(run["events"].event == "synth.complete") & (run["events"].wav == wav)]
                resp = int(complete.iloc[-1].response) if not complete.empty else None
                native = native[native.response == resp] if resp is not None else native
            row = {
                "run": label,
                "run_id": manifest.get("run_id", run_id_a if label == "a" else run_id_b),
                "wav": wav,
                "knobs": manifest.get("knobs"),
                "deletions": diff.deletions if diff is not None else None,
                "duplicates": diff.duplicates if diff is not None else None,
            }
            if not native.empty:
                last = native.iloc[-1]
                row["n_speech_tok"] = last.get("n_speech_tok")
                row["stop"] = last.get("stop")
            rows.append(row)
    return {"comparison": pd.DataFrame(rows), "a": a, "b": b}


def _load(wav: Path):
    import pandas as pd
    events = _read_events()
    done = [e for e in events if e.get("event") == "synth.complete" and e.get("wav") == wav.name]
    if not done:
        raise RuntimeError(f"no synth.complete for {wav.name}")
    done_event = done[-1]
    rid = done_event["response"]
    run_id = done_event.get("run_id")
    if run_id:
        events = [e for e in events if e.get("run_id") in (run_id, None)]
    pieces = pd.DataFrame([e for e in events if e.get("event") == "synth.piece" and e.get("response") == rid])
    if pieces.empty:
        raise RuntimeError("synth.piece missing")
    if "t0" not in pieces.columns or "t1" not in pieces.columns:
        raise RuntimeError("synth.piece missing t0/t1")
    pieces["dur"] = pieces["t1"] - pieces["t0"]
    pieces["kind"] = ["short" if c < 20 else "speech" for c in pieces["chars"]]
    if not TTS_LOG.is_file():
        raise RuntimeError("tts.log missing")
    native = []
    for line in TTS_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("{"):
            continue
        obj = json.loads(_repair_forensic_json(line))
        if obj.get("n_speech_tok") is not None and obj.get("stop") is not None:
            native.append(obj)
    if not native:
        raise RuntimeError("tts.log has no piece JSON")
    nd = pd.DataFrame(native)
    nd = nd[nd["response"] == rid][["piece", "n_speech_tok", "stop", "t3_ms", "s3_ms"]]
    if nd.empty:
        raise RuntimeError("native piece JSON missing for this response")
    pieces = pieces.merge(nd, on="piece", how="left")
    if pieces[["n_speech_tok", "stop"]].isna().any().any():
        raise RuntimeError("native piece JSON incomplete")
    return pieces


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
    color = {"short": "#e67e22", "speech": "#2980b9"}
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
          html="listen.html", pieces=int(len(df)), shorts=int((df.kind == "short").sum()))
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    path = Path(sys.argv[1])
    _charts(path, _load(path))
