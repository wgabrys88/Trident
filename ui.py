from __future__ import annotations

import argparse
from pathlib import Path

import gradio as gr
import numpy as np

from asr_live import LiveASR
from config import FAMILIES, REFERENCE_VOICES, SHARED_MODELS, TTS_RATE, Paths, resolve_voice
from installer import require_model, runtime_parakeet_library, write_text_atomic
from main import effective_family, finish, prepared_reference, resolved_tts, start_run, stream_synthesize, write_meta
from resident import stop as resident_stop


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
    if rate != 16000 and x.size:
        n = max(1, round(x.size * 16000 / rate))
        x = np.interp(np.linspace(0, x.size - 1, n), np.arange(x.size), x).astype(np.float32)
    return x.astype("<f4", copy=False).tobytes()


def _args(streaming: bool, join: str):
    return argparse.Namespace(
        n_gpu_layers=None, context=None, threads=None, seed=None, max_tokens=None, top_k=None,
        top_p=None, min_p=None, temperature=None, repeat_penalty=None, cfm_steps=None,
        cfg_weight=None, exaggeration=None, first_chunk_chars=None, chunk_chars=None,
        stream_first_chunk_tokens=None, stream_chunk_tokens=None, streaming=streaming, stream_join=join,
    )


def build(models_dir: Path | None = None, data_dir: Path | None = None):
    root = Paths(models_dir, data_dir)
    live = {"asr": None, "paths": None}

    def asr_start():
        resident_stop("parakeet")
        paths = start_run("ui-asr", root.models_dir, root.data_dir)
        worker = LiveASR(runtime_parakeet_library(), require_model(SHARED_MODELS["parakeet"], root.models_dir))
        worker.start()
        live.update(asr=worker, paths=paths)
        return "", "Listening"

    def asr_feed(audio):
        worker = live["asr"]
        if worker is None:
            return "", "Microphone is not active"
        text = worker.feed(_pcm16k(audio))
        paths = live["paths"]
        write_text_atomic(paths.transcript, text + ("\n" if text else ""))
        event = "End of utterance" if worker.eou else "Backchannel" if worker.eob else "Listening"
        return text, event

    def asr_stop():
        worker, paths = live["asr"], live["paths"]
        if worker is None:
            return "", "Stopped"
        text = worker.finish()
        write_text_atomic(paths.transcript, text + ("\n" if text else ""))
        write_meta(paths, command="ui-asr", transcript=paths.transcript, language_mode="auto")
        finish(paths)
        live.update(asr=None, paths=None)
        return text, "Stopped"

    def family_changed(name):
        family = FAMILIES[name]
        choices = list(family["TTS_LANGUAGES"])
        return gr.Dropdown(choices=choices, value=family["DEFAULT_REPLY_LANGUAGE"])

    def speak(text, family_name, language, voice, reference_file, streaming, join):
        text = str(text or "").strip()
        if not text:
            raise gr.Error("Text is empty")
        paths = start_run("ui-tts", root.models_dir, root.data_dir)
        family = effective_family(family_name, _args(bool(streaming), join))
        language = language if language in family["TTS_LANGUAGES"] else family["DEFAULT_REPLY_LANGUAGE"]
        reference = Path(reference_file).resolve() if reference_file else resolve_voice(root.data_dir, voice)
        reference = prepared_reference(reference, root.data_dir)
        if streaming:
            for raw in stream_synthesize(text, reference, paths.output, language, family, paths):
                yield (TTS_RATE, np.frombuffer(raw, dtype="<i2").copy())
        else:
            from main import synthesize
            source = paths.literal
            write_text_atomic(source, text + "\n")
            synthesize(source, reference, paths.output, language, family, paths)
            yield str(paths.output)
        write_meta(paths, command="ui-tts", family=family_name, language=language, output=paths.output, resolved_tts=resolved_tts(family), streaming=int(bool(streaming)), join=join)
        finish(paths)

    voices = list(REFERENCE_VOICES)
    family_default = next(iter(FAMILIES))
    with gr.Blocks(fill_width=True, title="Trident") as demo:
        with gr.Row(equal_height=False):
            with gr.Column(scale=1, min_width=260):
                mic = gr.Audio(sources=["microphone"], type="numpy", streaming=True, label="Live microphone")
                status = gr.Textbox(value="Stopped", label="ASR", interactive=False, container=False)
            with gr.Column(scale=2, min_width=300):
                text = gr.Textbox(label="Text", lines=6, max_lines=12, placeholder="Speak or type")
        with gr.Row(equal_height=False):
            family = gr.Dropdown(list(FAMILIES), value=family_default, label="Model")
            language = gr.Dropdown(list(FAMILIES[family_default]["TTS_LANGUAGES"]), value=FAMILIES[family_default]["DEFAULT_REPLY_LANGUAGE"], label="TTS language")
            voice = gr.Dropdown(voices, value=voices[0], label="Voice")
            reference = gr.Audio(sources=["upload", "microphone"], type="filepath", label="Custom voice", min_width=220)
        with gr.Row(equal_height=False):
            streaming = gr.Checkbox(value=True, label="Stream TTS")
            join = gr.Radio([("Chunks", "chunks"), ("Equal-power crossfade", "crossfade")], value="crossfade", label="Join")
            speak_button = gr.Button("Speak", variant="primary")
        output = gr.Audio(label="Output", streaming=True, autoplay=True)

        mic.start_recording(asr_start, outputs=[text, status], queue=True, concurrency_limit=1, concurrency_id="trident")
        mic.stream(asr_feed, inputs=mic, outputs=[text, status], time_limit=900, stream_every=0.25, concurrency_limit=1, concurrency_id="trident")
        mic.stop_recording(asr_stop, outputs=[text, status], queue=True, concurrency_limit=1, concurrency_id="trident")
        family.change(family_changed, family, language, queue=False)
        speak_button.click(speak, [text, family, language, voice, reference, streaming, join], output, concurrency_limit=1, concurrency_id="trident", show_progress="minimal")
    return demo.queue(default_concurrency_limit=1, max_size=8)


def launch(models_dir: Path | None = None, data_dir: Path | None = None) -> None:
    build(models_dir, data_dir).launch(server_name="127.0.0.1", server_port=7860, show_error=True)
