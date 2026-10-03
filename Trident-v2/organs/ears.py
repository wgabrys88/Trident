"""Ears: 16 kHz speech in, finished utterances and their text out.

  Segmenter   Silero VAD (ONNX, CPU). Feed float32 16 kHz audio from anywhere: the room cable or the phone call.
              Yields one numpy array per utterance, padded with a little lead-in.
  Microphone  The room. WASAPI capture of the VB-Audio cable through sounddevice, resampled to 16 kHz by Windows.
  transcribe  Nemotron ASR through nemo-speech.exe. Returns (text, language tag).

Run alone:  python -m organs.ears            -> listens to the room and prints what it hears
            python -m organs.ears file.wav   -> transcribes one file
"""

import json
import re
import subprocess
import sys
import threading
import unicodedata
import wave
from pathlib import Path
from typing import Callable

import numpy as np
import onnxruntime as ort
import sounddevice as sd

from organs import CONFIG, ROOT, log, path_of

LOG = log("ears")
CFG = CONFIG["ears"]
RATE = 16000
WINDOW = 512
LANG_LEAK = re.compile(r"\s*<[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?>\s*")


class Segmenter:
    """Stateful Silero gate. push(samples) -> utterance array or None."""

    def __init__(self):
        self.session = ort.InferenceSession(str(ROOT / CONFIG["paths"]["models"] / "silero_vad.onnx"), providers=["CPUExecutionProvider"])
        self.pad = RATE * CFG["pad_ms"] // 1000
        self.min_silence = RATE * CFG["min_silence_ms"] // 1000
        self.max_len = RATE * CFG["max_utterance_s"]
        self.min_len = int(RATE * CFG["min_utterance_s"])
        self.reset()

    def reset(self):
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros(WINDOW // 8, dtype=np.float32)
        self.pending = np.zeros(0, dtype=np.float32)
        self.lead: list[np.ndarray] = []
        self.speech: list[np.ndarray] = []
        self.talking = False
        self.silent_for = 0

    def _prob(self, hop: np.ndarray) -> float:
        audio = np.concatenate([self.context, hop])
        self.context = audio[-self.context.size :]
        prob, self.state = self.session.run(None, {"input": audio.reshape(1, -1), "state": self.state, "sr": np.array([RATE], dtype=np.int64)})
        return float(prob.reshape(-1)[0])

    def push(self, samples: np.ndarray) -> np.ndarray | None:
        self.pending = np.concatenate([self.pending, samples.astype(np.float32)])
        finished = None
        while self.pending.size >= WINDOW:
            hop, self.pending = self.pending[:WINDOW], self.pending[WINDOW:]
            prob = self._prob(hop)
            if not self.talking:
                self.lead.append(hop)
                self.lead = self.lead[-(self.pad // WINDOW + 1) :]
                if prob >= CFG["vad_threshold"]:
                    self.talking = True
                    self.silent_for = 0
                    self.speech = list(self.lead)
                continue
            self.speech.append(hop)
            self.silent_for = 0 if prob >= CFG["vad_threshold"] - 0.15 else self.silent_for + WINDOW
            total = len(self.speech) * WINDOW
            if self.silent_for >= self.min_silence or total >= self.max_len:
                clip = np.concatenate(self.speech)
                self.talking = False
                self.speech = []
                self.lead = []
                self.state[:] = 0
                if clip.size >= self.min_len:
                    finished = clip
        return finished


class Microphone:
    """Room capture. on_utterance(samples) is called from the capture thread for each finished utterance."""

    def __init__(self, on_utterance: Callable[[np.ndarray], None]):
        self.on_utterance = on_utterance
        self.segmenter = Segmenter()
        self.muted = False
        self.stream = None

    @staticmethod
    def device_index() -> int:
        wasapi = next(i for i, api in enumerate(sd.query_hostapis()) if "WASAPI" in api["name"])
        for index, dev in enumerate(sd.query_devices()):
            if dev["hostapi"] == wasapi and dev["max_input_channels"] > 0 and CFG["device"].lower() in dev["name"].lower():
                return index
        raise LookupError(f"no WASAPI capture device containing {CFG['device']!r}")

    def _callback(self, indata, frames, time_info, status):
        if self.muted:
            self.segmenter.reset()
            return
        clip = self.segmenter.push(indata[:, 0].copy())
        if clip is not None:
            self.on_utterance(clip)

    def start(self):
        self.stream = sd.InputStream(
            device=self.device_index(), samplerate=RATE, channels=1, dtype="float32", blocksize=WINDOW,
            extra_settings=sd.WasapiSettings(auto_convert=True), callback=self._callback,
        )
        self.stream.start()
        LOG.info("room microphone open: %s", sd.query_devices(self.device_index())["name"])

    def stop(self):
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None


def write_wav(path: Path, samples: np.ndarray, rate: int = RATE) -> Path:
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())
    return path


def transcribe(wav: Path) -> tuple[str, str]:
    """-> (text, language). Language is the ASR tag, or 'pl' when Polish letters appear."""
    exe = ROOT / CONFIG["paths"]["bin"] / "nemo-speech" / "bin" / "nemo-speech.exe"
    done = subprocess.run(
        [str(exe), "transcribe", str(wav), "--model", str(path_of("ears", "model")), "--device", "cpu", "--format", "json", "--verbatim", "--quiet", "--endpointing=true", "--stop-history-eou-ms", "1200"],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr.decode("utf-8", errors="replace").strip() or f"nemo-speech exit {done.returncode}")
    data = json.loads(done.stdout.decode("utf-8", errors="replace"))
    text = " ".join(unicodedata.normalize("NFC", LANG_LEAK.sub(" ", data["text"])).split())
    language = str(data.get("language") or "").split("-")[0].lower()
    if any(ch in "ąćęłńóśźż" for ch in text.lower()):
        language = "pl"
    return text, language


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(transcribe(Path(sys.argv[1])))
        raise SystemExit
    stop = threading.Event()

    def heard(clip):
        path = write_wav(ROOT / "state" / "room.wav", clip)
        print(transcribe(path))

    mic = Microphone(heard)
    mic.start()
    print("listening to the room, Ctrl+C to stop")
    try:
        stop.wait()
    except KeyboardInterrupt:
        mic.stop()
