import argparse
import sys
import time
import wave
from pathlib import Path

from brain import Brain
from parakeet import Parakeet
from tts_nano import NanoTTS


def audio_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("text")
    args = parser.parse_args()
    total_start = time.perf_counter()

    print("[brain] start", file=sys.stderr, flush=True)
    brain_start = time.perf_counter()
    brain = Brain()
    chunks, first = [], None
    try:
        brain.start()
        brain_ready = time.perf_counter()
        for chunk in brain.stream(args.text):
            if first is None:
                first = time.perf_counter()
            chunks.append(chunk)
        brain_generated = time.perf_counter()
    finally:
        brain.close()
    brain_end = time.perf_counter()
    answer = "".join(chunks).replace("\r", "").strip()
    marker = "Assistant:\n"
    if marker in answer:
        answer = answer.rsplit(marker, 1)[-1].strip()
    if not answer:
        raise RuntimeError("Brain produced no spoken reply")
    brain_startup = brain_ready - brain_start
    brain_ttft = (first or brain_generated) - brain_ready
    brain_inference = brain_generated - brain_ready
    print(f"[brain] done startup_s={brain_startup:.3f} ttft_s={brain_ttft:.3f} inference_s={brain_inference:.3f} total_s={brain_end-brain_start:.3f}", file=sys.stderr)

    print("[tts] start", file=sys.stderr, flush=True)
    tts_start = time.perf_counter()
    wav = NanoTTS(log=False).synthesize(answer)
    tts_elapsed = time.perf_counter() - tts_start
    duration = audio_seconds(wav)
    print(f"[tts] done audio_s={duration:.3f} elapsed_s={tts_elapsed:.3f} rtf={tts_elapsed/duration:.3f}", file=sys.stderr)
    print(f"[brain] rtf audio_s={duration:.3f} inference_rtf={brain_inference/duration:.3f} startup_plus_inference_rtf={(brain_startup+brain_inference)/duration:.3f}", file=sys.stderr)

    print("[asr] start", file=sys.stderr, flush=True)
    asr_start = time.perf_counter()
    transcript = Parakeet().transcribe(wav, log=False).strip()
    asr_elapsed = time.perf_counter() - asr_start
    print(f"[asr] done audio_s={duration:.3f} elapsed_s={asr_elapsed:.3f} rtf={asr_elapsed/duration:.3f}", file=sys.stderr)

    total_elapsed = time.perf_counter() - total_start
    print(f"[total] done elapsed_s={total_elapsed:.3f} audio_s={duration:.3f} rtf={total_elapsed/duration:.3f} wav={wav.name}", file=sys.stderr)
    print(transcript)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
