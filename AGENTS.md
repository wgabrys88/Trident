# AGENTS

Read `GOAL.md`, this file, `RULES.md`, and `BOTS.md` before editing. This is the handoff for a session with no earlier chat. `BOTS.md` seats the Grok Bot roles. This file does not restate that kit. `CODE_REVIEW_CHECKLIST.md` is the review procedure. Commit messages carry only the delta for that change. Do not reconstruct the project by copying old commit bodies forward.

Work on branch `runner-h` in the local clone of https://github.com/wgabrys88/Trident. Fetch and update `origin/runner-h` before changing anything. The final commit stays on `runner-h`. Iris commits only: make the commit on this Iris checkout. Do not open a pull request.

## This machine

This checkout is Wojciech's private Windows worker. Re-measure if the machine changes. A note from another computer is memory of that computer.

- PowerShell on this PC does not accept `&&` as a statement separator. Use `;` or separate commands.
- `python` is Python 3.11.9. The `py` launcher is not on `PATH`.
- CMake 4.4.2 and Git are on `PATH`.
- Visual Studio Build Tools 2022 (17.14) are at `C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools`. `install.txt` asks for generator `Visual Studio 17 2022`, architecture `x64`.
- Vulkan SDK lives under `C:\VulkanSDK`. The measured SDK is `C:\VulkanSDK\1.4.357.0`. `install.vulkan_sdk` is empty, so the installer picks the newest SDK that contains `Bin\glslc.exe`.
- The GPU is Intel Iris Xe. There is no CUDA toolkit. `gemma\scripts\detect_gpu.ps1` prints `cuda` only when an NVIDIA adapter and `nvcc` are both present; otherwise it prints `vulkan`. With `install.gemma_backend` set to `auto`, this machine builds the brain and the gate with Vulkan.
- `install.build_gemma` is `C:\tgemma`. The Vulkan shader step fails on a long path. Leave that short path in place.

Primary cook, proofs, and commits stay on this Iris checkout, branch `runner-h`. Do not open a pull request.

## Audio on this machine

Two capture truths. Cable proofs and `hear.py` are different entries.

Enumerate endpoints yourself. `vad.exe` matches `vad.device` to the WASAPI capture friendly name (`PKEY_Device_FriendlyName`) and fails if that string is not an active capture endpoint. It does not use a device index. It opens the device in shared mode, reads the mix format, and resamples with `Audio::resample` to `vad.rate` (16000 in `vad.txt`).

Names on this worker, re-enumerate before a proof:

- Capture for cable proofs: `CABLE Output (VB-Audio Virtual Cable)`. That string is `vad.device` in `vad.txt`.
- Playback into the cable: `CABLE Input (VB-Audio Virtual Cable)`.
- Also present, and not the proof pair: `CABLE In 16ch (VB-Audio Virtual Cable)`.
- Real speakers: `Speakers (Realtek(R) Audio)`.
- Room microphone: the Intel Smart Sound microphone array. Cable proofs leave it unused. `hear.py` uses the normal PC mic, which on this worker is that array.

`chatterbox.exe` with `chatterbox.play on` plays the wav with `PlaySoundW` on the default waveform device. `mouth.py` plays with `PlaySoundW` on that same default. There is no device flag on `mouth.py` or in `chatterbox.txt`. Before a chain proof or a Mouth one-shot, set that default to the real speakers, `Speakers (Realtek(R) Audio)` on this worker, so the mouth is not the next thing capture hears.

If the virtual cable is missing, install VB-Audio Virtual Cable. The person may call it a BB cable. Do not switch a cable proof to the room microphone.

## Clone, update, install

```
git clone https://github.com/wgabrys88/Trident.git
git checkout runner-h
git pull origin runner-h
python install.py install.txt
```

On an existing checkout, fetch and pull `runner-h` instead of cloning again. `python install.py install.txt` is the only install command. The installer reads that file and nothing else for its parameters. It creates `.venv` when needed and re-runs itself with that interpreter. It clones the pinned ggml, llama.cpp, and NeMo-Speech trees under `.install`, configures CMake, builds, downloads the models named in `install.txt`, and bakes nano, turbo, and v3 with `chatterbox-bake.exe`. `install.publish` is `off`. Leave it off. Publishing would create a GitHub release.

What the installer copies to the repository root:

- `chatterbox.exe`, `chatterbox-bake.exe`, `ear.exe`, `vad.exe`, and `onnxruntime.dll`. The mouth links ggml statically so it does not import `ggml.dll`; the ear build keeps its own ggml DLL.
- `nemo-speech.exe` and the DLLs beside it. `ear.exe` launches `nemo-speech.exe transcribe` and writes the captured stdout. `hear.py` launches the same executable once and prints that stdout.
- `gemma-brain.exe` and `sense.exe` from `C:\tgemma`.
- Model files named by the templates (`vad.model`, `ear.model`, `sense.model`, `gemma.model`, `gemma.mmproj`, and the nano, turbo, and v3 GGUF pairs). `hear.py` defaults to `ear.gguf` beside the script.

