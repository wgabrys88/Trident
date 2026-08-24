from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import gradio as gr
import numpy as np

from config import ASR_RATE, FAMILIES, LANGUAGES, LIVE_AUDIO, LIVE_SETTINGS, REFERENCE_VOICES, TTS_RATE, Paths, resolve_voice, save_live_settings
from conversation import Conversation
from main import effective_family, finish, prepared_reference, resolved_tts, start_run, stream_synthesize, write_meta
from ui_streaming import highlighted_progress, pcm16_lookahead, text_batches

ROOT = Path(__file__).resolve().parent
INT_FLAGS = {
    "n_gpu_layers": "--n-gpu-layers", "context": "--context", "threads": "--threads", "seed": "--seed",
    "max_tokens": "--max-tokens", "top_k": "--top-k", "cfm_steps": "--cfm-steps",
    "first_chunk_chars": "--first-chunk-chars", "chunk_chars": "--chunk-chars",
    "stream_first_chunk_tokens": "--stream-first-chunk-tokens", "stream_chunk_tokens": "--stream-chunk-tokens",
}
FLOAT_FLAGS = {
    "top_p": "--top-p", "min_p": "--min-p", "temperature": "--temperature",
    "repeat_penalty": "--repeat-penalty", "cfg_weight": "--cfg-weight", "exaggeration": "--exaggeration",
}
TTS_SETTING_KEYS = ["family", "language", "voice", "reference", "join", *INT_FLAGS, *FLOAT_FLAGS]
LIVE_SETTING_KEYS = [
    "ingestion_mode", "eou_trigger", "vad_trigger", "vad_threshold", "vad_silence_ms", "char_trigger",
    "system_prompt", "tts_mode", "tts_family", "tts_language", "tts_voice", "tts_join",
]
CSS = """
.gradio-container {max-width: 1560px !important;}
.trident-hero {padding: 14px 18px; border: 1px solid var(--border-color-primary); border-radius: 14px; margin-bottom: 10px;}
.trident-hero h1 {margin: 0 0 4px 0; font-size: 1.55rem;}
.trident-hero p {margin: 0; opacity: .72;}
.trident-deck {border: 1px solid var(--border-color-primary); border-radius: 14px; padding: 10px;}
.trident-status textarea {font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace !important;}
"""

_sessions: dict[str, Conversation] = {}


def _pcm16k(audio) -> bytes:
    if audio is None:
        return b""
    rate, values = audio
    x = np.asarray(values)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if np.issubdtype(x.dtype, np.integer):
        x = x.astype(np.float32) / max(abs(np.iinfo(x.dtype).min), np.iinfo(x.dtype).max)
    else:
        x = x.astype(np.float32, copy=False)
    x = np.clip(x, -1.0, 1.0)
    if rate != ASR_RATE and x.size:
        count = max(1, round(x.size * ASR_RATE / rate))
        x = np.interp(np.linspace(0, x.size - 1, count), np.arange(x.size), x).astype(np.float32)
    return x.astype("<f4", copy=False).tobytes()


def _settings(values) -> dict:
    return dict(zip(TTS_SETTING_KEYS, values, strict=True))


def _namespace(settings: dict, streaming: bool) -> argparse.Namespace:
    values = {key: settings.get(key) for key in (*INT_FLAGS, *FLOAT_FLAGS)}
    values.update(streaming=streaming, stream_join=settings["join"])
    return argparse.Namespace(**values)


def _reference(root: Paths, settings: dict) -> Path:
    custom = settings.get("reference")
    return Path(custom).resolve() if custom else resolve_voice(root.data_dir, settings["voice"])


def _cli(root: Paths, parts: list[str]) -> str:
    command = [sys.executable, str(ROOT / "main.py"), "--models-dir", str(root.models_dir), "--data-dir", str(root.data_dir), *parts]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or f"CLI failed with exit code {result.returncode}").strip())
    return result.stdout.strip()


