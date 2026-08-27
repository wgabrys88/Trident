from __future__ import annotations

import argparse
import io
import threading
import wave
from pathlib import Path

import gradio as gr
import numpy as np

from config import ASR_RATE, FAMILIES, LANGUAGES, LIVE_AUDIO, LIVE_SETTINGS, REFERENCE_VOICES, TTS_FIELDS, TTS_RATE, Paths, live_settings_path, load_live_settings, resolve_voice, save_live_settings
from conversation import Conversation
from main import effective_family, finish, prepared_reference, resident_report, resolved_tts, run_asr, run_brain, run_install, run_pipeline, run_tts, start_run, stream_synthesize, synthesize_text, tts_endpoint, tts_metrics, warm_resident, write_meta
from resident import stop_all as resident_stop_all

TTS_SETTING_KEYS = ["family", "language", "voice", "reference_mode", "reference", "join", *[field[0] for field in TTS_FIELDS]]
LIVE_SETTING_KEYS = [
    "ingestion_mode", "vad_threshold", "vad_silence_ms",
    "system_prompt", "tts_mode", "tts_family", "tts_language", "tts_voice", "tts_join",
]
CSS = """
.gradio-container {max-width: 1480px !important;}
.trident-hero {padding: 12px 2px 18px 2px;}
.trident-hero h1 {margin: 0; font-size: 1.55rem;}
.trident-hero p {margin: 4px 0 0 0; opacity: .70;}
.trident-card {border: 1px solid var(--border-color-primary); border-radius: 12px; padding: 10px;}
.trident-status textarea {font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace !important;}
"""

_sessions: dict[str, Conversation] = {}
_sessions_lock = threading.Lock()


def _wav_bytes(pcm16: bytes) -> bytes:
    if not pcm16:
        return b""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(TTS_RATE)
        output.writeframes(pcm16)
    return buffer.getvalue()


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


def _reference(root: Paths, settings: dict) -> Path:
    if settings["reference_mode"] == "custom":
        custom = settings.get("reference")
        if not custom:
            raise RuntimeError("record or upload a custom reference first")
        return Path(custom).resolve()
    return resolve_voice(root.data_dir, settings["voice"])


def _family_help(name: str) -> str:
    if name == "v3":
        return "**Multilingual V3:** Min P, CFG weight, and exaggeration are active model controls."
    return "**Turbo / Nano:** upstream uses Temperature, Top K, Top P, and repetition penalty. Min P, CFG weight, and exaggeration are unsupported and are hidden here."


def _session_id(request: gr.Request) -> str:
    if not request.session_hash:
        raise RuntimeError("Gradio session is unavailable")
    return request.session_hash


def _cleanup_session(session_id: str) -> None:
    with _sessions_lock:
        engine = _sessions.pop(session_id, None)
    if engine:
        engine.close()


def _mode_help(mode: str) -> str:
    if mode == "ptt":
        return "**Push to talk:** start the engine, record one complete utterance, then press **Stop** on the microphone. That recording is sent as one turn."
    return "**Hands-free:** the microphone stays open. Silero finds candidate pauses and Smart Turn decides whether the utterance is complete before ASR is dispatched."


def _tts_status(label: str, result: str) -> str:
    metrics = tts_metrics(result)
    return f"{label} · {metrics['audio_s']:.1f}s audio · RTF {metrics['rtf']:.3f} · {metrics['x_realtime']:.2f}× realtime"


