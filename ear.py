import json, re, subprocess, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "workspace"
MODELS = ROOT / "models"
EAR = {
    "dir": "nemotron-3.5-asr-streaming-0.6b",
    "files": ("config.json", "generation_config.json", "processor_config.json", "tokenizer_config.json", "tokenizer.json", "model.safetensors"),
    "sample_rate": 16000,
    "threads": 4,
    "language": "auto",
    "lookahead": 13,
    "pause": 1.2,
    "level": 0.03,
}
TAG = re.compile(r"<([A-Za-z]{2,3}-[A-Za-z]{2})>\s*$")


def venv_python() -> Path:
    return ROOT / ".venv" / "Scripts" / "python.exe"


def reexec() -> None:
    py = venv_python()
    if not py.is_file():
        raise RuntimeError("missing " + str(py))
    if Path(sys.executable).resolve() != py.resolve():
        raise SystemExit(subprocess.call([str(py), *sys.argv]))
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def request(server: str, method: str, path: str, payload=None):
    raw = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(server + path, data=raw, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=70) as resp:
        return json.loads(resp.read().decode("utf-8"))


def retire(path: Path, kind: str) -> Path:
    folder = WORK / "done" / kind
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / path.name
    n = 1
    while dest.exists():
        dest = folder / f"{path.stem}-{n}{path.suffix}"
        n += 1
    path.replace(dest)
    return dest


