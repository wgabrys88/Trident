import re, sys, time
from pathlib import Path
import torch
from runtime import MODELS, reexec
from settings import EAR, INBOX, bus, next_path, put, ready, take

TAG = re.compile(r"<([A-Za-z]{2,3}-[A-Za-z]{2})>\s*$")


class Ear:
    def __init__(self, model=True):
        self.ready = False
        self.stream = True
        if not model:
            bus()
            return
        from transformers import AutoModelForRNNT, AutoProcessor
        home = MODELS / "ear" / EAR["dir"]
        missing = [home / name for name in EAR["files"] if not (home / name).is_file()]
        if missing:
            raise RuntimeError("missing " + str(missing[0]))
        torch.set_num_threads(EAR["threads"])
        self.processor = AutoProcessor.from_pretrained(home)
        self.processor.set_num_lookahead_tokens(EAR["lookahead"])
        self.model = AutoModelForRNNT.from_pretrained(home)
        self.model.eval()
        self.prompt_ids = self.processor._resolve_prompt_ids(EAR["language"], 1)
        print("streaming_latency_ms", self.processor.streaming_latency_ms, flush=True)
        self.ready = True

    def tag(self, sequences, durations=None):
        ids = sequences[0]
        raw = self.processor.decode(ids, skip_special_tokens=False)
        if not isinstance(raw, str):
            raw = raw[0]
        match = TAG.search(raw.strip())
        lang = "<" + match.group(1) + ">" if match else ""
        text = self.processor.decode(ids, skip_special_tokens=True)
        if not isinstance(text, str):
            text = text[0]
        text = TAG.sub("", text).strip()
        times = ""
        if durations is not None:
            try:
                _decoded, stamps = self.processor.decode(ids, durations=durations, skip_special_tokens=True)
                words = []
                for item in stamps:
                    token = item["token"]
                    if not words or token.startswith(" "):
                        words.append([token.strip(), item["start"]])
                    else:
                        words[-1][0] += token
                times = " ".join(f"{word} {start:.2f}" for word, start in words if word)
            except Exception as err:
                print("timestamps skipped", err, flush=True)
        return (lang + " " + text).strip(), times

    def transcribe_batch(self, audio):
        inputs = self.processor(audio, sampling_rate=EAR["sample_rate"], language=EAR["language"])
        with torch.inference_mode():
            output = self.model.generate(**inputs, return_dict_in_generate=True)
        durations = output.durations[0] if getattr(output, "durations", None) is not None else None
        return self.tag(output.sequences, durations)

    def transcribe_stream(self, pull):
        first_n = self.processor.num_samples_first_audio_chunk
        step_n = self.processor.num_samples_per_audio_chunk
        import numpy as np
        state = {"buf": np.zeros(0, dtype=np.float32), "first": True, "last": time.time()}

        def features(samples, first):
            feat = self.processor(samples, sampling_rate=EAR["sample_rate"], language=EAR["language"],
                                  is_streaming=True, is_first_audio_chunk=first)
            return feat["input_features"]

        def chunks():
            while True:
                need = first_n if state["first"] else step_n
                while len(state["buf"]) < need:
                    frame = pull()
                    if frame is None:
                        if len(state["buf"]) == 0 and not state["first"]:
                            return
                        state["buf"] = np.pad(state["buf"], (0, need - len(state["buf"])))
                        yield features(state["buf"], state["first"])
                        state["buf"] = np.zeros(0, dtype=np.float32)
                        return
                    state["last"] = time.time()
                    state["buf"] = np.concatenate([state["buf"], np.asarray(frame, dtype=np.float32).reshape(-1)])
                piece, state["buf"] = state["buf"][:need], state["buf"][need:]
                yield features(piece, state["first"])
                state["first"] = False

        with torch.inference_mode():
            output = self.model.generate(input_features=chunks(), num_lookahead_tokens=EAR["lookahead"], prompt_ids=self.prompt_ids)
        durations = output.durations[0] if getattr(output, "durations", None) is not None else None
        text, times = self.tag(output.sequences, durations)
        return text, times, state["last"]

    def line(self, text, times="", last=None):
        body = text + "\n"
        if times:
            body += times + "\n"
        path = put(next_path("transcription"), body)
        if last is not None:
            print("last_hot", f"{last:.3f}", "transcription", f"{path.stat().st_mtime:.3f}",
                  "delta", f"{path.stat().st_mtime - last:.3f}", flush=True)
        print(path.name, text, flush=True)

    def drain_inbox(self):
        bus()
        for path in sorted(INBOX.glob("*.txt")):
            text = take(path, "inbox").strip()
            if text:
                self.line(text)

    def hear(self, path):
        import numpy as np
        from transformers.audio_utils import load_audio
        file = Path(path)
        if not file.is_file():
            raise RuntimeError("missing " + str(file))
        audio = np.asarray(load_audio(str(file), sampling_rate=EAR["sample_rate"], backend="librosa"), dtype=np.float32)
        cursor = {"i": 0}

        def pull():
            hop = int(EAR["sample_rate"] * 0.05)
            if cursor["i"] >= len(audio):
                return None
            nxt = audio[cursor["i"]:cursor["i"] + hop]
            cursor["i"] += hop
            return nxt

        try:
            text, times, last = self.transcribe_stream(pull)
        except Exception as err:
            print("stream failed", err, flush=True)
            text, times = self.transcribe_batch(audio)
            last = None
        if not text:
            raise RuntimeError("ear heard nothing")
        self.line(text, times, last)

    def listen(self, mic=True):
        bus()
        ready("ear")
        if not mic:
            while True:
                self.drain_inbox()
                time.sleep(0.05)
            return
        import numpy as np
        import sounddevice as sd
        rate, hop = EAR["sample_rate"], int(EAR["sample_rate"] * 0.05)
        pause, level = max(1, int(EAR["pause"] / 0.05)), EAR["level"]
        with sd.InputStream(samplerate=rate, channels=1, dtype="float32", blocksize=hop) as stream:
            while True:
                self.drain_inbox()
                frame, _ = stream.read(hop)
                if float(np.abs(frame).mean()) < level:
                    continue
                pending = [frame.reshape(-1).copy()]
                quiet = {"n": 0}

                def pull(pending=pending, quiet=quiet):
                    self.drain_inbox()
                    if pending:
                        return pending.pop(0)
                    frame, _ = stream.read(hop)
                    if float(np.abs(frame).mean()) >= level:
                        quiet["n"] = 0
                        return frame.reshape(-1).copy()
                    quiet["n"] += 1
                    if quiet["n"] >= pause:
                        return None
                    return frame.reshape(-1).copy()

                try:
                    text, times, last = self.transcribe_stream(pull)
                except Exception as err:
                    print("stream failed", err, flush=True)
                    self.stream = False
                    chunks = []
                    frame = pull()
                    while frame is not None:
                        chunks.append(frame)
                        frame = pull()
                    if not chunks:
                        continue
                    text, times = self.transcribe_batch(np.concatenate(chunks))
                    last = time.time()
                if text:
                    self.line(text, times, last)


if __name__ == "__main__":
    reexec()
    if len(sys.argv) == 1:
        Ear().listen()
    elif len(sys.argv) == 2 and sys.argv[1] == "--inbox":
        Ear(model=False).listen(mic=False)
    elif len(sys.argv) == 2 and sys.argv[1]:
        Ear().hear(sys.argv[1])
    else:
        raise SystemExit("usage: python asr.py [wav | --inbox]")
