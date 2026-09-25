# Silero VAD on CPU. Language-agnostic. An utterance ends when the model reports speech end.
# The wav path is written to ear.prompt.txt. The recognizer stays idle until then.
# Knobs are trident.txt vad.* and they are VADIterator's own arguments.

import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
import torch

from trident_runtime import read_cfg

ROOT = Path(__file__).resolve().parents[1]
CHUNKS = ROOT / ".install" / "vad"


def knob(cfg, key):
    if key not in cfg:
        raise SystemExit("missing " + key)
    return cfg[key]


def listen(prompt: Path, stop, busy) -> None:
    cfg = read_cfg()
    rate = int(knob(cfg, "vad.rate"))
    window = int(knob(cfg, "vad.window"))
    torch.set_num_threads(1)
    model, utils = torch.hub.load(
        repo_or_dir="snakers4/silero-vad",
        model="silero_vad",
        trust_repo=True,
    )
    model.to("cpu")
    iterator = utils[3](
        model,
        threshold=float(knob(cfg, "vad.threshold")),
        sampling_rate=rate,
        min_silence_duration_ms=int(knob(cfg, "vad.min-silence-ms")),
        speech_pad_ms=int(knob(cfg, "vad.speech-pad-ms")),
    )
    CHUNKS.mkdir(parents=True, exist_ok=True)
    speech = []
    index = 0
    with sd.InputStream(samplerate=rate, channels=1, dtype="float32", blocksize=window) as stream:
        while not stop.is_set():
            frame, _ = stream.read(window)
            if busy.is_set():
                speech = []
                iterator.reset_states()
                continue
            clip = np.squeeze(frame).astype(np.float32)
            event = iterator(torch.from_numpy(clip), return_seconds=False)
            if event and "start" in event:
                speech = [clip]
                continue
            if not speech:
                continue
            speech.append(clip)
            if not (event and "end" in event):
                continue
            iterator.reset_states()
            if busy.is_set():
                speech = []
                continue
            audio = np.concatenate(speech)
            speech = []
            index += 1
            wav = CHUNKS / f"utt-{index}.wav"
            pcm = np.clip(audio, -1.0, 1.0)
            pcm = (pcm * 32767.0).astype(np.int16)
            with wave.open(str(wav), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(rate)
                handle.writeframes(pcm.tobytes())
            prompt.write_text(str(wav.relative_to(ROOT)).replace("\\", "/"), encoding="utf-8")