class Ear:
    def __init__(self, server: str, model=True):
        self.server = server.rstrip("/")
        if not model:
            return
        import torch
        from transformers import AutoModelForRNNT, AutoProcessor
        home = MODELS / "ear" / EAR["dir"]
        missing = [home / name for name in EAR["files"] if not (home / name).is_file()]
        if missing:
            raise RuntimeError("missing " + str(missing[0]))
        torch.set_num_threads(EAR["threads"])
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(home)
        self.processor.set_num_lookahead_tokens(EAR["lookahead"])
        self.model = AutoModelForRNNT.from_pretrained(home)
        self.model.eval()
        self.prompt_ids = self.processor._resolve_prompt_ids(EAR["language"], 1)
        print("streaming_latency_ms", self.processor.streaming_latency_ms, flush=True)

    def tag(self, sequences, durations=None):
        ids = sequences[0]
        flat = [int(x) for x in ids.tolist()]
        raw = self.processor.decode(ids, skip_special_tokens=False)
        if not isinstance(raw, str):
            raw = raw[0]
        match = TAG.search(str(raw).strip())
        language = match.group(1) if match else ""
        if not language:
            for piece in reversed(self.processor.tokenizer.convert_ids_to_tokens(flat)):
                found = re.search(r"[A-Za-z]{2,3}-[A-Za-z]{2}", str(piece))
                if found:
                    language = found.group(0)
                    break
        text = self.processor.decode(ids, skip_special_tokens=True)
        if not isinstance(text, str):
            text = text[0]
        text = TAG.sub("", text).strip()
        times = ""
        if durations is not None and durations.numel():
            try:
                batch = sequences if sequences.ndim == 2 else sequences.unsqueeze(0)
                spans = durations if durations.ndim == 2 else durations.unsqueeze(0)
                width = min(batch.shape[1], spans.shape[1])
                _, stamps = self.processor.decode(batch[:, :width], durations=spans[:, :width], skip_special_tokens=True)
                words = []
                for item in stamps[0]:
                    token = item["token"]
                    if not words or token[:1] == " ":
                        words.append([token.strip(), float(item["start"])])
                    else:
                        words[-1][0] += token
                times = " ".join(f"{word} {start:.2f}" for word, start in words if word)
            except Exception as err:
                print("timestamps skipped", type(err).__name__, err, flush=True)
        return language, text, times

    def transcribe_batch(self, audio):
        inputs = self.processor(audio, sampling_rate=EAR["sample_rate"], language=EAR["language"])
        with self.torch.inference_mode():
            output = self.model.generate(**inputs, return_dict_in_generate=True)
        return self.tag(output.sequences, getattr(output, "durations", None))

    def transcribe_stream(self, pull):
        import numpy as np
        first_n = self.processor.num_samples_first_audio_chunk
        step_n = self.processor.num_samples_per_audio_chunk
        state = {"buf": np.zeros(0, dtype=np.float32), "first": True, "last": time.time()}

        def features(samples, first):
            feat = self.processor(samples, sampling_rate=EAR["sample_rate"], language=EAR["language"], is_streaming=True, is_first_audio_chunk=first)["input_features"]
            need = self.processor.num_mel_frames_first_audio_chunk if first else self.processor.num_mel_frames_per_audio_chunk
            if feat.shape[1] > need:
                feat = feat[:, :need]
            elif feat.shape[1] < need:
                pad = feat.new_zeros(feat.shape[0], need - feat.shape[1], *feat.shape[2:])
                feat = self.torch.cat([feat, pad], dim=1)
            return feat

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

        with self.torch.inference_mode():
            output = self.model.generate(input_features=chunks(), num_lookahead_tokens=EAR["lookahead"], prompt_ids=self.prompt_ids)
        language, text, times = self.tag(output.sequences, getattr(output, "durations", None))
        return language, text, times, state["last"]

    def send(self, text: str, language: str = "", timestamps: str = "", last=None):
        text = text.strip()
        if not text:
            return
        row = request(self.server, "POST", "/heard", {"text": text, "language": language, "timestamps": timestamps})
        if last is not None:
            print("last_hot", f"{last:.3f}", "event", row.get("id"), flush=True)
        print("heard", row.get("id"), language, text, flush=True)

    def drain_inbox(self):
        inbox = WORK / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        for path in sorted(inbox.glob("*.txt")):
            body = path.read_text(encoding="utf-8-sig").strip()
            if not body:
                retire(path, "inbox")
                continue
            match = re.match(r"<([A-Za-z]{2,3}-[A-Za-z]{2})>\s*", body)
            language = match.group(1) if match else ""
            text = body[match.end():] if match else body
            self.send(text, language)
            retire(path, "inbox")

    def hear(self, path: str):
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
            language, text, times, last = self.transcribe_stream(pull)
        except Exception as err:
            print("stream failed", err, flush=True)
            language, text, times = self.transcribe_batch(audio)
            last = None
        if not text:
            raise RuntimeError("ear heard nothing")
        self.send(text, language, times, last)

    def listen(self, mic=True):
        request(self.server, "POST", "/ready", {"name": "ear"})
        if not mic:
            while True:
                self.drain_inbox()
                time.sleep(0.05)
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

                def pull():
                    self.drain_inbox()
                    if pending:
                        return pending.pop(0)
                    item, _ = stream.read(hop)
                    if float(np.abs(item).mean()) >= level:
                        quiet["n"] = 0
                        return item.reshape(-1).copy()
                    quiet["n"] += 1
                    if quiet["n"] >= pause:
                        return None
                    return item.reshape(-1).copy()

                try:
                    language, text, times, last = self.transcribe_stream(pull)
                except Exception as err:
                    print("stream failed", err, flush=True)
                    chunks = []
                    item = pull()
                    while item is not None:
                        chunks.append(item)
                        item = pull()
                    if not chunks:
                        continue
                    language, text, times = self.transcribe_batch(np.concatenate(chunks))
                    last = time.time()
                if text:
                    self.send(text, language, times, last)


def main():
    reexec()
    if len(sys.argv) < 2:
        raise SystemExit("usage: python ear.py <server> [--inbox | wav]")
    server = sys.argv[1]
    if len(sys.argv) == 2:
        Ear(server).listen()
    elif len(sys.argv) == 3 and sys.argv[2] == "--inbox":
        Ear(server, model=False).listen(mic=False)
    elif len(sys.argv) == 3:
        Ear(server).hear(sys.argv[2])
    else:
        raise SystemExit("usage: python ear.py <server> [--inbox | wav]")


if __name__ == "__main__":
    main()