def build(models_dir: Path | None = None, data_dir: Path | None = None):
    root = Paths(models_dir, data_dir)
    load_live_settings(root.data_dir)
    runs_dir = root.data_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    def _ns(**kwargs):
        kwargs.setdefault("models_dir", root.models_dir)
        kwargs.setdefault("data_dir", root.data_dir)
        return argparse.Namespace(**kwargs)

    def _tts_ns(settings, **extra):
        return _ns(
            family=settings["family"], language=settings["language"], tts_language=settings["language"],
            reference=str(_reference(root, settings)), stream_join=settings["join"],
            **{key: settings.get(key) for key, *_ in TTS_FIELDS}, **extra,
        )

    def family_changed(name):
        family = FAMILIES[name]
        multilingual = name == "v3"
        return (
            gr.Dropdown(choices=list(family["TTS_LANGUAGES"]), value=family["DEFAULT_REPLY_LANGUAGE"]),
            gr.Number(value=None, visible=multilingual),
            gr.Number(value=None, visible=multilingual),
            gr.Number(value=None, visible=multilingual),
            _family_help(name),
        )

    def custom_reference_changed(mode: str):
        return gr.Audio(value=None, visible=mode == "custom")

    def live_settings(values) -> dict:
        settings = dict(zip(LIVE_SETTING_KEYS, values, strict=True))
        settings["vad_threshold"] = float(settings["vad_threshold"])
        settings["vad_silence_ms"] = int(settings["vad_silence_ms"])
        if settings["tts_language"] not in FAMILIES[settings["tts_family"]]["TTS_LANGUAGES"]:
            raise RuntimeError(f"language {settings['tts_language']!r} is not wired in {settings['tts_family']}")
        return settings

    def save_config(request: gr.Request, *values):
        settings = live_settings(values)
        save_live_settings(root.data_dir, settings)
        with _sessions_lock:
            engine = _sessions.get(_session_id(request))
            if engine and engine.active:
                engine.configure(settings)
        return f"Saved to {live_settings_path(root.data_dir).name}"

    def start_conversation(request: gr.Request, *values):
        session_id = _session_id(request)
        settings = live_settings(values)
        save_live_settings(root.data_dir, settings)
        with _sessions_lock:
            previous = _sessions.pop(session_id, None)
        if previous:
            previous.close()
        engine = Conversation(root.models_dir, root.data_dir, settings)
        engine.start()
        with _sessions_lock:
            _sessions[session_id] = engine
        continuous = settings["ingestion_mode"] == "continuous"
        return (
            engine.transcript, engine.answer, engine.status,
            gr.Audio(value=None, visible=continuous, interactive=continuous, recording=continuous),
            gr.Audio(value=None, visible=not continuous, interactive=not continuous, recording=False),
            gr.Button(interactive=False), gr.Button(interactive=True),
            gr.Radio(interactive=False), gr.Button(interactive=True),
        )

    def conversation_pump(request: gr.Request):
        session_id = _session_id(request)
        with _sessions_lock:
            engine = _sessions.get(session_id)
        if engine is None:
            return
        unchanged_controls = (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip())
        stopped_controls = (
            gr.Audio(value=None, interactive=False, recording=False),
            gr.Audio(value=None, interactive=False, recording=False),
            gr.Button(interactive=True), gr.Button(interactive=False),
            gr.Radio(interactive=True), gr.Button(interactive=False),
        )
        while True:
            kind, payload = engine.next_output()
            audio = gr.skip()
            if kind == "audio-pcm":
                audio = _wav_bytes(payload)
            elif kind == "audio-file":
                audio = payload
            elif kind == "audio-reset":
                audio = gr.Audio(value=None)
            if kind == "error":
                _cleanup_session(session_id)
                yield engine.transcript, engine.answer, audio, engine.status, *stopped_controls
                raise RuntimeError(str(payload))
            if kind == "closed":
                yield engine.transcript, engine.answer, audio, engine.status, *stopped_controls
                return
            yield engine.transcript, engine.answer, audio, engine.status, *unchanged_controls

    def feed_conversation(audio, request: gr.Request):
        pcm_f32 = _pcm16k(audio)
        with _sessions_lock:
            engine = _sessions.get(_session_id(request))
            if engine is not None:
                engine.feed_audio(pcm_f32)

    def ptt_submit(audio, request: gr.Request):
        pcm_f32 = _pcm16k(audio)
        with _sessions_lock:
            engine = _sessions.get(_session_id(request))
            if engine is None:
                return gr.Audio(value=None, interactive=False, recording=False), "Stopped"
            engine.submit_audio(pcm_f32)
            return gr.Audio(value=None, interactive=True, recording=False), engine.status

    def manual_submit(text: str, request: gr.Request):
        with _sessions_lock:
            engine = _sessions.get(_session_id(request))
            if engine is None:
                raise RuntimeError("start the conversation engine before submitting a turn")
            engine.submit(str(text or ""))
        return ""

    def prepare_stop():
        return (
            gr.Audio(value=None, interactive=False, recording=False),
            gr.Audio(value=None, interactive=False, recording=False),
            gr.Button(interactive=False), gr.Button(interactive=False),
        )

    def stop_conversation(request: gr.Request):
        with _sessions_lock:
            engine = _sessions.pop(_session_id(request), None)
        if engine is not None:
            engine.stop()
        return "Stopped", gr.Button(interactive=True), gr.Radio(interactive=True)

    def cleanup_session(request: gr.Request):
        _cleanup_session(_session_id(request))

    def speak(text: str, mode: str, *values):
        text = str(text or "").strip()
        if not text:
            raise RuntimeError("text is empty")
        settings = _settings(values)
        family = effective_family(settings["family"], {**{key: settings.get(key) for key, *_ in TTS_FIELDS}, "streaming": mode == "real", "stream_join": settings["join"]})
        language = settings["language"]
        if language not in family["TTS_LANGUAGES"]:
            raise RuntimeError(f"language {language!r} is not wired in {family['name']}")
        paths = start_run("ui-tts", root.models_dir, root.data_dir)
        outcome = "aborted"
        try:
            yield gr.skip(), "Preparing synthesis"
            reference = prepared_reference(_reference(root, settings), root.data_dir)
            base = tts_endpoint(reference, language, family, paths)
            write_meta(
                paths, command="ui-tts", family=family["name"], language=language,
                resolved_tts=resolved_tts(family), streaming=int(mode == "real"),
                join=settings["join"], output=paths.output,
            )
            if mode == "buffered":
                yield gr.skip(), "Buffered · synthesizing"
                result = synthesize_text(text, reference, paths.output, language, family, paths, base=base, streaming=False)
                if not paths.output.is_file():
                    raise RuntimeError(f"Chatterbox did not create buffered output: {result}")
                outcome = "ok"
                yield str(paths.output), _tts_status("Buffered · complete", result)
                return

            yield gr.skip(), "Streaming · waiting for first speech unit"
            generator = stream_synthesize(text, reference, paths.output, language, family, paths, base=base)
            units = 0
            audio_samples = 0
            while True:
                try:
                    raw = next(generator)
                except StopIteration as done:
                    result = str(done.value or "")
                    if not result:
                        raise RuntimeError("resident TTS stream ended without a completion result")
                    outcome = "ok"
                    yield gr.skip(), _tts_status("Streaming · complete", result)
                    return
                units += 1
                audio_samples += len(raw) // 2
                yield _wav_bytes(raw), f"Streaming · speech unit {units} · {audio_samples / TTS_RATE:.1f}s audio ready"
        except Exception:
            outcome = "error"
            raise
        finally:
            finish(paths, outcome)

    def cli_tts(text: str, text_file: str | None, output_file: str, cli_streaming: bool, *values):
        text = str(text or "").strip()
        if bool(text) == bool(text_file):
            raise RuntimeError("provide exactly one TTS text source")
        output_file = str(output_file or "").strip() or None
        path = run_tts(_tts_ns(
            _settings(values),
            input=str(Path(text_file).resolve()) if text_file else None,
            text=text or None, output=output_file, streaming=bool(cli_streaming),
        ))
        return path, f"Output: {path}"

    def cli_asr(audio: str | None, output_file: str):
        if not audio:
            raise RuntimeError("ASR input audio is required")
        return run_asr(_ns(input=str(Path(audio).resolve()), output=str(output_file or "").strip() or None))

    def cli_brain(text: str, text_file: str | None, output_file: str, language: str, system_prompt: str, system_prompt_file: str | None):
        text = str(text or "").strip()
        if bool(text) == bool(text_file):
            raise RuntimeError("provide exactly one Brain text source")
        prompt, prompt_file = str(system_prompt or "").strip() or None, system_prompt_file
        if prompt and prompt_file:
            raise RuntimeError("choose one system prompt source")
        return run_brain(_ns(
            input=str(Path(text_file).resolve()) if text_file else None, text=text or None,
            output=str(output_file or "").strip() or None, language=language,
            system_prompt=prompt, system_prompt_file=prompt_file,
        ))

    def cli_run(audio: str | None, output_file: str, cli_streaming: bool, system_prompt: str, system_prompt_file: str | None, *values):
        if not audio:
            raise RuntimeError("pipeline input audio is required")
        prompt, prompt_file = str(system_prompt or "").strip() or None, system_prompt_file
        if prompt and prompt_file:
            raise RuntimeError("choose one system prompt source")
        output_file = str(output_file or "").strip() or None
        transcript, answer, output = run_pipeline(_tts_ns(
            _settings(values), input=str(Path(audio).resolve()), output=output_file,
            streaming=bool(cli_streaming), system_prompt=prompt, system_prompt_file=prompt_file,
        ))
        return transcript, answer, output, f"Transcript: {transcript}\nAnswer: {answer}\nOutput: {output}"

    def cli_resident_status():
        return resident_report()

    def cli_resident_stop():
        resident_stop_all()
        return resident_report()

    def cli_resident_warm(*values):
        warm_resident(_tts_ns(_settings(values)))
        return resident_report()

    def cli_install(family_name: str):
        return run_install(_ns(family=family_name))

    def selected_log(value) -> Path | None:
        if isinstance(value, list):
            value = value[0] if value else None
        if value:
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = runs_dir / candidate
            candidate = candidate.resolve()
            try:
                candidate.relative_to(runs_dir.resolve())
            except ValueError as exc:
                raise RuntimeError("selected log is outside the run directory") from exc
            if candidate.is_file():
                return candidate
        files = [path for path in runs_dir.glob("*/*-trident.log") if path.is_file()]
        return max(files, key=lambda path: path.stat().st_mtime_ns) if files else None

    def read_log(value=None) -> str:
        path = selected_log(value)
        return path.read_text(encoding="utf-8", errors="replace") if path else "No Trident run logs yet."

    voices = list(REFERENCE_VOICES)
    family_default = LIVE_SETTINGS["tts_family"]
    live_language_default = LIVE_SETTINGS["tts_language"]

    with gr.Blocks(fill_width=True, title="Trident", delete_cache=(86400, 86400)) as demo:
        gr.HTML("<div class='trident-hero'><h1>Trident</h1><p>Local multilingual speech pipeline · Parakeet TDT · Smart Turn · Gemma · Chatterbox.</p></div>")

        with gr.Sidebar(label="Speech settings", open=False, width=340):
            gr.Markdown("### Speech output")
            family = gr.Dropdown(list(FAMILIES), value=family_default, label="Chatterbox family")
            language = gr.Dropdown(list(FAMILIES[family_default]["TTS_LANGUAGES"]), value=live_language_default, label="Spoken language")
            voice = gr.Dropdown(voices, value=LIVE_SETTINGS["tts_voice"], label="Preset voice")
            join = gr.Radio([("Crossfade", "crossfade"), ("Separate chunks", "chunks")], value=LIVE_SETTINGS["tts_join"], label="Speech-unit join")
            gr.Markdown("Preset voice is used by Conversation. Manual TTS and CLI can instead use a temporary reference; recording one does not create a new preset.")
            reference_mode = gr.Radio([("Use preset", "preset"), ("Use custom reference", "custom")], value="preset", label="Manual / CLI voice source")
            reference = gr.Audio(sources=["upload", "microphone"], type="filepath", label="Custom voice reference", visible=False)
            with gr.Accordion("Manual / CLI engine overrides", open=False):
                gr.Markdown("Conversation uses the selected family defaults. These overrides apply to Manual TTS, CLI actions, and Runtime Warm. Blank values use family defaults; changing resident settings can restart Chatterbox.")
                family_note = gr.Markdown(_family_help(family_default))
                override_widgets = {
                    key: gr.Number(value=None, precision=0 if typ is int else None, label=label, visible=(not v3_only) or family_default == "v3")
                    for key, _, _, typ, _, label, v3_only in TTS_FIELDS
                }

        tts_inputs = [family, language, voice, reference_mode, reference, join, *[override_widgets[key] for key, *_ in TTS_FIELDS]]
        family.change(family_changed, family, [language, override_widgets["min_p"], override_widgets["cfg_weight"], override_widgets["exaggeration"], family_note], queue=False)
        reference_mode.change(custom_reference_changed, reference_mode, reference, queue=False)

        with gr.Tabs():
            with gr.Tab("Conversation"):
                with gr.Row(equal_height=False):
                    with gr.Column(scale=1, min_width=340, elem_classes="trident-card"):
                        ingestion = gr.Radio(
                            [("Hands-free · automatic turns", "continuous"), ("Push to talk · Record / Stop", "ptt")],
                            value=LIVE_SETTINGS["ingestion_mode"], label="Microphone mode",
                        )
                        mode_help = gr.Markdown(_mode_help(LIVE_SETTINGS["ingestion_mode"]))
                        handsfree_mic = gr.Audio(
                            sources=["microphone"], type="numpy", streaming=True, interactive=False,
                            visible=LIVE_SETTINGS["ingestion_mode"] == "continuous", label="Hands-free microphone",
                        )
                        ptt_mic = gr.Audio(
                            sources=["microphone"], type="numpy", streaming=False, interactive=False,
                            visible=LIVE_SETTINGS["ingestion_mode"] == "ptt", label="Push-to-talk microphone",
                        )
                        with gr.Row():
                            start_button = gr.Button("Start", variant="primary")
                            stop_button = gr.Button("Stop", interactive=False)
                        live_status = gr.Textbox(value="Stopped", label="Pipeline status", interactive=False, elem_classes="trident-status")
                    with gr.Column(scale=2, min_width=480, elem_classes="trident-card"):
                        transcript = gr.Textbox(label="Transcript", lines=6, interactive=False)
                        answer = gr.Textbox(label="Response", lines=7, interactive=False)
                        live_audio = gr.Audio(label="Spoken response", streaming=True, autoplay=True)

                with gr.Accordion("Conversation settings", open=False):
                    with gr.Row(equal_height=False):
                        with gr.Column():
                            with gr.Column(visible=LIVE_SETTINGS["ingestion_mode"] == "continuous") as vad_group:
                                gr.Markdown("**Hands-free turn detection.** Silero only proposes a pause; Smart Turn v3.2 makes the multilingual complete/incomplete decision on CPU. The default candidate pause matches Smart Turn's intended VAD handoff.")
                                vad_threshold = gr.Slider(0.1, 0.9, value=LIVE_SETTINGS["vad_threshold"], step=0.05, label="Silero speech threshold")
                                vad_silence = gr.Slider(100, 1500, value=LIVE_SETTINGS["vad_silence_ms"], step=20, label="Candidate silence · ms")
                        with gr.Column():
                            system_prompt = gr.Textbox(value=LIVE_SETTINGS["system_prompt"], label="System prompt", lines=7)
                            tts_mode = gr.Radio([("Stream speech units", "real"), ("Buffered WAV units", "buffered")], value=LIVE_SETTINGS["tts_mode"], label="Conversation TTS delivery")
                            manual_text = gr.Textbox(label="Manual turn", placeholder="Send text directly, or leave empty to finalize captured speech")
                            with gr.Row():
                                submit_button = gr.Button("Send turn", interactive=False)
                                save_button = gr.Button("Save settings")
                            config_status = gr.Textbox(value="", label="Configuration", interactive=False, elem_classes="trident-status")

                live_inputs = [ingestion, vad_threshold, vad_silence, system_prompt, tts_mode, family, language, voice, join]
                def mode_changed(mode: str):
                    return (
                        _mode_help(mode), gr.Column(visible=mode == "continuous"),
                        gr.Audio(visible=mode == "continuous", interactive=False, recording=False),
                        gr.Audio(visible=mode == "ptt", interactive=False, recording=False),
                    )

                ingestion.change(mode_changed, ingestion, [mode_help, vad_group, handsfree_mic, ptt_mic], queue=False)
                start_event = start_button.click(
                    start_conversation, live_inputs,
                    [transcript, answer, live_status, handsfree_mic, ptt_mic, start_button, stop_button, ingestion, submit_button],
                    concurrency_limit=None, show_progress="minimal",
                )
                start_event.then(
                    conversation_pump,
                    outputs=[transcript, answer, live_audio, live_status, handsfree_mic, ptt_mic, start_button, stop_button, ingestion, submit_button],
                    concurrency_limit=None, show_progress="hidden",
                )
                handsfree_mic.stream(feed_conversation, handsfree_mic, outputs=None, time_limit=LIVE_AUDIO["mic_time_limit_seconds"], stream_every=LIVE_AUDIO["asr_feed_seconds"], concurrency_limit=1, show_progress="hidden")
                ptt_mic.stop_recording(ptt_submit, ptt_mic, [ptt_mic, live_status], concurrency_limit=None, show_progress="minimal")
                submit_button.click(manual_submit, manual_text, manual_text, concurrency_limit=None, show_progress="minimal")
                save_button.click(save_config, live_inputs, config_status, concurrency_limit=None, show_progress="minimal")
                stop_button.click(prepare_stop, outputs=[handsfree_mic, ptt_mic, stop_button, submit_button], queue=False).then(
                    stop_conversation, outputs=[live_status, start_button, ingestion], concurrency_limit=None, show_progress="minimal"
                )

            with gr.Tab("TTS"):
                with gr.Row(equal_height=False):
                    with gr.Column(elem_classes="trident-card"):
                        manual_tts_text = gr.Textbox(label="Text", lines=10)
                        manual_tts_mode = gr.Radio([("Stream speech units", "real"), ("Buffered WAV", "buffered")], value="real", label="Delivery")
                        speak_button = gr.Button("Speak", variant="primary")
                    with gr.Column(elem_classes="trident-card"):
                        manual_output = gr.Audio(label="Output", streaming=True, autoplay=True)
                        manual_status = gr.Textbox(value="Idle", label="Synthesis status", interactive=False, elem_classes="trident-status")
                speak_button.click(speak, [manual_tts_text, manual_tts_mode, *tts_inputs], [manual_output, manual_status], concurrency_limit=None, show_progress="minimal")

            with gr.Tab("CLI"):
                gr.Markdown("This tab runs the same `main.py` command functions used by the CLI. Manual TTS above streams through the resident path directly.")
                with gr.Row(equal_height=False):
                    with gr.Column(elem_classes="trident-card"):
                        gr.Markdown("### ASR")
                        asr_file = gr.Audio(sources=["upload", "microphone"], type="filepath", label="Input audio")
                        asr_output_file = gr.Textbox(label="Optional transcript output path")
                        asr_file_button = gr.Button("Transcribe")
                        asr_file_text = gr.Textbox(label="Transcript", lines=6)
                        asr_file_button.click(cli_asr, [asr_file, asr_output_file], asr_file_text, concurrency_limit=None, show_progress="minimal")
                    with gr.Column(elem_classes="trident-card"):
                        gr.Markdown("### Brain")
                        brain_text = gr.Textbox(label="Text", lines=4)
                        brain_file = gr.File(label="Text file", file_types=[".txt"], type="filepath")
                        brain_output_file = gr.Textbox(label="Optional answer output path")
                        brain_language = gr.Dropdown(list(LANGUAGES), value="en", label="Language")
                        brain_system = gr.Textbox(label="System prompt", lines=3)
                        brain_system_file = gr.File(label="System prompt file", file_types=[".txt"], type="filepath")
                        brain_button = gr.Button("Run Brain")
                        brain_output = gr.Textbox(label="Answer", lines=7)
                        brain_button.click(cli_brain, [brain_text, brain_file, brain_output_file, brain_language, brain_system, brain_system_file], brain_output, concurrency_limit=None, show_progress="minimal")
                with gr.Row(equal_height=False):
                    with gr.Column(elem_classes="trident-card"):
                        gr.Markdown("### TTS")
                        cli_tts_text = gr.Textbox(label="Text", lines=5)
                        cli_tts_file = gr.File(label="Text file", file_types=[".txt"], type="filepath")
                        cli_tts_output_file = gr.Textbox(label="Optional WAV output path")
                        cli_tts_streaming = gr.Checkbox(value=False, label="Streaming delivery")
                        cli_tts_button = gr.Button("Render WAV")
                        cli_tts_audio = gr.Audio(label="Rendered WAV")
                        cli_tts_log = gr.Textbox(label="CLI output", lines=4, interactive=False, elem_classes="trident-status")
                        cli_tts_button.click(cli_tts, [cli_tts_text, cli_tts_file, cli_tts_output_file, cli_tts_streaming, *tts_inputs], [cli_tts_audio, cli_tts_log], concurrency_limit=None, show_progress="minimal")
                    with gr.Column(elem_classes="trident-card"):
                        gr.Markdown("### Full pipeline")
                        run_audio = gr.Audio(sources=["upload", "microphone"], type="filepath", label="Input audio")
                        run_output_file = gr.Textbox(label="Optional WAV output path")
                        run_streaming = gr.Checkbox(value=False, label="Streaming TTS delivery")
                        run_system = gr.Textbox(label="System prompt", lines=3)
                        run_system_file = gr.File(label="System prompt file", file_types=[".txt"], type="filepath")
                        run_button = gr.Button("Run ASR → Brain → TTS", variant="primary")
                        run_transcript = gr.Textbox(label="Transcript", lines=3)
                        run_answer = gr.Textbox(label="Answer", lines=5)
                        run_output = gr.Audio(label="Response WAV")
                        run_log = gr.Textbox(label="CLI output", lines=5, interactive=False, elem_classes="trident-status")
                        run_button.click(cli_run, [run_audio, run_output_file, run_streaming, run_system, run_system_file, *tts_inputs], [run_transcript, run_answer, run_output, run_log], concurrency_limit=None, show_progress="minimal")

            with gr.Tab("Runtime"):
                with gr.Row(equal_height=False):
                    with gr.Column(elem_classes="trident-card"):
                        gr.Markdown("### Resident models")
                        with gr.Row():
                            resident_status_button = gr.Button("Status")
                            resident_warm_button = gr.Button("Warm")
                            resident_stop_button = gr.Button("Stop all")
                        resident_output = gr.Textbox(label="Resident state", lines=8, interactive=False, elem_classes="trident-status")
                        resident_status_button.click(cli_resident_status, outputs=resident_output, concurrency_limit=None, show_progress="minimal")
                        resident_warm_button.click(cli_resident_warm, tts_inputs, resident_output, concurrency_limit=None, show_progress="minimal")
                        resident_stop_button.click(cli_resident_stop, outputs=resident_output, concurrency_limit=None, show_progress="minimal")
                    with gr.Column(elem_classes="trident-card"):
                        gr.Markdown("### Installation")
                        gr.Markdown("Existing validated model files are reused. Install/repair only downloads or converts a model when its expected artifact is missing or invalid.")
                        install_family = gr.Dropdown(["all", *FAMILIES], value="all", label="Family")
                        install_button = gr.Button("Install / repair")
                        install_output = gr.Textbox(label="Installer output", lines=8, interactive=False, elem_classes="trident-status")
                        install_button.click(cli_install, install_family, install_output, concurrency_limit=None, show_progress="minimal")

            with gr.Tab("Logs"):
                gr.Markdown("Run-owned logs stay on disk and are shown here directly. Leave the selector empty to follow the newest run; choose a file to inspect an older run.")
                with gr.Row(equal_height=False):
                    log_file = gr.FileExplorer(glob="**/*-trident.log", root_dir=runs_dir, file_count="single", label="Run logs", max_height=520)
                    log_view = gr.Code(value=read_log, language=None, label="Log", lines=28, max_lines=40, interactive=False, wrap_lines=True, show_line_numbers=False, buttons=["copy", "download"])
                log_timer = gr.Timer(1.0)
                log_timer.tick(read_log, log_file, log_view, queue=False, show_progress="hidden")
                log_file.change(read_log, log_file, log_view, queue=False, show_progress="hidden")

        demo.unload(cleanup_session)

    return demo.queue(default_concurrency_limit=None)


def launch(models_dir: Path | None = None, data_dir: Path | None = None) -> None:
    build(models_dir, data_dir).launch(server_name="127.0.0.1", server_port=7860, show_error=True, css=CSS)
