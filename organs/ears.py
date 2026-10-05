import json
import re
import unicodedata
import wave
from pathlib import Path

import numpy as np
import onnxruntime as ort

from organs import CONFIG, ROOT, path_of

CFG = CONFIG["ears"]
RATE = 16000
WINDOW = 512
LANG_LEAK = re.compile(r"\s*<[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?>\s*")


class Segmenter:
    def __init__(self):
        self.session = ort.InferenceSession(str(ROOT / CONFIG["paths"]["models"] / "silero_vad.onnx"), providers=["CPUExecutionProvider"])
        self.pad = RATE * CFG["pad_ms"] // 1000
        self.min_silence = RATE * CFG["min_silence_ms"] // 1000
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
            if self.silent_for >= self.min_silence:
                clip = np.concatenate(self.speech)
                self.talking = False
                self.speech = []
                self.lead = []
                self.state[:] = 0
                if clip.size >= self.min_len:
                    finished = clip
        return finished


def write_wav(path: Path, samples: np.ndarray, rate: int = RATE) -> Path:
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())
    return path


def ear_cmd(wav: Path) -> list[str]:
    exe = ROOT / CONFIG["paths"]["bin"] / "nemo-speech" / "bin" / "nemo-speech.exe"
    return [str(exe), "transcribe", str(wav), "--model", str(path_of("ears", "model")), "--device", "vulkan", "--format", "json", "--verbatim", "--quiet", "--endpointing=true", "--stop-history-eou-ms", "1200"]


def ear_text(raw: bytes) -> str:
    data = json.loads(raw.decode("utf-8", errors="replace"))
    return " ".join(unicodedata.normalize("NFC", LANG_LEAK.sub(" ", data["text"])).split())
