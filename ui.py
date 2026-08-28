from __future__ import annotations

import io
import threading
import wave
from pathlib import Path

import gradio as gr
import numpy as np

from config import ASR_FEED_SECONDS, ASR_RATE, MIC_TIME_LIMIT_SECONDS, TTS_RATE, Paths, load_live_settings
from conversation import Conversation


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


def build(models_dir: Path | None = None, data_dir: Path | None = None):
    root = Paths(models_dir, data_dir)
    settings = load_live_settings(root.data_dir)

    def _session_id(request: gr.Request) -> str:
        if not request.session_hash:
            raise RuntimeError("Gradio session is unavailable")
        return request.session_hash

    def _cleanup(session_id: str) -> None:
        with _sessions_lock:
            engine = _sessions.pop(session_id, None)
        if engine:
            engine.close()

    def start_conversation(request: gr.Request):
        session_id = _session_id(request)
        with _sessions_lock:
            previous = _sessions.pop(session_id, None)
        if previous:
            previous.close()
        engine = Conversation(root.models_dir, root.data_dir, settings)
        engine.start()
        with _sessions_lock:
            _sessions[session_id] = engine
        return (
            engine.transcript, engine.answer, engine.status,
            gr.Audio(value=None, interactive=True, recording=True),
            gr.Button(interactive=False), gr.Button(interactive=True),
        )

    def conversation_pump(request: gr.Request):
        session_id = _session_id(request)
        with _sessions_lock:
            engine = _sessions.get(session_id)
        if engine is None:
            return
        unchanged = (gr.skip(), gr.skip(), gr.skip())
        stopped = (
            gr.Audio(value=None, interactive=False, recording=False),
            gr.Button(interactive=True), gr.Button(interactive=False),
        )
        while True:
            event = engine.next_output()
            kind, payload, epoch = event.kind, event.payload, event.epoch
            audio_value = gr.skip()
            if kind == "audio-pcm":
                if epoch != engine.audio_epoch:
                    continue
                audio_value = _wav_bytes(payload)
            elif kind == "audio-reset":
                if epoch != engine.audio_epoch:
                    continue
                audio_value = gr.Audio(value=None, streaming=True, autoplay=True)
            if kind == "error":
                _cleanup(session_id)
                yield engine.transcript, engine.answer, audio_value, engine.status, *stopped
                raise RuntimeError(str(payload))
            if kind == "closed":
                yield engine.transcript, engine.answer, audio_value, engine.status, *stopped
                return
            yield engine.transcript, engine.answer, audio_value, engine.status, *unchanged

    def feed_conversation(audio, request: gr.Request):
        pcm_f32 = _pcm16k(audio)
        with _sessions_lock:
            engine = _sessions.get(_session_id(request))
            if engine is not None:
                engine.feed_audio(pcm_f32)

    def prepare_stop():
        return gr.Audio(value=None, interactive=False, recording=False), gr.Button(interactive=False)

    def stop_conversation(request: gr.Request):
        with _sessions_lock:
            engine = _sessions.pop(_session_id(request), None)
        if engine is not None:
            engine.stop()
        return "Stopped", gr.Button(interactive=True)

    def cleanup_session(request: gr.Request):
        _cleanup(_session_id(request))

    with gr.Blocks(fill_width=True, title="Trident") as demo:
        mic = gr.Audio(sources=["microphone"], type="numpy", streaming=True, interactive=False, label="Microphone")
        with gr.Row():
            start_button = gr.Button("Start", variant="primary")
            stop_button = gr.Button("Stop", interactive=False)
        live_status = gr.Textbox(value="Stopped", label="Status", interactive=False)
        transcript = gr.Textbox(label="You", lines=5, interactive=False)
        answer = gr.Textbox(label="Trident", lines=6, interactive=False)
        live_audio = gr.Audio(label="Speech", streaming=True, autoplay=True)
        start_event = start_button.click(
            start_conversation,
            outputs=[transcript, answer, live_status, mic, start_button, stop_button],
            concurrency_limit=None, show_progress="minimal",
        )
        start_event.then(
            conversation_pump,
            outputs=[transcript, answer, live_audio, live_status, mic, start_button, stop_button],
            concurrency_limit=None, show_progress="hidden",
        )
        mic.stream(
            feed_conversation, mic, outputs=None,
            time_limit=MIC_TIME_LIMIT_SECONDS, stream_every=ASR_FEED_SECONDS,
            concurrency_limit=1, show_progress="hidden",
        )
        stop_button.click(prepare_stop, outputs=[mic, stop_button], queue=False).then(
            stop_conversation, outputs=[live_status, start_button],
            concurrency_limit=None, show_progress="minimal",
        )
        demo.unload(cleanup_session)
    return demo.queue(default_concurrency_limit=None)


def launch(models_dir: Path | None = None, data_dir: Path | None = None) -> None:
    build(models_dir, data_dir).launch(server_name="127.0.0.1", server_port=7860, show_error=True)
