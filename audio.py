import io, sys, wave
import numpy as np
import onnxruntime as ort
from core import BIN, CONFIG, MODELS, ROOT

RATE, WINDOW, WORDS, CAP = 16000, 512, 70, 30 * 48000

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

def chunks(text):
    words = text.split()
    if not words:
        return []
    pieces, sentence = [], []
    for word in words:
        sentence.append(word)
        if word[-1] in ".!?" or len(sentence) == WORDS:
            pieces.append(" ".join(sentence))
            sentence = []
    if sentence:
        pieces.append(" ".join(sentence))
    groups, current, count = [], [], 0
    for piece in pieces:
        size = len(piece.split())
        if current and (len(current) == 3 or count + size > WORDS):
            groups.append(" ".join(current))
            current, count = [], 0
        current.append(piece)
        count += size
    return groups + ([" ".join(current)] if current else [])

def ogg(pcm):
    import av
    samples = np.frombuffer(pcm, np.int16)
    buffer = io.BytesIO()
    with av.open(buffer, "w", format="ogg") as container:
        stream = container.add_stream("libopus", rate=48000)
        stream.layout = "mono"
        for offset in range(0, max(samples.size, 1), 960):
            frame_samples = np.zeros(960, np.int16)
            end = min(960, max(samples.size - offset, 0))
            if end:
                frame_samples[:end] = samples[offset:offset + end]
            frame = av.AudioFrame.from_ndarray(frame_samples.reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate = 48000
            frame.pts = offset
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buffer.getvalue()

def render(samples, source):
    count = min(CAP, round(samples.size * 48000 / source)) if samples.size else 0
    if not count:
        return b""
    audio = np.interp(np.arange(count) * source / 48000, np.arange(samples.size), samples)
    return (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()

def transcription(path, samples):
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        output.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
    return [BIN / "nemo-speech" / "bin" / "nemo-speech.exe", "transcribe", path, "--model",
            MODELS / CONFIG["ears"]["model"], "--device", "vulkan", "--format", "json", "--verbatim", "--quiet"]

if __name__ == "__main__":
    cfg, out = CONFIG["mouth"], sys.stdout.buffer
    sys.stdout = sys.stderr
    import torch
    from chatterbox.tts_turbo import ChatterboxTurboTTS, Conditionals
    from transformers.initialization import no_init_weights
    torch.set_num_threads(CONFIG["brain"]["threads"])
    with torch.inference_mode(), no_init_weights():
        model = ChatterboxTurboTTS.from_local(MODELS / cfg["model"], cfg["device"], nano=True)
    voice = ROOT / sys.argv[1]
    with torch.inference_mode():
        if voice.exists():
            model.conds = Conditionals.load(voice, map_location="cpu").to(cfg["device"])
        else:
            model.prepare_conditionals(str(ROOT / cfg["reference"]))
            model.conds.save(voice)
        out.write(b"\0\0\0\0")
        out.flush()
        while True:
            header = sys.stdin.buffer.read(4)
            if len(header) < 4:
                break
            count = int.from_bytes(header, "little")
            if not count:
                break
            words = sys.stdin.buffer.read(count)
            if len(words) < count:
                break
            audio = render(model.generate(words.decode("utf-8")).squeeze().cpu().numpy(), model.sr)
            out.write(len(audio).to_bytes(4, "little") + audio)
            out.flush()
