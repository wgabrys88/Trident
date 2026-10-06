import contextlib, sys, wave
import numpy as np
import onnxruntime as ort
from core import BIN, CONFIG, MODELS, ROOT

RATE, WINDOW = 16000, 512

class Segmenter:
    def __init__(self):
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(MODELS / "silero_vad.onnx"), options, providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self):
        self.state, self.context = np.zeros((2, 1, 128), np.float32), np.zeros(64, np.float32)
        self.pending, self.lead, self.speech, self.silence = np.zeros(0, np.float32), [], [], 0

    def push(self, pcm):
        cfg = CONFIG["ears"]
        self.pending = np.concatenate((self.pending, np.frombuffer(pcm, np.int16).astype(np.float32) / 32768))
        while self.pending.size >= WINDOW:
            hop, self.pending = self.pending[:WINDOW], self.pending[WINDOW:]
            audio = np.concatenate((self.context, hop))
            self.context = audio[-64:]
            probability, self.state = self.session.run(None, {"input": audio[None, :], "state": self.state,
                                                             "sr": np.array(RATE, np.int64)})
            speaking = float(probability.flat[0]) >= cfg["vad_threshold"]
            if not self.speech:
                self.lead = (self.lead + [hop])[-(RATE * cfg["pad_ms"] // 1000 // WINDOW + 1):]
                if speaking:
                    self.speech, self.lead = self.lead, []
            else:
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

def transcription(path, samples):
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        output.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
    return [BIN / "nemo-speech" / "bin" / "nemo-speech.exe", "transcribe", path, "--model",
            MODELS / CONFIG["ears"]["model"], "--device", "vulkan", "--format", "json", "--verbatim", "--quiet"]

if __name__ == "__main__":
    words, cfg = sys.stdin.buffer.read().decode("utf-8"), CONFIG["mouth"]
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        from chatterbox.tts_turbo import ChatterboxTurboTTS, Conditionals
        from transformers.initialization import no_init_weights
        torch.set_num_threads(CONFIG["brain"]["threads"])
        with torch.inference_mode(), no_init_weights():
            model = ChatterboxTurboTTS.from_local(MODELS / cfg["model"], cfg["device"])
        with torch.inference_mode():
            voice = ROOT / sys.argv[1]
            if voice.exists():
                model.conds = Conditionals.load(voice, map_location="cpu").to(cfg["device"])
            else:
                model.prepare_conditionals(str(ROOT / cfg["reference"]))
                model.conds.save(voice)
            samples = model.generate(words).squeeze().cpu().numpy()
    count = round(samples.size * 48000 / model.sr)
    samples = np.interp(np.arange(count) * model.sr / 48000, np.arange(samples.size), samples)
    sys.stdout.buffer.write((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
