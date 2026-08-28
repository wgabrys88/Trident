from __future__ import annotations

import io
import threading
import wave
from pathlib import Path

import gradio as gr
import numpy as np

from config import ASR_RATE, FAMILIES, LIVE_AUDIO, TTS_RATE, Paths, list_voices, live_settings_path, load_live_settings, resolve_voice, save_live_settings
from conversation import Conversation
from main import effective_family, finish, prepared_reference, resolved_tts, run_install, save_voice, start_run, stream_synthesize, synthesize_text, transcribe_file, tts_endpoint, tts_metrics, write_meta

TTS_SETTING_KEYS = ["family", "language", "voice"]
LIVE_SETTING_KEYS = [
    "ingestion_mode", "vad_threshold", "vad_silence_ms",
    "system_prompt", "tts_family", "tts_language", "tts_voice",
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


def _in_flight(engine: Conversation | None) -> bool:
    return bool(
        engine and engine.active and engine.turn >= 1
        and engine.turn > engine.tts_done_through and engine.turn > engine.cancelled_through
    )


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
    return "**Hands-free:** the microphone stays open. Silero marks pauses for ASR. Gemma decides whether to speak or keep listening."


def _tts_status(label: str, result: str) -> str:
    metrics = tts_metrics(result)
    return f"{label} · {metrics['audio_s']:.1f}s audio · RTF {metrics['rtf']:.3f} · {metrics['x_realtime']:.2f}× realtime"


def build(models_dir: Path | None = None, data_dir: Path | None = None):
    root = Paths(models_dir, data_dir)
    live = load_live_settings(root.data_dir)

    def family_changed(name):
        family = FAMILIES[name]
        return gr.Dropdown(choices=list(family["TTS_LANGUAGES"]), value=family["DEFAULT_REPLY_LANGUAGE"])

    def live_settings(values) -> dict:
        settings = dict(zip(LIVE_SETTING_KEYS, values, strict=True))
        settings["vad_threshold"] = float(settings["vad_threshold"])
        settings["vad_silence_ms"] = int(settings["vad_silence_ms"])
        if settings["tts_language"] not in FAMILIES[settings["tts_family"]]["TTS_LANGUAGES"]:
            raise RuntimeError(f"language {settings['tts_language']!r} is not wired in {settings['tts_family']}")
        return settings

    def _load_speech(settings: dict) -> None:
        family = effective_family(settings["tts_family"])
        tts_endpoint(
            prepared_reference(resolve_voice(root.data_dir, settings["tts_voice"]), root.data_dir),
            settings["tts_language"], family, root,
        )

    def save_config(request: gr.Request, *values):
        settings = live_settings(values)
        save_live_settings(root.data_dir, settings)
        with _sessions_lock:
            engine = _sessions.get(_session_id(request))
            busy = _in_flight(engine)
            if engine and engine.active:
                engine.configure(settings)
        if not busy:
            _load_speech(settings)
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
            event = engine.next_output()
            kind, payload = event.kind, event.payload
            audio = gr.skip()
            if kind == "audio-pcm":
                audio = _wav_bytes(payload)
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

    def speak(text: str, *values):
        text = str(text or "").strip()
        if not text:
            raise RuntimeError("text is empty")
        settings = _settings(values)
        family = effective_family(settings["family"])
        language = settings["language"]
        if language not in family["TTS_LANGUAGES"]:
            raise RuntimeError(f"language {language!r} is not wired in {family['name']}")
        paths = start_run("ui-tts", root.models_dir, root.data_dir)
        outcome = "aborted"
        try:
            yield gr.skip(), "Preparing synthesis"
            reference = prepared_reference(resolve_voice(root.data_dir, settings["voice"]), root.data_dir)
            base = tts_endpoint(reference, language, family, paths)
            write_meta(
                paths, command="ui-tts", family=family["name"], language=language,
                resolved_tts=resolved_tts(family), output=paths.output,
            )
            yield gr.skip(), "Streaming · waiting for first speech unit"
            units = 0
            audio_samples = 0
            for raw in stream_synthesize(text, reference, paths.output, language, family, paths, base=base):
                units += 1
                audio_samples += len(raw) // 2
                yield _wav_bytes(raw), f"Streaming · speech unit {units} · {audio_samples / TTS_RATE:.1f}s audio ready"
            outcome = "ok"
            yield gr.skip(), f"Streaming · complete · {units} units · {audio_samples / TTS_RATE:.1f}s audio"
            return
        except Exception:
            outcome = "error"
            raise
        finally:
            finish(paths, outcome)

    def cli_install():
        return run_install(root.models_dir, root.data_dir)

    def save_clone(name: str, audio, request: gr.Request):
        if not audio:
            raise RuntimeError("record or upload a voice first")
        slug = save_voice(root.data_dir, name, Path(audio).resolve())
        settings = load_live_settings(root.data_dir)
        settings["tts_voice"] = slug
        save_live_settings(root.data_dir, settings)
        with _sessions_lock:
            engine = _sessions.get(request.session_hash) if request.session_hash else None
            busy = _in_flight(engine)
            if engine and engine.active:
                engine.configure(settings)
        if not busy:
            _load_speech(settings)
        return f"Saved voice {slug}", gr.Dropdown(choices=list_voices(root.data_dir), value=slug)

    def asr_file(audio):
        if not audio:
            raise RuntimeError("provide an audio file")
        paths = start_run("ui-asr", root.models_dir, root.data_dir)
        outcome = "error"
        try:
            yield "", "Converting to 16 kHz mono"
            text = ""
            for kind, text, chunks, duration, extra in transcribe_file(Path(audio).resolve(), paths):
                if kind == "progress":
                    yield text, f"{extra:.0f}s / {duration:.0f}s · {chunks} chunks"
                else:
                    write_meta(paths, command="ui-asr", transcript=paths.transcript, chunks=chunks, duration_s=f"{duration:.3f}")
                    outcome = "ok"
                    yield text, f"Done · {duration:.1f}s · {chunks} chunks · {extra:.2f}× realtime · {paths.transcript}"
        finally:
            finish(paths, outcome)

    voices = list_voices(root.data_dir)
    family_default = live["tts_family"]
    live_language_default = live["tts_language"]
    voice_default = live["tts_voice"]
    if voice_default not in voices:
        voices.append(voice_default)

    with gr.Blocks(fill_width=True, title="Trident", delete_cache=(86400, 86400)) as demo:
        gr.HTML("<div class='trident-hero'><h1>Trident</h1><p>Clone a voice. Talk over each other. Transcribe hours of audio. Local Parakeet, Gemma, Chatterbox.</p></div>")

        with gr.Sidebar(label="Speech settings", open=False, width=340):
            gr.Markdown("### Speech output")
            family = gr.Dropdown(list(FAMILIES), value=family_default, label="Chatterbox family")
            language = gr.Dropdown(list(FAMILIES[family_default]["TTS_LANGUAGES"]), value=live_language_default, label="Spoken language")
            voice = gr.Dropdown(voices, value=voice_default, label="Voice")
            gr.Markdown("The voice is the speaking identity, including voices you clone. v3 speaks any wired language in that voice. Nano and turbo are English only. Changing family or voice replaces the one loaded Chatterbox process.")
            gr.Markdown("### Installation")
            gr.Markdown("Existing validated model files are reused. Install/repair only downloads or converts a model when its expected artifact is missing or invalid.")
            install_button = gr.Button("Install / repair")
            install_output = gr.Textbox(label="Installer output", lines=8, interactive=False, elem_classes="trident-status")
            install_button.click(cli_install, outputs=install_output, concurrency_limit=None, show_progress="minimal")

        tts_inputs = [family, language, voice]
        family.change(family_changed, family, language, queue=False)

        with gr.Tabs():
            with gr.Tab("Clone"):
                with gr.Row(equal_height=False):
                    with gr.Column(elem_classes="trident-card"):
                        clone_name = gr.Textbox(label="Voice name", placeholder="my-voice")
                        clone_audio = gr.Audio(sources=["microphone", "upload"], type="filepath", label="Recording or file · at least 5 seconds")
                        clone_button = gr.Button("Save voice", variant="primary")
                    with gr.Column(elem_classes="trident-card"):
                        clone_status = gr.Textbox(value="Record yourself, name it, save. Conversation and TTS then speak as you.", label="Clone status", interactive=False, elem_classes="trident-status")
                clone_button.click(save_clone, [clone_name, clone_audio], [clone_status, voice], concurrency_limit=None, show_progress="minimal")

            with gr.Tab("Conversation"):
                with gr.Row(equal_height=False):
                    with gr.Column(scale=1, min_width=340, elem_classes="trident-card"):
                        ingestion = gr.Radio(
                            [("Hands-free · automatic turns", "continuous"), ("Push to talk · Record / Stop", "ptt")],
                            value=live["ingestion_mode"], label="Microphone mode",
                        )
                        mode_help = gr.Markdown(_mode_help(live["ingestion_mode"]))
                        handsfree_mic = gr.Audio(
                            sources=["microphone"], type="numpy", streaming=True, interactive=False,
                            visible=live["ingestion_mode"] == "continuous", label="Hands-free microphone",
                        )
                        ptt_mic = gr.Audio(
                            sources=["microphone"], type="numpy", streaming=False, interactive=False,
                            visible=live["ingestion_mode"] == "ptt", label="Push-to-talk microphone",
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
                            with gr.Column(visible=live["ingestion_mode"] == "continuous") as vad_group:
                                gr.Markdown("**Hands-free listening.** Silero marks pauses; each segment is transcribed and stored. Gemma decides whether to speak or keep listening.")
                                vad_threshold = gr.Slider(0.1, 0.9, value=live["vad_threshold"], step=0.05, label="Silero speech threshold")
                                vad_silence = gr.Slider(100, 1500, value=live["vad_silence_ms"], step=20, label="Candidate silence · ms")
                        with gr.Column():
                            system_prompt = gr.Textbox(value=live["system_prompt"], label="System prompt", lines=7)
                            manual_text = gr.Textbox(label="Manual turn", placeholder="Send text directly, or leave empty to finalize captured speech")
                            with gr.Row():
                                submit_button = gr.Button("Send turn", interactive=False)
                                save_button = gr.Button("Save settings")
                            config_status = gr.Textbox(value="", label="Configuration", interactive=False, elem_classes="trident-status")

                live_inputs = [ingestion, vad_threshold, vad_silence, system_prompt, family, language, voice]
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

            with gr.Tab("ASR"):
                with gr.Row(equal_height=False):
                    with gr.Column(elem_classes="trident-card"):
                        asr_audio = gr.Audio(sources=["upload"], type="filepath", label="Audio file · hours are fine, 30-second overlapping chunks")
                        asr_button = gr.Button("Transcribe", variant="primary")
                    with gr.Column(elem_classes="trident-card"):
                        asr_text = gr.Textbox(label="Transcript", lines=18)
                        asr_status = gr.Textbox(value="Idle", label="ASR status", interactive=False, elem_classes="trident-status")
                asr_button.click(asr_file, asr_audio, [asr_text, asr_status], concurrency_limit=None, show_progress="minimal")

            with gr.Tab("TTS"):
                with gr.Row(equal_height=False):
                    with gr.Column(elem_classes="trident-card"):
                        manual_tts_text = gr.Textbox(label="Text", lines=10)
                        speak_button = gr.Button("Speak", variant="primary")
                    with gr.Column(elem_classes="trident-card"):
                        manual_output = gr.Audio(label="Output", streaming=True, autoplay=True)
                        manual_status = gr.Textbox(value="Idle", label="Synthesis status", interactive=False, elem_classes="trident-status")
                speak_button.click(speak, [manual_tts_text, *tts_inputs], [manual_output, manual_status], concurrency_limit=None, show_progress="minimal")

        demo.unload(cleanup_session)

    return demo.queue(default_concurrency_limit=None)


def launch(models_dir: Path | None = None, data_dir: Path | None = None) -> None:
    build(models_dir, data_dir).launch(server_name="127.0.0.1", server_port=7860, show_error=True, css=CSS)
