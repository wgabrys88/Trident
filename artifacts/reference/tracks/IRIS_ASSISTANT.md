# Iris assistant

Current law. This track is in force on this PC. Near term it runs only on Iris. It is the local voice loop. It is not a Grok seat, and it is not the LAN job router. Wave 3 is a research note, not this path.

## This PC

- Worker: `trident-iris`
- Cwd: `C:\Users\eb-wjt\Downloads\Jarvis\Trident`
- EB-W machine id: `84403f85-8162-436b-9567-dd9255e82a60`
- Shell: PowerShell. Do not use `&&`.
- Playback for a Mouth one-shot and for this loop: default waveform device, `Speakers (Realtek(R) Audio)`.
- Microphone for `hear.py`: the Intel Smart Sound microphone array. Cable proofs stay on VB-Audio and are a different entry (`GOAL.md`, the resident chain).

## Path

`assistant.py` runs, through `.venv\Scripts\python.exe`:

1. `hear.py` (unless `--text`)
2. `qwen.py` by default, or `gemma.py` when `--brain gemma`
3. `mouth.py`

The default brain is `qwen` (Qwen3-0.6B, text only). `--brain gemma` is opt-in and stays on this Iris machine. The Vulkan build can be slow. The loop does not move to Nvidia. `--image` requires `--brain gemma`. An image with the default brain exits 2.

This path makes no network call. Grok is off this path. A `local_bots` package, if it is ever built, is a separate package and may share transport only with this track. It is not built, and it is not this loop. Cloud SPOC routing is a different track. Neither one replaces this loop, and this loop does not call them. Wave 3 (`artifacts/reference/WAVE3_PLAN.md`) is not this loop.

`assistant.py` does not load a model. It does not start `vad.exe`, `ear.exe`, `sense.exe`, `gemma-brain.exe`, or `chatterbox.exe`. Those start only inside the one-shots, as they already do. It does not keep the five residents loaded. It does not close the stay-loaded finish line in `GOAL.md`.

## Commands

From the cwd above:

```
.\.venv\Scripts\python.exe assistant.py
.\.venv\Scripts\python.exe assistant.py --once --text "Say only: ready." --model nano
```

`--seconds` defaults to 8 and is the `hear.py` duration. `--text` skips the mic and runs one brain-then-mouth round. `--once` is one heard turn. With neither `--once` nor `--text`, the loop repeats until the heard transcript is `quit`, `exit`, or `stop`, or until Ctrl-C.

`--model` defaults to `nano`. Omitted `--lang` is `en` for nano and turbo, and `pl` for v3. The mouth language is shared by every chunk.

A dry prove is the `--text` command. It skips the mic. Speakers are the default playback device.

## Contract owner

Flag-level behavior, the speakable span, and the chunk cap (65 English words, or 55 when the mouth language is `pl`) are owned by `GOAL.md`. This file owns the track boundary: this PC, Iris-only for the near term, hear then brain then mouth, default `qwen`, no network, Grok off this path. If the two disagree, `GOAL.md` wins for the program and this file is rewritten in the same change.
