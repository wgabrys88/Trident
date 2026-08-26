from __future__ import annotations

import threading
from pathlib import Path

import gradio as gr

from agent import run as run_agent
from config import FAMILIES, LIVE_AUDIO, REFERENCE_VOICES, TTS_FIELDS, Paths, effective_family, live_settings_path, load_live_settings, resolved_tts, save_live_settings
from conversation import Conversation, prepared_reference, resident_report, stream_synthesize, synthesize_text, transcribe_pcm, tts_endpoint, tts_metrics
from log import finish, start_run, write_meta
from media import audio_pcm, encode_wav, pcm16_wav
from resident import stop_all as stop_residents

CSS = ".gradio-container{max-width:1480px!important}.trident-status textarea{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace!important}"
_sessions: dict[str, Conversation] = {}
_sessions_lock = threading.Lock()


def _session_id(request: gr.Request) -> str:
    if not request.session_hash:
        raise RuntimeError("Gradio session is unavailable")
    return request.session_hash


def _cleanup(session_id: str) -> None:
    with _sessions_lock:
        engine = _sessions.pop(session_id, None)
    if engine:
        engine.close()


def _tts_status(label: str, result: str) -> str:
    metrics = tts_metrics(result)
    return f"{label} · {metrics['audio_s']:.1f}s audio · RTF {metrics['rtf']:.3f} · {metrics['x_realtime']:.2f}× realtime"