def _tts_cli_args(root: Paths, settings: dict, streaming: bool, language_flag: str = "--language") -> list[str]:
    args = [
        "--family", settings["family"], language_flag, settings["language"],
        "-r", str(_reference(root, settings)),
        "--streaming" if streaming else "--no-streaming",
        "--stream-join", settings["join"],
    ]
    for key, flag in INT_FLAGS.items():
        value = settings.get(key)
        if value is not None:
            args += [flag, str(int(value))]
    for key, flag in FLOAT_FLAGS.items():
        value = settings.get(key)
        if value is not None:
            args += [flag, str(float(value))]
    return args


def _system_prompt_args(text: str | None, filename: str | None) -> list[str]:
    text = str(text or "").strip()
    if text and filename:
        raise RuntimeError("choose one system prompt source")
    if filename:
        return ["--system-prompt-file", str(Path(filename).resolve())]
    return ["--system-prompt", text] if text else []


def _output_path(stdout: str) -> str:
    for line in reversed(stdout.splitlines()):
        if line.startswith("Output: "):
            return line[8:].strip()
    raise RuntimeError("CLI did not report an output WAV")


def _cleanup_session(session_id: str) -> None:
    engine = _sessions.pop(session_id, None)
    if engine:
        engine.close()


def build(models_dir: Path | None = None, data_dir: Path | None = None):
    root = Paths(models_dir, data_dir)

    def family_changed(name):
        family = FAMILIES[name]
        return gr.Dropdown(choices=list(family["TTS_LANGUAGES"]), value=family["DEFAULT_REPLY_LANGUAGE"])

    def live_settings(values) -> dict:
        settings = dict(zip(LIVE_SETTING_KEYS, values, strict=True))
        settings["vad_threshold"] = float(settings["vad_threshold"])
        settings["vad_silence_ms"] = int(settings["vad_silence_ms"])
        settings["char_trigger"] = int(settings["char_trigger"])
        if settings["tts_language"] not in FAMILIES[settings["tts_family"]]["TTS_LANGUAGES"]:
            raise RuntimeError(f"language {settings['tts_language']!r} is not wired in {settings['tts_family']}")
        return settings

    def save_config(session_id: str, *values):
        settings = live_settings(values)
        save_live_settings(settings)
        engine = _sessions.get(session_id)
        if engine and engine.active:
            engine.configure(settings)
        return "Configuration written to config.py"

    def start_conversation(session_id: str, *values):
        _cleanup_session(session_id)
        settings = live_settings(values)
        save_live_settings(settings)
        engine = Conversation(root.models_dir, root.data_dir, settings)
        engine.start()
        _sessions[session_id] = engine
        return engine.transcript, engine.answer, engine.progress, engine.status

    def conversation_pump(session_id: str):
        engine = _sessions[session_id]
        while True:
            kind, payload = engine.next_output()
            audio = gr.skip()
            if kind == "audio-pcm":
                audio = (TTS_RATE, np.frombuffer(payload, dtype="<i2").copy())
            elif kind == "audio-file":
                audio = payload
            yield engine.transcript, engine.answer, engine.progress, audio, engine.status
            if kind == "error":
                raise RuntimeError(str(payload))
            if kind == "closed":
                return

    def feed_conversation(audio, session_id: str):
        _sessions[session_id].feed_audio(_pcm16k(audio))

    def ptt_start(session_id: str):
        engine = _sessions[session_id]
        engine.ptt_start()
        return engine.status

    def ptt_stop(session_id: str):
        engine = _sessions[session_id]
        engine.ptt_stop()
        return engine.status

    def microphone_recording(enabled: bool):
        return gr.Audio(recording=enabled)

    def manual_submit(text: str, session_id: str):
        engine = _sessions[session_id]
        engine.submit(str(text or ""))
        return ""

    def stop_conversation(session_id: str):
        engine = _sessions[session_id]
        engine.stop()
        return engine.status

    def speak(text: str, mode: str, *values):
        text = str(text or "").strip()
        if not text:
            raise RuntimeError("text is empty")
        settings = _settings(values)
        family = effective_family(settings["family"], _namespace(settings, streaming=mode == "real"))
        language = settings["language"]
        if language not in family["TTS_LANGUAGES"]:
            raise RuntimeError(f"language {language!r} is not wired in {family['name']}")
        if mode == "buffered":
            chunk = family["TTS_CHUNK"]
            batches = text_batches(text, chunk.get("first_chars", chunk["chars"]), chunk["chars"], int(LIVE_AUDIO["tts_fake_group_chunks"]))
            if not batches:
                raise RuntimeError("text is empty")

            def render(index: int) -> str:
                part, _ = batches[index]
                return _output_path(_cli(root, ["tts", "-t", part, *_tts_cli_args(root, settings, streaming=False)]))

            pending = render(0)
            if len(batches) == 1:
                yield pending, highlighted_progress(text, len(text), len(text)), "Buffered WAV · complete"
                return
            next_path = render(1)
            yield pending, highlighted_progress(text, batches[0][1], batches[1][1]), f"Buffered WAV · 1/{len(batches)} sent · one ahead"
            pending = next_path
            for index in range(2, len(batches)):
                next_path = render(index)
                yield pending, highlighted_progress(text, batches[index - 1][1], batches[index][1]), f"Buffered WAV · {index}/{len(batches)} sent · one ahead"
                pending = next_path
            yield pending, highlighted_progress(text, len(text), len(text)), "Buffered WAV · complete"
            return
        paths = start_run("ui-tts-stream", root.models_dir, root.data_dir)
        reference = prepared_reference(_reference(root, settings), root.data_dir)
        count = 0
        for raw in pcm16_lookahead(stream_synthesize(text, reference, paths.output, language, family, paths), TTS_RATE, float(LIVE_AUDIO["tts_gradio_min_seconds"])):
            count += 1
            yield (TTS_RATE, np.frombuffer(raw, dtype="<i2").copy()), highlighted_progress(text, 0, len(text)), f"Native stream · chunk {count} · one ahead"
        write_meta(paths, command="ui-tts", family=family["name"], language=language, output=paths.output, resolved_tts=resolved_tts(family), streaming=1, join=settings["join"], gradio_lookahead=1)
        finish(paths)
        yield gr.skip(), highlighted_progress(text, len(text), len(text)), "Native stream · complete"

    def cli_tts(text: str, text_file: str | None, output_file: str, cli_streaming: bool, *values):
        text = str(text or "").strip()
        if bool(text) == bool(text_file):
            raise RuntimeError("provide exactly one TTS text source")
        settings = _settings(values)
        parts = ["tts"]
        if text_file:
            parts.append(str(Path(text_file).resolve()))
        else:
            parts += ["-t", text]
        if output_file := str(output_file or "").strip():
            parts += ["-o", output_file]
        stdout = _cli(root, [*parts, *_tts_cli_args(root, settings, streaming=bool(cli_streaming))])
        return str(Path(output_file).expanduser().resolve()) if output_file else _output_path(stdout), stdout

    def cli_asr(audio: str | None, output_file: str):
        if not audio:
            raise RuntimeError("ASR input audio is required")
        parts = ["asr", str(Path(audio).resolve())]
        if output_file := str(output_file or "").strip():
            parts += ["-o", output_file]
        stdout = _cli(root, parts)
        marker = "\nRun: "
        return stdout.rsplit(marker, 1)[0].strip() if marker in stdout else stdout

    def cli_brain(text: str, text_file: str | None, output_file: str, language: str, system_prompt: str, system_prompt_file: str | None):
        text = str(text or "").strip()
        if bool(text) == bool(text_file):
            raise RuntimeError("provide exactly one Brain text source")
        parts = ["brain"]
        if text_file:
            parts.append(str(Path(text_file).resolve()))
        else:
            parts += ["-t", text]
        if output_file := str(output_file or "").strip():
            parts += ["-o", output_file]
        parts += ["--language", language, *_system_prompt_args(system_prompt, system_prompt_file)]
        return _cli(root, parts)

    def cli_run(audio: str | None, output_file: str, cli_streaming: bool, system_prompt: str, system_prompt_file: str | None, *values):
        if not audio:
            raise RuntimeError("pipeline input audio is required")
        settings = _settings(values)
        parts = ["run", str(Path(audio).resolve())]
        if output_file := str(output_file or "").strip():
            parts += ["-o", output_file]
        stdout = _cli(root, [*parts, *_tts_cli_args(root, settings, streaming=bool(cli_streaming), language_flag="--tts-language"), *_system_prompt_args(system_prompt, system_prompt_file)])
        if not stdout.startswith("Transcript: ") or "\nAnswer: " not in stdout or "\nOutput: " not in stdout:
            raise RuntimeError("pipeline CLI output format changed")
        transcript, rest = stdout[len("Transcript: "):].split("\nAnswer: ", 1)
        answer, output = rest.rsplit("\nOutput: ", 1)
        actual_output = str(Path(output_file).expanduser().resolve()) if output_file else output.strip()
        return transcript.strip(), answer.strip(), actual_output, stdout

    def cli_resident_status():
        return _cli(root, ["resident", "status"])

    def cli_resident_stop():
        return _cli(root, ["resident", "stop"])

    def cli_resident_warm(cli_streaming: bool, *values):
        settings = _settings(values)
        return _cli(root, ["resident", "warm", *_tts_cli_args(root, settings, streaming=bool(cli_streaming), language_flag="--tts-language")])

    def cli_install(family_name: str):
        return _cli(root, ["install", "--family", family_name]) or "Install complete"

    voices = list(REFERENCE_VOICES)
    family_default = LIVE_SETTINGS["tts_family"]
    live_language_default = LIVE_SETTINGS["tts_language"]

    with gr.Blocks(fill_width=True, title="Trident", delete_cache=(86400, 86400)) as demo:
        session_id = gr.State(value=lambda: uuid4().hex, time_to_live=3600, delete_callback=_cleanup_session)
        gr.HTML("<div class='trident-hero'><h1>Trident Full-Duplex Console</h1><p>Persistent Parakeet EOU ASR · resident Gemma · resident Chatterbox. Streaming stages run independently; CLI operations remain available below.</p></div>")

        with gr.Accordion("Shared TTS deck", open=False):
            with gr.Row():
                family = gr.Dropdown(list(FAMILIES), value=family_default, label="Family")
                language = gr.Dropdown(list(FAMILIES[family_default]["TTS_LANGUAGES"]), value=live_language_default, label="Language")
                voice = gr.Dropdown(voices, value=LIVE_SETTINGS["tts_voice"], label="Voice")
                reference = gr.Audio(sources=["upload", "microphone"], type="filepath", label="Custom reference · manual/CLI")
                join = gr.Radio([("Chunks", "chunks"), ("Crossfade", "crossfade")], value=LIVE_SETTINGS["tts_join"], label="Join")
            with gr.Accordion("Engine overrides · blank means family default", open=False):
                with gr.Row():
                    n_gpu_layers = gr.Number(value=None, precision=0, label="GPU layers")
                    context = gr.Number(value=None, precision=0, label="Context")
                    threads = gr.Number(value=None, precision=0, label="Threads")
                    seed = gr.Number(value=None, precision=0, label="Seed")
                    max_tokens = gr.Number(value=None, precision=0, label="Max tokens")
                    top_k = gr.Number(value=None, precision=0, label="Top K")
                with gr.Row():
                    top_p = gr.Number(value=None, label="Top P")
                    min_p = gr.Number(value=None, label="Min P")
                    temperature = gr.Number(value=None, label="Temperature")
                    repeat_penalty = gr.Number(value=None, label="Repeat penalty")
                    cfm_steps = gr.Number(value=None, precision=0, label="CFM steps")
                    cfg_weight = gr.Number(value=None, label="CFG weight")
                    exaggeration = gr.Number(value=None, label="Exaggeration")
                with gr.Row():
                    first_chunk_chars = gr.Number(value=None, precision=0, label="First text chunk chars")
                    chunk_chars = gr.Number(value=None, precision=0, label="Text chunk chars")
                    stream_first_chunk_tokens = gr.Number(value=None, precision=0, label="First stream tokens")
                    stream_chunk_tokens = gr.Number(value=None, precision=0, label="Stream tokens")
        tts_inputs = [
            family, language, voice, reference, join,
            n_gpu_layers, context, threads, seed, max_tokens, top_k, cfm_steps,
            first_chunk_chars, chunk_chars, stream_first_chunk_tokens, stream_chunk_tokens,
            top_p, min_p, temperature, repeat_penalty, cfg_weight, exaggeration,
        ]
        family.change(family_changed, family, language, queue=False)

        with gr.Tab("Conversation"):
            with gr.Row(equal_height=False):
                with gr.Column(scale=1, min_width=340, elem_classes="trident-deck"):
                    ingestion = gr.Radio([("Continuous", "continuous"), ("Push-to-talk gate", "ptt")], value=LIVE_SETTINGS["ingestion_mode"], label="Microphone mode")
                    mic = gr.Audio(sources=["microphone"], type="numpy", streaming=True, label=f"Microphone · {LIVE_AUDIO['asr_feed_seconds'] * 1000:.0f} ms transport")
                    with gr.Row():
                        start_button = gr.Button("Start engine", variant="primary")
                        stop_button = gr.Button("Stop engine")
                    with gr.Row():
                        ptt_on = gr.Button("PTT ON")
                        ptt_send = gr.Button("PTT SEND")
                    live_status = gr.Textbox(value="Stopped", label="Pipeline status", interactive=False, elem_classes="trident-status")
                with gr.Column(scale=2, min_width=480, elem_classes="trident-deck"):
                    transcript = gr.Textbox(label="Parakeet partial transcript", lines=6, interactive=False)
                    answer = gr.Textbox(label="Gemma streaming response", lines=7, interactive=False)
                    progress = gr.HighlightedText(label="TTS progress", show_legend=True, show_inline_category=False, combine_adjacent=True, color_map={"sent": "#22c55e", "buffered": "#f59e0b", "pending": "#64748b"})
                    live_audio = gr.Audio(label="Spoken response", streaming=True, autoplay=True)

            with gr.Row(equal_height=False):
                with gr.Column(elem_classes="trident-deck"):
                    gr.Markdown("### Turn triggers")
                    with gr.Row():
                        eou_trigger = gr.Checkbox(value=LIVE_SETTINGS["eou_trigger"], label="Parakeet native EOU")
                        vad_trigger = gr.Checkbox(value=LIVE_SETTINGS["vad_trigger"], label="Silero VAD silence")
                    vad_threshold = gr.Slider(0.1, 0.9, value=LIVE_SETTINGS["vad_threshold"], step=0.05, label="Silero speech threshold")
                    vad_silence = gr.Slider(100, 1500, value=LIVE_SETTINGS["vad_silence_ms"], step=20, label="Silence offset · ms")
                    char_trigger = gr.Slider(40, 1200, value=LIVE_SETTINGS["char_trigger"], step=20, label="Secondary transcript character trigger")
                with gr.Column(elem_classes="trident-deck"):
                    gr.Markdown("### Dialogue behavior")
                    system_prompt = gr.Textbox(value=LIVE_SETTINGS["system_prompt"], label="Dynamic system prompt", lines=7)
                    tts_mode = gr.Radio([("Native stream", "real"), ("Buffered WAV · five native chunks/request", "buffered")], value=LIVE_SETTINGS["tts_mode"], label="Conversation TTS delivery")
                    manual_text = gr.Textbox(label="Manual prompt", placeholder="Enter text, or leave blank to flush current ASR")
                    with gr.Row():
                        submit_button = gr.Button("Submit now")
                        save_button = gr.Button("Apply config")
                    config_status = gr.Textbox(value="", label="Config", interactive=False, elem_classes="trident-status")

            live_inputs = [ingestion, eou_trigger, vad_trigger, vad_threshold, vad_silence, char_trigger, system_prompt, tts_mode, family, language, voice, join]
            start_event = start_button.click(start_conversation, [session_id, *live_inputs], [transcript, answer, progress, live_status], concurrency_limit=None, show_progress="minimal")
            start_event.then(lambda mode: microphone_recording(mode == "continuous"), ingestion, mic, queue=False).then(conversation_pump, session_id, [transcript, answer, progress, live_audio, live_status], concurrency_limit=None, show_progress="hidden")
            mic.stream(feed_conversation, [mic, session_id], outputs=None, time_limit=LIVE_AUDIO["mic_time_limit_seconds"], stream_every=LIVE_AUDIO["asr_feed_seconds"], concurrency_limit=1, show_progress="hidden")
            ptt_on_event = ptt_on.click(ptt_start, session_id, live_status, concurrency_limit=None)
            ptt_on_event.then(lambda: microphone_recording(True), outputs=mic, queue=False)
            ptt_send.click(lambda: microphone_recording(False), outputs=mic, queue=False).then(ptt_stop, session_id, live_status, concurrency_limit=None)
            submit_button.click(manual_submit, [manual_text, session_id], manual_text, concurrency_limit=None)
            save_button.click(save_config, [session_id, *live_inputs], config_status, concurrency_limit=None)
            stop_button.click(lambda: microphone_recording(False), outputs=mic, queue=False).then(stop_conversation, session_id, live_status, concurrency_limit=None, show_progress="minimal")

        with gr.Tab("Manual TTS"):
            with gr.Row(equal_height=False):
                with gr.Column(elem_classes="trident-deck"):
                    manual_tts_text = gr.Textbox(label="Text", lines=9)
                    manual_tts_mode = gr.Radio([("Native real stream", "real"), ("Buffered WAV · five chunks/request", "buffered")], value="real", label="Delivery")
                    with gr.Row():
                        speak_button = gr.Button("Speak", variant="primary")
                        stop_speak = gr.Button("Stop after current chunk")
                with gr.Column(elem_classes="trident-deck"):
                    manual_output = gr.Audio(label="Output", streaming=True, autoplay=True)
                    manual_progress = gr.HighlightedText(label="Synthesis progress", show_legend=True, show_inline_category=False, combine_adjacent=True, color_map={"sent": "#22c55e", "buffered": "#f59e0b", "pending": "#64748b"})
                    manual_status = gr.Textbox(value="Idle", label="TTS status", interactive=False, elem_classes="trident-status")
            speak_event = speak_button.click(speak, [manual_tts_text, manual_tts_mode, *tts_inputs], [manual_output, manual_progress, manual_status], concurrency_limit=None, show_progress="minimal")
            stop_speak.click(None, cancels=[speak_event], queue=False)

        with gr.Tab("CLI parity"):
            gr.Markdown("Every control in this tab invokes `main.py` directly. Shared TTS deck values above map one-for-one to the corresponding CLI flags.")
            with gr.Row(equal_height=False):
                with gr.Column(elem_classes="trident-deck"):
                    gr.Markdown("### `asr`")
                    asr_file = gr.Audio(sources=["upload", "microphone"], type="filepath", label="Input audio")
                    asr_output_file = gr.Textbox(label="Optional output path (`-o`)")
                    asr_file_button = gr.Button("Transcribe file")
                    asr_file_text = gr.Textbox(label="Transcript", lines=6)
                    asr_file_button.click(cli_asr, [asr_file, asr_output_file], asr_file_text, concurrency_limit=None)
                with gr.Column(elem_classes="trident-deck"):
                    gr.Markdown("### `brain`")
                    brain_text = gr.Textbox(label="Text (`-t`)", lines=4)
                    brain_file = gr.File(label="Text file", file_types=[".txt"], type="filepath")
                    brain_output_file = gr.Textbox(label="Optional output path (`-o`)")
                    brain_language = gr.Dropdown(list(LANGUAGES), value="en", label="Language")
                    brain_system = gr.Textbox(label="System prompt", lines=3)
                    brain_system_file = gr.File(label="System prompt file", file_types=[".txt"], type="filepath")
                    brain_button = gr.Button("Run Brain")
                    brain_output = gr.Textbox(label="Answer", lines=7)
                    brain_button.click(cli_brain, [brain_text, brain_file, brain_output_file, brain_language, brain_system, brain_system_file], brain_output, concurrency_limit=None)
            with gr.Row(equal_height=False):
                with gr.Column(elem_classes="trident-deck"):
                    gr.Markdown("### `tts`")
                    cli_tts_text = gr.Textbox(label="Text (`-t`)", lines=5)
                    cli_tts_file = gr.File(label="Text file", file_types=[".txt"], type="filepath")
                    cli_tts_output_file = gr.Textbox(label="Optional output WAV path (`-o`)")
                    cli_tts_streaming = gr.Checkbox(value=False, label="CLI `--streaming`")
                    cli_tts_button = gr.Button("Render WAV")
                    cli_tts_audio = gr.Audio(label="Rendered WAV")
                    cli_tts_log = gr.Textbox(label="CLI output", lines=4, interactive=False, elem_classes="trident-status")
                    cli_tts_button.click(cli_tts, [cli_tts_text, cli_tts_file, cli_tts_output_file, cli_tts_streaming, *tts_inputs], [cli_tts_audio, cli_tts_log], concurrency_limit=None)
                with gr.Column(elem_classes="trident-deck"):
                    gr.Markdown("### `run` ASR → Brain → TTS")
                    run_audio = gr.Audio(sources=["upload", "microphone"], type="filepath", label="Input audio")
                    run_output_file = gr.Textbox(label="Optional output WAV path (`-o`)")
                    run_streaming = gr.Checkbox(value=False, label="CLI `--streaming` for TTS")
                    run_system = gr.Textbox(label="System prompt", lines=3)
                    run_system_file = gr.File(label="System prompt file", file_types=[".txt"], type="filepath")
                    run_button = gr.Button("Run pipeline", variant="primary")
                    run_transcript = gr.Textbox(label="Transcript", lines=3)
                    run_answer = gr.Textbox(label="Answer", lines=5)
                    run_output = gr.Audio(label="Response WAV")
                    run_log = gr.Textbox(label="CLI output", lines=5, interactive=False, elem_classes="trident-status")
                    run_button.click(cli_run, [run_audio, run_output_file, run_streaming, run_system, run_system_file, *tts_inputs], [run_transcript, run_answer, run_output, run_log], concurrency_limit=None)

        with gr.Tab("Runtime"):
            with gr.Row(equal_height=False):
                with gr.Column(elem_classes="trident-deck"):
                    gr.Markdown("### `resident`")
                    resident_warm_streaming = gr.Checkbox(value=True, label="Warm streaming TTS configuration")
                    with gr.Row():
                        resident_status_button = gr.Button("Status")
                        resident_warm_button = gr.Button("Warm")
                        resident_stop_button = gr.Button("Stop all")
                    resident_output = gr.Textbox(label="Resident state", lines=8, interactive=False, elem_classes="trident-status")
                    resident_status_button.click(cli_resident_status, outputs=resident_output, concurrency_limit=None)
                    resident_warm_button.click(cli_resident_warm, [resident_warm_streaming, *tts_inputs], resident_output, concurrency_limit=None)
                    resident_stop_button.click(cli_resident_stop, outputs=resident_output, concurrency_limit=None)
                with gr.Column(elem_classes="trident-deck"):
                    gr.Markdown("### `install`")
                    install_family = gr.Dropdown(["all", *FAMILIES], value="all", label="Family")
                    install_button = gr.Button("Install / repair")
                    install_output = gr.Textbox(label="Installer output", lines=8, interactive=False, elem_classes="trident-status")
                    install_button.click(cli_install, install_family, install_output, concurrency_limit=None, show_progress="minimal")

    return demo.queue(default_concurrency_limit=None, max_size=32)


def launch(models_dir: Path | None = None, data_dir: Path | None = None) -> None:
    build(models_dir, data_dir).launch(server_name="127.0.0.1", server_port=7860, show_error=True, css=CSS)
