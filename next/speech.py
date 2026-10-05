import contextlib
import sys
import wave
import numpy as np
import onnxruntime as ort
from next import BIN, CONFIG, MODELS, ROOT

RATE, WINDOW = 16000, 512


class Segmenter:
    def __init__(self):
        self.session = ort.InferenceSession(str(MODELS / "silero_vad.onnx"), providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self):
        self.state = np.zeros((2, 1, 128), np.float32)
        self.context = np.zeros(64, np.float32)
        self.pending = np.zeros(0, np.float32)
        self.lead, self.speech, self.silence = [], [], 0

    def push(self, pcm):
        cfg = CONFIG["ears"]
        self.pending = np.concatenate((self.pending, np.frombuffer(pcm, np.int16).astype(np.float32) / 32768))
        while self.pending.size >= WINDOW:
            hop, self.pending = self.pending[:WINDOW], self.pending[WINDOW:]
            audio = np.concatenate((self.context, hop))
            self.context = audio[-64:]
            prob, self.state = self.session.run(None, {"input": audio[None, :], "state": self.state,
                                                       "sr": np.array(RATE, np.int64)})
            speaking = float(prob.flat[0]) >= cfg["vad_threshold"]
            if not self.speech:
                self.lead.append(hop)
                self.lead = self.lead[-(RATE * cfg["pad_ms"] // 1000 // WINDOW + 1):]
                if speaking:
                    self.speech, self.lead = self.lead, []
                continue
            self.speech.append(hop)
            self.silence = 0 if speaking else self.silence + WINDOW
            if self.silence >= RATE * cfg["min_silence_ms"] // 1000:
                clip = np.concatenate(self.speech)
                self.speech, self.silence = [], 0
                if clip.size >= RATE * cfg["min_utterance_s"]:
                    yield clip

    def finish(self):
        clip = np.concatenate(self.speech + [self.pending]) if self.speech else None
        self.reset()
        return clip


def transcription_command(path, samples):
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        output.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
    return [str(BIN / "nemo-speech" / "bin" / "nemo-speech.exe"), "transcribe", str(path), "--model", str(MODELS / CONFIG["ears"]["model"]),
            "--device", "vulkan", "--format", "json", "--verbatim", "--quiet"]


if __name__ == "__main__":
    cfg = CONFIG["mouth"]
    words = sys.stdin.buffer.read().decode("utf-8").split()
    with contextlib.redirect_stdout(sys.stderr):
        from chatterbox.tts_turbo import ChatterboxTurboTTS
        model = ChatterboxTurboTTS.from_local(MODELS / cfg["model"], cfg["device"])
        model.prepare_conditionals(str(ROOT / cfg["reference"]))
        clips = [model.generate(" ".join(words[i:i + 40])).squeeze().cpu().numpy() for i in range(0, len(words), 40)]
    if clips:
        samples = np.concatenate(clips)
        count = round(samples.size * 48000 / model.sr)
        samples = np.interp(np.arange(count) * model.sr / 48000, np.arange(samples.size), samples)
        sys.stdout.buffer.write((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
