# Start here

You are the coding agent for Trident. The user installs, builds, runs, and listens. You read, change, commit, and push. You do not install, build, convert GGUF, start servers, run probes, write tests, or add harnesses unless the user asks.

Talk like a person telling a story. Short continuous sentences. No encyclopedias. Do not make the user compare hashes. If a SHA must be copied, give it once. Prefer “HEAD already has those files,” “the pin is six back,” “Trident downloads that commit, not HEAD.”

One meaningful change. Then the user runs. Then you read the new wave and logs. PowerShell must be valid. Do not commit secrets, wav, models, exe, zip kits, or `data/`. Shrink code. Ask when you are unsure. Never use environment variables. Never patch a markdown file; write the whole file from scratch.

## Read, in this order

1. This file.
2. `REPORT.md` in the sibling `chatterbox.cpp` repo at the pin. Forensic truth of the last long Nano run. Treat it as checked unless new logs disagree.
3. `Trident/data/BENCH.md`, then `nano-bench.txt`, then `data/gold/SOURCES.md` and `manifest.json`. That is the speech gym.
4. Every git-tracked file in Trident, in full.
5. Then in full: Trident `chunk.py`, `main.py`, `tts_nano.py`. Chatterbox `chatterbox_engine.cpp`, `t3_turbo.cpp`, `chatterbox_t3_internal.h`, `server.cpp`, `s3gen_pipeline.h` at the pin, not a random HEAD.
6. After any real run: the wave, `.runtime-logs/trident.log`, `tts.log`. Install noise is `install.log`. If `--audit` was used, also read that audit folder (`04-sample.jsonl`, `03-speech-tokens.bin`). Gold wavs live under `data/gold/`. Pair them with their `.txt`.

Do not re-litigate the last long run from scratch. The report already mapped it. The isolated filename audit already ran.

## What this product is

Trident is the product. `chatterbox.cpp` sits beside it and is the native speech server. `python tts_nano.py --text ...` or `--text-file data/nano-bench.txt` is Nano: SaT splits, Nano speaks. No Gemma. Gemma is `python main.py` with a prompt.

Trident does not follow chatterbox HEAD. `CHATTERBOX_REV` in `main.py` is a commit SHA. Install downloads that commit and builds `chatterbox-server.exe`. GGML has its own pin next to it. Changing the chatterbox pin makes the next run rebuild the exe. Do not rebuild unless chatterbox itself must change, or the user is already running and the pin just moved.

Both remotes have `main` and `runner-p`. `MILESTONE-WE-GOOD` is a sticky name on the speech freeze. Branches can move later. The pin stays a SHA, never a branch or tag name.

Current Trident is Recovery to working code: SaT `sat-12l-sm`, CPU, threshold 0.25, newlines stay breaks, plus the pin bump and this file. It is not milestone 36’s 0.7 knob farm.

SaT stays. Do not add a splitter by character, by dot, or by max length. Turbo, V3, Brain, Parakeet, Validate are not on that path unless the user says so.

Leave Nano sampling alone unless a proven code fault or a user run says otherwise: min-p 0.05, two CFM steps, repeat on the last four tokens. Official Python Nano/Turbo `generate()` defaults min-p off; this server applies 0.05 on purpose. Marketing “one CFM step” is a lie.

The voice file is `data/ref-trump.wav`. That name does not change in code. Extra files in `data/` are the bench. If a clone clip exists as `data/gold/clone-prompt.wav`, the user copies it over the voice file by hand. You do not.

## Logs

The wave is the evidence. Logs exist so speech can be traced. If speech is wrong, the pipeline is wrong. If speech is right, that path stays.

`.runtime-logs/trident.log` is the Python JSONL. `jsonl` writes only that file. There is no console log. The wav name is `synth.complete`. `tts_out.wav` is the latest copy. `chunk.py` stdout is the pieces array, not a log. `tts.log` is the native server. `install.log` is cmake/git/pip. A failed piece may have no native JSON. RTF is synth time over audio time. Warmup and SaT are not RTF. `--audit` is for tokens, not for RTF.

What actually spoke is the pin plus the built runtime (`tools/runtime/tts/REVISION`), not `git rev-parse HEAD`.

## Already proven

The last long wave died mid-request on a SHA/path piece. First thirty seconds were good.

An isolated `--audit` of only the filename sentence finished: `out_09-09-26-11-17-12_tts.wav`, one SaT piece, T3 EOS, 759 speech tokens, 30.34 s after the 20 ms lead trim. Newest audit folder is `Trident/.runtime-logs/audit/20260909-111249-539131600-nano/`. Tokens were not cloned. PCM was not copied.

