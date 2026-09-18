from __future__ import annotations
import base64
import html
import json
import sys
from pathlib import Path
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.offline import get_plotlyjs
from run_tables import wav_path


def data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def f0_plot(run_dir: Path, run_id: str) -> str:
    path = run_dir / "acoustic_frames.parquet"
    if not path.is_file():
        return ""
    frame = pd.read_parquet(path)
    stride = max(1, len(frame) // 4000)
    frame = frame.iloc[::stride]
    fig = go.Figure()
    for col, name in (("f0_praat_hz", "Praat"), ("f0_pyworld_hz", "WORLD"), ("f0_torchcrepe_hz", "CREPE")):
        if col in frame:
            fig.add_trace(go.Scattergl(x=frame["t_s"], y=frame[col], mode="lines", name=name))
    fig.update_layout(title=f"F0 estimators: {run_id}", height=330, margin=dict(l=45, r=20, t=45, b=40), xaxis_title="seconds", yaxis_title="Hz")
    return pio.to_html(fig, include_plotlyjs=False, full_html=False, config={"displaylogo": False, "responsive": True})


def img(run_dir: Path, name: str) -> str:
    path = run_dir / name
    return f'<img loading="lazy" src="{data_uri(path, "image/png")}" alt="{html.escape(name)}">' if path.is_file() else ""


def render_run(run_dir: Path) -> str:
    meta = read_json(run_dir / "meta.json")
    analysis = read_json(run_dir / "analysis_metrics.json")
    asr = read_json(run_dir / "asr_parakeet.json")
    normalization = read_json(run_dir / "normalization.json")
    alignment = read_json(run_dir / "normalization_alignment.json")
    determinism = read_json(run_dir / "determinism.json")
    wav = wav_path(run_dir, meta)
    run_id = str(meta.get("run_id") or run_dir.name)
    original = str(meta.get("original_text") or normalization.get("original_text") or "")
    transport = str(meta.get("transport_text") or normalization.get("transport_text") or original)
    transcript = str(asr.get("transcript") or "")
    audio = data_uri(wav, "audio/wav")
    changes = normalization.get("changes") or []
    aligned = alignment.get("items") or []
    rows = []
    for i, change in enumerate(changes):
        item = aligned[i] if i < len(aligned) else {}
        score = item.get("character_similarity")
        score_text = f"{float(score):.3f}" if score is not None else ""
        rows.append(f"<tr><td>{html.escape(str(change.get('kind','')))}</td><td>{html.escape(str(change.get('source','')))}</td><td>{html.escape(str(change.get('target','')))}</td><td>{html.escape(score_text)}</td><td>{html.escape(str(item.get('best_transcript_window','')))}</td></tr>")
    change_rows = "".join(rows)
    metrics = html.escape(json.dumps(analysis, ensure_ascii=False, indent=2))
    knobs = html.escape(json.dumps(meta.get("knobs") or {}, ensure_ascii=False, indent=2))
    determinism_text = html.escape(json.dumps(determinism, ensure_ascii=False, indent=2))
    conversion_text = html.escape(json.dumps(meta.get("conversion_types") or {}, ensure_ascii=False, indent=2))
    return f'''<article class="run" data-run="{html.escape(run_id)}">
<header><h2>{html.escape(run_id)}</h2><span>{html.escape(str(meta.get("variant", "")))} / {html.escape(str(meta.get("language_id") or "en"))}</span></header>
<div class="audio"><audio controls preload="metadata" src="{audio}"></audio><button class="play">Play</button><button class="loop">Loop off</button></div>
<section class="texts"><div><h3>Original</h3><p>{html.escape(original)}</p></div><div><h3>Transport</h3><p>{html.escape(transport)}</p></div><div><h3>Parakeet</h3><p>{html.escape(transcript)}</p></div></section>
<details open><summary>Engine number frontend and ASR alignment</summary><table><thead><tr><th>Kind</th><th>Source</th><th>Spoken transport</th><th>Similarity</th><th>Best transcript window</th></tr></thead><tbody>{change_rows}</tbody></table></details>
<div class="plots">{img(run_dir, "spectrogram_tokens.png")}{img(run_dir, "waveform_tokens.png")}{img(run_dir, "f0_estimators.png")}{img(run_dir, "energy_spectral.png")}</div>
<div class="interactive">{f0_plot(run_dir, run_id)}</div>
<details><summary>Metrics</summary><pre>{metrics}</pre></details><details><summary>Runtime knobs</summary><pre>{knobs}</pre></details><details><summary>Determinism</summary><pre>{determinism_text}</pre></details><details><summary>Conversion precision</summary><pre>{conversion_text}</pre></details>
</article>'''


def page(run_dirs: list[Path], title: str) -> str:
    body = "\n".join(render_run(x) for x in run_dirs)
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>
:root{{font-family:Inter,ui-sans-serif,system-ui,sans-serif;color-scheme:dark;background:#0b0d10;color:#eef1f5}}body{{margin:0;padding:24px;max-width:1800px;margin-inline:auto}}h1{{font-size:clamp(26px,4vw,52px);margin:0 0 24px}}.run{{background:#14181d;border:1px solid #2b333d;border-radius:16px;padding:20px;margin:0 0 24px;box-shadow:0 12px 35px #0005}}header{{display:flex;justify-content:space-between;gap:20px;align-items:baseline}}h2,h3{{margin:.2rem 0}}.audio{{display:flex;gap:10px;align-items:center;margin:14px 0}}audio{{width:min(820px,75vw)}}button{{font:inherit;padding:8px 12px;border-radius:9px;border:1px solid #4b5866;background:#202731;color:inherit;cursor:pointer}}.texts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}}.texts>div,details{{background:#0f1318;border:1px solid #252c34;border-radius:10px;padding:12px}}p{{white-space:pre-wrap;line-height:1.5}}table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;padding:7px;border-bottom:1px solid #2a3139;vertical-align:top}}.plots{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:12px;margin-top:12px}}img{{display:block;width:100%;height:auto;border-radius:8px;background:white}}pre{{overflow:auto;white-space:pre-wrap}}summary{{cursor:pointer;font-weight:700}}.interactive{{margin-top:12px;background:white;border-radius:8px;overflow:hidden}}@media(max-width:600px){{body{{padding:10px}}.plots{{grid-template-columns:1fr}}header{{display:block}}}}
</style><script>{get_plotlyjs()}</script></head><body><h1>{html.escape(title)}</h1>{body}<script>
document.querySelectorAll('.run').forEach(r=>{{const a=r.querySelector('audio');r.querySelector('.play').onclick=()=>a.play();r.querySelector('.loop').onclick=e=>{{a.loop=!a.loop;e.currentTarget.textContent=a.loop?'Loop on':'Loop off'}}}});const newest=[...document.querySelectorAll('audio')].at(-1);if(newest)newest.play().catch(()=>{{}});
</script></body></html>'''


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: report_html.py RUN_DIR OUTPUT_ROOT")
    run_dir = Path(sys.argv[1]).resolve()
    output_root = Path(sys.argv[2]).resolve()
    (run_dir / "report.html").write_text(page([run_dir], f"Trident run {run_dir.name}"), encoding="utf-8")
    runs = []
    for candidate in output_root.glob("*_log"):
        if (candidate / "meta.json").is_file():
            try:
                wav_path(candidate, read_json(candidate / "meta.json"))
            except BaseException:
                continue
            runs.append(candidate)
    runs.sort(key=lambda p: (read_json(p / "meta.json").get("started_at") or "", p.name))
    (output_root / "tts_dashboard.html").write_text(page(runs, "Trident TTS dashboard"), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
