# Silero VAD on CPU. Language-agnostic. An utterance ends when the model reports speech end.
# The wav path is written to ear.prompt.txt. The recognizer stays idle until then.

import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
import torch

ROOT = Path(__file__).resolve().parents[1]
CHUNKS = ROOT / ".install" / "vad"
RATE = 16000
WINDOW = 512


def listen(prompt: Path, stop, busy) -> None:
    torch.set_num_threads(1)
    model, utils = torch.hub.load(
        repo_or_dir="snakers4/silero-vad",
        model="silero_vad",
        trust_repo=True,
    )
    model.to("cpu")
    iterator = utils[3](model, sampling_rate=RATE, min_silence_duration_ms=800)
    CHUNKS.mkdir(parents=True, exist_ok=True)
    speech = []
    index = 0
    with sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=WINDOW) as stream:
        while not stop.is_set():
            frame, _ = stream.read(WINDOW)
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
                handle.setframerate(RATE)
                handle.writeframes(pcm.tobytes())
            prompt.write_text(str(wav.relative_to(ROOT)).replace("\\", "/"), encoding="utf-8")