def build(models_dir: Path | None = None, data_dir: Path | None = None):
    root = Paths(models_dir, data_dir)
    live = load_live_settings(root.data_dir)
    runs_dir = root.data_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    voice_keys = list(REFERENCE_VOICES)
    live_voice = live["tts_voice"] if live["tts_voice"] in REFERENCE_VOICES else "custom"
    live_reference = None if live_voice != "custom" else live["tts_voice"]

    def voice_value(choice: str, reference: str | None) -> str:
        if choice != "custom":
            return choice
        if not reference:
            raise RuntimeError("record or upload a custom reference")
        return str(encode_wav(Path(reference), root.data_dir / "custom-reference.wav", 24000, reuse=False))

    def settings(values) -> dict:
        ingestion_mode, vad_threshold, vad_silence_ms, system_prompt, tts_mode, family, language, voice, reference, join = values
        return {
            "ingestion_mode": ingestion_mode,
            "vad_threshold": vad_threshold,
            "vad_silence_ms": vad_silence_ms,
            "system_prompt": system_prompt,
            "tts_mode": tts_mode,
            "tts_family": family,
            "tts_language": language,
            "tts_voice": voice_value(voice, reference),
            "tts_join": join,
        }

    def save_config(request: gr.Request, *values):
        current = save_live_settings(root.data_dir, settings(values))
        with _sessions_lock:
            engine = _sessions.get(_session_id(request))
            if engine and engine.active:
                engine.configure(current)
        return f"Saved · {live_settings_path(root.data_dir).name}"

    def family_changed(name: str):
        family = FAMILIES[name]
        return gr.Dropdown(choices=list(family["TTS_LANGUAGES"]), value=family["DEFAULT_REPLY_LANGUAGE"])

    def voice_changed(choice: str):
        return gr.Audio(value=None, visible=choice == "custom")

    def mode_changed(mode: str):
        return "Hands-free · Silero + Smart Turn" if mode == "continuous" else "Push-to-talk · one recording per turn"

    def start_conversation(request: gr.Request, *values):
        session_id = _session_id(request)
        current = save_live_settings(root.data_dir, settings(values))
        _cleanup(session_id)
        engine = Conversation(root.models_dir, root.data_dir, current)
        engine.start()
        with _sessions_lock:
            _sessions[session_id] = engine
        continuous = current["ingestion_mode"] == "continuous"
        return (
            engine.transcript, engine.answer, engine.status,
            gr.Microphone(value=None, interactive=True, recording=continuous),
            gr.Button(interactive=False), gr.Button(interactive=True), gr.Radio(interactive=False), gr.Button(interactive=True),
        )

    def conversation_pump(request: gr.Request):
        session_id = _session_id(request)
        with _sessions_lock:
            engine = _sessions.get(session_id)
        if engine is None:
            return
        while True:
            kind, payload = engine.next_output()
            update = {transcript: engine.transcript, answer: engine.answer, live_status: engine.status}
            if kind == "audio-pcm":
                update[live_audio] = pcm16_wav(payload)
            elif kind == "audio-file":
                update[live_audio] = payload
            elif kind == "audio-reset":
                update[live_audio] = None
            if kind in {"error", "closed"}:
                update.update({
                    microphone: gr.Microphone(value=None, interactive=False, recording=False),
                    start_button: gr.Button(interactive=True), stop_button: gr.Button(interactive=False),
                    ingestion: gr.Radio(interactive=True), submit_button: gr.Button(interactive=False),
                })
                if kind == "error":
                    _cleanup(session_id)
                    yield update
                    raise RuntimeError(str(payload))
                yield update
                return
            yield update

    def feed_conversation(audio, request: gr.Request):
        with _sessions_lock:
            engine = _sessions.get(_session_id(request))
            if engine:
                engine.feed_audio(audio_pcm(audio))

    def recording_stopped(audio, request: gr.Request):
        with _sessions_lock:
            engine = _sessions.get(_session_id(request))
            if not engine:
                return gr.Microphone(value=None, interactive=False, recording=False), "Stopped"
            continuous = engine.settings["ingestion_mode"] == "continuous"
            if not continuous:
                engine.submit_audio(audio_pcm(audio))
            return gr.Microphone(value=None, interactive=True, recording=continuous), engine.status

    def submit_text(text: str, request: gr.Request):
        with _sessions_lock:
            engine = _sessions.get(_session_id(request))
            if not engine:
                raise RuntimeError("start Conversation first")
            engine.submit(text)
        return ""

    def submit_audio(audio, request: gr.Request):
        with _sessions_lock:
            engine = _sessions.get(_session_id(request))
            if not engine:
                raise RuntimeError("start Conversation first")
            engine.submit_audio(audio_pcm(audio))
        return None

    def stop_conversation(request: gr.Request):
        session_id = _session_id(request)
        with _sessions_lock:
            engine = _sessions.pop(session_id, None)
        if engine:
            engine.stop()
        return "Stopped", gr.Microphone(value=None, interactive=False, recording=False), gr.Button(interactive=True), gr.Button(interactive=False), gr.Radio(interactive=True), gr.Button(interactive=False)

    def speak(text: str, delivery: str, family_name: str, language: str, voice: str, reference: str | None, join: str, *overrides):
        text = str(text or "").strip()
        if not text:
            raise RuntimeError("text is empty")
        override = {field[0]: value for field, value in zip(TTS_FIELDS, overrides, strict=True)}
        override.update(streaming=delivery == "real", stream_join=join)
        family = effective_family(family_name, override)
        if language not in family["TTS_LANGUAGES"]:
            raise RuntimeError(f"language {language!r} is not wired in {family_name}")
        paths = start_run("tts", root.models_dir, root.data_dir)
        outcome = "error"
        try:
            ref = prepared_reference(Path(voice_value(voice, reference)) if voice == "custom" else root.data_dir / REFERENCE_VOICES[voice]["file"], root.data_dir)
            base = tts_endpoint(ref, language, family, paths)
            write_meta(paths, command="tts", family=family_name, language=language, resolved_tts=resolved_tts(family), delivery=delivery, join=join)
            if delivery == "buffered":
                result = synthesize_text(text, ref, paths.output, language, family, paths, base=base, streaming=False)
                if not paths.output.is_file():
                    raise RuntimeError(f"Chatterbox did not create output: {result}")
                outcome = "ok"
                yield str(paths.output), _tts_status("Complete", result)
                return
            generator = stream_synthesize(text, ref, paths.output, language, family, paths, base=base)
            while True:
                try:
                    yield pcm16_wav(next(generator)), "Streaming"
                except StopIteration as done:
                    result = str(done.value or "")
                    outcome = "ok"
                    yield gr.skip(), _tts_status("Complete", result)
                    return
        finally:
            finish(paths, outcome)

    def standalone_asr(audio):
        paths = start_run("asr", root.models_dir, root.data_dir)
        outcome = "error"
        try:
            text = transcribe_pcm(audio_pcm(audio), paths)
            outcome = "ok"
            return text
        finally:
            finish(paths, outcome)

    def self_test(prompts: str, expects: str):
        says = [line.strip() for line in prompts.splitlines() if line.strip()]
        patterns = [line.strip() for line in expects.splitlines()] if expects.strip() else None
        return run_agent(says, patterns, root.models_dir, root.data_dir)

    def runtime_status():
        return resident_report()

    def runtime_stop():
        stop_residents()
        return resident_report()

    def cleanup_session(request: gr.Request):
        _cleanup(_session_id(request))

    def read_log(selected=None):
        path = Path(selected) if selected else None
        if not path or not path.is_file():
            logs = sorted(runs_dir.glob("*/trident.log"), key=lambda item: item.stat().st_mtime_ns, reverse=True)
            path = logs[0] if logs else None
        return path.read_text(encoding="utf-8", errors="replace") if path else ""

    with gr.Blocks(title="Trident") as demo:
        gr.Markdown("# Trident\nLocal ASR → LLM → TTS with persistent residents. `python main.py` installs/repairs and opens this application.")
        with gr.Accordion("Live settings", open=True):
            with gr.Row():
                ingestion = gr.Radio([("Hands-free", "continuous"), ("Push-to-talk", "ptt")], value=live["ingestion_mode"], label="Input mode")
                family = gr.Dropdown(list(FAMILIES), value=live["tts_family"], label="TTS family")
                language = gr.Dropdown(list(FAMILIES[live["tts_family"]]["TTS_LANGUAGES"]), value=live["tts_language"], label="Language")
                voice = gr.Dropdown([*voice_keys, "custom"], value=live_voice, label="Voice")
                join = gr.Radio([("Crossfade", "crossfade"), ("Chunks", "chunks")], value=live["tts_join"], label="Join")
                tts_mode = gr.Radio([("Streaming", "real"), ("Buffered", "buffered")], value=live["tts_mode"], label="TTS")
            custom_reference = gr.Audio(value=live_reference, sources=["upload", "microphone"], type="filepath", label="Custom voice reference", visible=live_voice == "custom")
            mode_help = gr.Markdown(mode_changed(live["ingestion_mode"]))
            with gr.Row():
                vad_threshold = gr.Slider(0.1, 0.9, value=live["vad_threshold"], step=0.01, label="VAD threshold")
                vad_silence = gr.Slider(100, 1500, value=live["vad_silence_ms"], step=25, label="VAD silence ms")
            system_prompt = gr.Textbox(value=live["system_prompt"], lines=3, label="System prompt")
            save_button = gr.Button("Save settings")
            config_status = gr.Textbox(value=f"Loaded · {live_settings_path(root.data_dir).name}", interactive=False, show_label=False)
        live_inputs = [ingestion, vad_threshold, vad_silence, system_prompt, tts_mode, family, language, voice, custom_reference, join]
        family.change(family_changed, family, language, queue=False)
        voice.change(voice_changed, voice, custom_reference, queue=False)
        ingestion.change(mode_changed, ingestion, mode_help, queue=False)
        save_button.click(save_config, live_inputs, config_status, concurrency_limit=None)

        with gr.Tabs():
            with gr.Tab("Conversation"):
                with gr.Row():
                    with gr.Column():
                        microphone = gr.Microphone(type="numpy", streaming=True, interactive=False, recording=False, label="Microphone")
                        with gr.Row():
                            start_button = gr.Button("Start", variant="primary")
                            stop_button = gr.Button("Stop", interactive=False)
                        manual_text = gr.Textbox(label="Text turn", lines=2)
                        submit_button = gr.Button("Submit text", interactive=False)
                        file_turn = gr.Audio(sources=["upload"], type="numpy", label="Audio turn")
                        file_submit = gr.Button("Submit audio file")
                    with gr.Column():
                        transcript = gr.Textbox(label="Transcript", lines=7, interactive=False)
                        answer = gr.Textbox(label="Answer", lines=7, interactive=False)
                        live_audio = gr.Audio(label="Response", streaming=True, autoplay=True)
                        live_status = gr.Textbox(value="Stopped", label="Status", interactive=False, elem_classes="trident-status")
                start_event = start_button.click(start_conversation, live_inputs, [transcript, answer, live_status, microphone, start_button, stop_button, ingestion, submit_button], concurrency_limit=None)
                start_event.then(conversation_pump, outputs=[transcript, answer, live_audio, live_status, microphone, start_button, stop_button, ingestion, submit_button], concurrency_limit=None, show_progress="hidden")
                microphone.stream(feed_conversation, microphone, time_limit=LIVE_AUDIO["mic_time_limit_seconds"], stream_every=LIVE_AUDIO["asr_feed_seconds"], concurrency_limit=1, show_progress="hidden")
                microphone.stop_recording(recording_stopped, microphone, [microphone, live_status], concurrency_limit=None)
                submit_button.click(submit_text, manual_text, manual_text, concurrency_limit=None)
                file_submit.click(submit_audio, file_turn, file_turn, concurrency_limit=None)
                stop_button.click(stop_conversation, outputs=[live_status, microphone, start_button, stop_button, ingestion, submit_button], concurrency_limit=None)

            with gr.Tab("TTS"):
                tts_text = gr.Textbox(label="Text", lines=8)
                delivery = gr.Radio([("Streaming", "real"), ("Buffered", "buffered")], value="real", label="Delivery")
                with gr.Accordion("Advanced model controls", open=False):
                    overrides = []
                    for key, section, field, typ, label in TTS_FIELDS:
                        overrides.append(gr.Number(value=None, label=label))
                speak_button = gr.Button("Speak", variant="primary")
                tts_audio = gr.Audio(label="Output", streaming=True, autoplay=True)
                tts_status_box = gr.Textbox(value="Idle", interactive=False, label="Status", elem_classes="trident-status")
                speak_button.click(speak, [tts_text, delivery, family, language, voice, custom_reference, join, *overrides], [tts_audio, tts_status_box], concurrency_limit=None)

            with gr.Tab("Tools"):
                with gr.Row():
                    with gr.Column():
                        gr.Markdown("### ASR")
                        asr_audio = gr.Audio(sources=["upload", "microphone"], type="numpy", label="Audio")
                        asr_button = gr.Button("Transcribe")
                        asr_text = gr.Textbox(label="Transcript", lines=6)
                        asr_button.click(standalone_asr, asr_audio, asr_text, concurrency_limit=None)
                    with gr.Column():
                        gr.Markdown("### Self-test")
                        test_prompts = gr.Textbox(value="Install verification. Reply with one sentence.", label="TTS prompts, one per line", lines=4)
                        test_expects = gr.Textbox(value=".", label="Answer regexes, one per line", lines=4)
                        test_button = gr.Button("Run pipeline self-test")
                        test_output = gr.JSON(label="Result")
                        test_button.click(self_test, [test_prompts, test_expects], test_output, concurrency_limit=None)

            with gr.Tab("Runtime"):
                with gr.Row():
                    status_button = gr.Button("Resident status")
                    stop_resident_button = gr.Button("Stop residents")
                runtime_output = gr.Textbox(label="Residents", lines=8, interactive=False, elem_classes="trident-status")
                status_button.click(runtime_status, outputs=runtime_output, concurrency_limit=None)
                stop_resident_button.click(runtime_stop, outputs=runtime_output, concurrency_limit=None)

            with gr.Tab("Logs"):
                with gr.Row():
                    log_file = gr.FileExplorer(glob="**/trident.log", root_dir=runs_dir, file_count="single", label="Run logs", max_height=520)
                    log_view = gr.Code(value=read_log, language=None, label="Log", lines=28, max_lines=40, interactive=False, wrap_lines=True, show_line_numbers=False, buttons=["copy", "download"])
                timer = gr.Timer(1.0)
                timer.tick(read_log, log_file, log_view, queue=False, show_progress="hidden")
                log_file.change(read_log, log_file, log_view, queue=False, show_progress="hidden")

        demo.unload(cleanup_session)
    return demo.queue(default_concurrency_limit=None)


def launch(models_dir: Path | None = None, data_dir: Path | None = None) -> None:
    build(models_dir, data_dir).launch(server_name="127.0.0.1", server_port=7860, show_error=True, css=CSS)