This checkout already has those outputs, `.venv`, and `.install`. They are local. Do not commit them. If a build directory's CMake cache was generated for a different source tree, remove that cache and configure this source. A compile error is fixed in this repository's source by reading the error back to the include or the call that caused it. Do not satisfy a broken system header by adding a library path that exists only on one computer.

## Run one program

Each resident takes one text file and no flags. The usage line is `usage: program file.txt`. Run it from the directory where the result should appear. Paths inside the text file are resolved from that file's directory, not from the current directory.

| Command | Reads | Writes in the current directory |
| --- | --- | --- |
| `vad.exe vad.txt` | WASAPI capture named by `vad.device`. Silero ONNX, window 512. | One finished utterance, then exit. `HH-MM-SS-mmm_vad_out_NNN.txt` contains the wav file name. The wav sits beside it. |
| `ear.exe ear.txt` | `ear.input`, one wav. | `HH-MM-SS-mmm_ear_out_NNN.txt`, the recognizer text unchanged. Empty `ear.language` omits `--language`. |
| `sense.exe sense.txt` | `sense.text`, the whole prompt, including the Qwen3 turn markers the user wrote. CPU, `sense.gpu-layers` 0. | `HH-MM-SS-mmm_sense_out_NNN.txt`, the generation unchanged. |
| `gemma-brain.exe gemma.txt` | `gemma.text`, the whole prompt in Gemma 4's own form. `gemma.image` is raw base64 or empty. When the image is set, the prompt must already contain `<__media__>`. | `HH-MM-SS-mmm_gemma_out_NNN.txt`, the generation unchanged. |
| `chatterbox.exe chatterbox.txt` | `chatterbox.variant` is `nano`, `turbo`, or `v3`. The GGUF architecture selects the engine. `chatterbox.text` is spoken as written. `chatterbox.play` is `on` or `off`. | `HH-MM-SS-mmm_chatterbox_out_NNN.txt` names the wav. `on` plays that wav on the default speakers. `off` skips PlaySound. |
| `chatterbox-bake.exe bake.txt` | `bake.t3`, `bake.s3`, `bake.reference`. `bake.cond-seconds` is 15 for nano and turbo, 6 for v3. | Rewrites those two model files in place, writes `HH-MM-SS-mmm_bake_out_NNN.txt` with both paths, and exits. |
| `.\.venv\Scripts\python.exe mouth.py TEXT [TEXT ...]` | `chatterbox.txt` for the GGUF pairs and numeric knobs. The sentences, `--model`, and `--lang` come from the command. One process takes every chunk. | For each TEXT: overwrites `mouth.txt` with `chatterbox.play off`, then one `chatterbox.exe mouth.txt`. `mouth.py` plays the wavs and overlaps the next synthesize. |
| `.\.venv\Scripts\python.exe hear.py SECONDS` | The PC mic for `SECONDS`, then `nemo-speech.exe transcribe` once. | Transcript on stdout. Exit code is the recognizer's. No resident output file. |

The resident output file is created with `CREATE_NEW`. The number starts at `000`. An existing name is kept and the next number is used. Nothing in the program watches another program.

To run the chain, connect the files yourself. Play known speech into `CABLE Input`. Point `vad.device` at `CABLE Output`. Put the wav name `vad.exe` wrote into `ear.input`. Put the recognizer text into the next prompt file as that model's own prompt, still unchanged by code. Put the sentence the brain wrote into `chatterbox.text`, or pass that sentence to `mouth.py`. Judge each step by the file that step wrote. Judge `hear.py` by its stdout.

## Mouth one-shot

`mouth.py` is the speak entry. It does not synthesize. The Lego contract stays settings file, then `chatterbox.exe`.

```
.\.venv\Scripts\python.exe mouth.py [--model nano|turbo|v3] [--lang TAG] [--diag-log DIR_OR_PATH] "First sentence." "Second sentence."
```

One process takes every chunk. A separate process per chunk is a reported fallback, not a silent one. SPEAK GO for that role comes from SPOC or Wojciech. The role kit is `BOTS.md`.

`--model` defaults to `nano`. When `--lang` is omitted, `nano` and `turbo` use `en`, and `v3` uses `pl`. A passed `--lang` is written as given and shared by every chunk. Empty text, an unknown model, an empty language, and any text line whose entire content is `<<` are rejected with exit code 2 before the first `chatterbox.exe`.

For each TEXT, the script re-reads `chatterbox.txt`, drops `chatterbox.variant`, `chatterbox.language`, `chatterbox.play`, and the text block, writes `mouth.txt` with those keys plus `chatterbox.play off`, and runs `.\chatterbox.exe mouth.txt` once (synthesize only). `mouth.py` plays each wav with `PlaySoundW` (`SND_FILENAME | SND_NODEFAULT`) on the default speakers and synthesizes the next chunk while the current wav plays. A non-zero synthesize code stops the loop. Success is exit 0 and sound from the real speakers.

