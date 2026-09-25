import os
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


def main():
    cfg = read_cfg()
    rate = int(knob(cfg, "vad.rate"))
    window = int(knob(cfg, "vad.window"))
    device = knob(cfg, "vad.device")
    which = int(device) if device.isdigit() else device
    native = int(sd.query_devices(which, "input")["default_samplerate"])
    block = int(round(window * native / rate))
    prompt = ROOT / knob(cfg, "ear.prompt-file")
    stop = ROOT / "vad.stop"
    pid = ROOT / "vad.pid"
    stop.unlink(missing_ok=True)
    pid.write_text(str(os.getpid()), encoding="ascii")
    torch.set_num_threads(1)
    model, utils = torch.hub.load(repo_or_dir="snakers4/silero-vad", model="silero_vad", trust_repo=True)
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
    positions = np.linspace(0, 1, window)
    with sd.InputStream(device=which, samplerate=native, channels=1, dtype="float32", blocksize=block) as stream:
        while not stop.exists():
            frame, _ = stream.read(block)
            mono = np.squeeze(frame).astype(np.float32)
            if len(mono) == window and native == rate:
                clip = mono
            else:
                clip = np.interp(positions * max(len(mono) - 1, 0), np.arange(len(mono)), mono).astype(np.float32)
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
            audio = np.concatenate(speech)
            speech = []
            index += 1
            wav = CHUNKS / f"utt-{index}.wav"
            pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
            with wave.open(str(wav), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(rate)
                handle.writeframes(pcm.tobytes())
            prompt.write_text(str(wav.relative_to(ROOT)).replace("\\", "/"), encoding="utf-8")
    pid.unlink(missing_ok=True)
    stop.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