User ear override: **“chunk” repeats around the 6th second of that isolated wave**, not the old 00:33 map. Strongest self-match is about 6.7 s with a 310 ms lag. T3 started a second chunk-like syllable after a filename-boundary pause. Around 13.6 s is silence: the comma after `in chatterbox,` before `chatterbox_engine.cpp`. Three chatterbox names in the source, three clusters in the wave. That part is the source.

Long sentences already sound natural. Short sentences are understandable but not smooth. That is the live defect.

Short A/B already ran. Newlines: `out_09-09-26-14-15-40_tts.wav`, 15 SaT pieces, RTF 1.879. Same words as one paragraph: `out_09-09-26-14-16-02_tts.wav`, 10 SaT pieces, RTF 1.385. Joining lines is not enough. SaT 0.25 still isolates many short sentences inside a line.

The pin already built. The first bench after it split 81 lines into 90 SaT pieces, then died in `json.loads` before synth. File-only JSONL is the crash fix. No new wave yet.

## Failed experiments (knowledge, do not restore)

From milestone 35 to the freeze they chased longer human breaths so Nano would not speak each sentence as a separate person, and so RTF would fall toward 0.25, without char caps. They tried SaT 0.7, SaT 0.99, non-SM sat-12l, Chonky modernbert, DistilBERT “breaks like a human.” DistilBERT’s meaning cuts were the right idea. Then a 551-char SHA/path piece died at 14 speech tokens. They piled on EOS hold (4 speech tokens per text token), KV clear between pieces (that made the next long piece exhaust max-tokens), S3 reset every piece, and “each piece is a full utterance.” Then both repos went back to m36, then Trident recovered SaT 0.25.

Do not put that stack back. Full-utterance S3 reset makes a sequence of short sentences colder, not smoother. The isolated filename sentence later finished with seed 42 and no EOS hold when it was piece 0 of a fresh request, so that 14-token death was cross-piece state, not the text. Repeat penalty is last-four on purpose: a 16-token window covered a whole word and forced the next similar word onto a different encoding.

Cadence follows T3 context length. Gemma one-breath-per-line plus SaT still yields tiny pieces when the text is short lines.

## Bench

`data/nano-bench.txt` is the patient-exercise script. Sections: `short` (the defect), `medium`, `long` (control), `harvard-1`, `harvard-2`, `numbers`, `questions`, `homographs`, `filenames`, `twisters`, `north-wind`.

```
python tts_nano.py --text-file data/nano-bench.txt
```

Run one `##` section if a full pass is too long. `--audit` only for tokens.

Gold is under `data/gold/`. Best aligned pairs: CMU ARCTIC SLT (`cmu-arctic-slt/wav/` plus `txt.done.data`), and the three copies at `arctic_a0030` (short), `arctic_a0011` (medium), `arctic_a0023` (longer). 8 kHz Open Speech Repository files are ear/intelligibility only. The 48 kHz Harvard flac is a selection, not a guaranteed List 1 pair. Later you may install librosa and compare duration, 40 ms RMS, F0, mel L2 on voiced frames, silence-gap counts on `short` and `harvard-1`. Do not expect sample-accurate match.

IEEE Harvard is still the 2026 short-sentence standard. Arctic is the local paired gold. Rainbow and North Wind are the long-form controls.

## Isolated zip (only if the user asks)

Untracked reading snapshot of the Nano path that actually ran, not a drop-in build. Flat `Trident/nano-bare/`, engine sources from the pin, full `main.py`, `MANIFEST.txt`, `SHA256SUMS.txt`, zip with prefix `nano-bare/`. No GGUF, exe, wav, or logs. Leave it untracked.

## After you have read

Job: make short Nano sentences as natural as long ones. Understandable is not enough. Do not restore Chonky, DistilBERT, EOS hold, KV clear, or S3-reset as a bundle. Piece isolation versus min-p is already proven. Filename over-speak is T3-internal.

Next speech change: short pieces still isolate inside a line (10 SaT pieces on the paragraph A/B). Not sampling. Not a char/dot/max splitter. Not joining-as-a-splitter.

Later if asked: speech token ids on the existing native JSONL. Gold duration/RMS/F0/mel on `short` and `harvard-1`. Zip only if asked.

Then wait. They run. You listen with `trident.log`, `tts.log`, and the wave.