`chatterbox.play off` is how synthesize finishes without Speakers playback inside `chatterbox.exe`, so `mouth.py` owns the play queue and can overlap it with the next chunk. Optional `--diag-log DIR_OR_PATH` is Executor-facing proof tooling: phase markers plus a background GPU util sampler; default SPEAK stays quiet without it.

## Hearing one-shot

`hear.py` records the normal PC microphone for a fixed number of seconds, then runs `nemo-speech.exe transcribe` once and prints the transcript to stdout. Cable proofs still use VB-Audio. This entry is the laptop mic.

```
.\.venv\Scripts\python.exe hear.py SECONDS [--model PATH] [--device cpu] [--language TAG] [--mic NAME_OR_INDEX] [--format text] [--rate 16000] [--endpointing on|off] [--stop-history-eou-ms 1200] [--verbatim] [--no-punctuation] [--stream]
```

Defaults in the script: model `ear.gguf` beside the script, device `cpu`, format `text`, rate `16000`, endpointing `on`, stop-history end-of-utterance `1200`. The recognizer command always includes `--quiet`. `--language` is added only when the tag is non-empty. `--verbatim`, `--no-punctuation`, and `--stream` are added only when those flags are set. `SECONDS` must be greater than 0. Rate below 8000 is rejected.

Mic choice: no `--mic` uses the Windows default input when it has input channels and the name does not contain `cable`, otherwise the first input whose name contains `microphone` or `mic` and does not contain `cable`. A numeric `--mic` is that device index. A name substring matches only devices whose names do not contain `cable`.

Subprocess pipes use UTF-8 with `errors=replace`. Device-name prints do the same, so a cp1252 console does not drop a friendly name that contains a trademark byte. The temporary wav lives in a `hear_*` directory under the repo root and is removed when the process exits. `!/hear.py` keeps the script tracked. Do not commit those temp wavs.

## Prove

Prove on Windows, on this machine, with the real executables.

1. Prove one program at a time. A pass is that executable writing the file its template describes. A Mouth one-shot pass is `mouth.py` writing `mouth.txt` with `chatterbox.play off`, `chatterbox.exe` writing `HH-MM-SS-mmm_chatterbox_out_NNN.txt` plus the wav, and `mouth.py` playing those wavs on the real speakers with the next synthesize overlapped. A hearing one-shot pass is `hear.py` printing a transcript from the PC mic. It does not replace the cable proof.
2. Then prove the whole chain on the same cable. Speech goes in through `CABLE Input`. The text files carry the words. One utterance comes out of the real speakers because `chatterbox.exe` played it. Those speakers are not the cable input.
3. Run a failed proof once more. If it fails again, leave the system able to start, write what happened and what will change into the commit delta, and change approach. Do not add a harness, a mock, or a script that pretends a program ran.

## Change the code

Read the source of every program you edit, in full, and every helper it calls, in full. Follow each call to the function it names. Follow each key in each template to the line that reads it. Follow each comment, printed line, and usage line to the behavior it describes. A description that does not match the code is an error. An illogical branch, a second way to do the same work, and an unused path are errors.

Delete duplicated logic, unused parameters, and any code that overrides the text file. One behavior has one owner. Prefer fewer lines. What should remain is the executable, the text file it reads, and the files it writes.

The mouth one-shot is `mouth.py` plus the existing `chatterbox.exe`. The hearing one-shot is `hear.py` plus the existing `nemo-speech.exe`. Do not add a second synthesizer or a second recognizer. The finish line in `GOAL.md` says the five residents stay loaded. This tip still exits after one result. Reach that finish line inside the same executable. Do not add a process that starts the others.

When behavior changes, rewrite `GOAL.md`, `AGENTS.md`, `RULES.md`, `CODE_REVIEW_CHECKLIST.md`, and `BOTS.md` from zero so they match the tree. Keep them atemporal.

## What Git will take

`.gitignore` is a whitelist. `*` ignores everything until a later `!` rule names it. Tracked files stay tracked. A brand-new path is committed only after a whitelist rule names it, or it is not in the commit. `!/mouth.py` is the speak entry. `!/hear.py` is the hearing entry. `!/BOTS.md` is the role kit. All three stay in the repository.

Leave `mouth.txt`, generated audio, `hear_*` leftovers, `*_out_*.txt`, `*_chatterbox_out_*`, models, `.install`, `.venv`, `C:\tgemma`, and the other build trees uncommitted. Do not commit `*.pid`, `*.stop`, or run logs. The patterns at the bottom of `.gitignore` exist to keep those out even if a broader rule would have allowed them.

## Autonomy

You have this PC for the job: PowerShell, the compilers, the installer, and the proofs. Stay inside the Trident workspace except for a read-only lookup of the toolchain or the WASAPI friendly names. Follow `RULES.md` for commits and history. Iris commits only, on `runner-h`. Do not open a pull request. Role seating, speak, hear, and cook routing live in `BOTS.md`.
