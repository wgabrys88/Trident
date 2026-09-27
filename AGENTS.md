# AGENTS

Read `GOAL.md`, this file, `RULES.md`, and `BOTS.md` before editing. This is the handoff for a session with no earlier chat. `BOTS.md` is the seat index and the routes. Recreate a seat from `artifacts/reference/seats/` only. The local assistant track is `artifacts/reference/tracks/IRIS_ASSISTANT.md`. The LAN job router in `artifacts/reference/tracks/DEVICE_ROUTER.md` is not built. The five drawings are captioned in `artifacts/reference/design/README.md`. `CODE_REVIEW_CHECKLIST.md` is the review procedure. A commit message carries only the delta for that change.

## Branch

Work on `runner-h` in the local clone of https://github.com/wgabrys88/Trident. Fetch and pull `origin/runner-h` before changing anything. The commit stays on `runner-h`. Iris commits only: the commit is made on this Iris checkout. Never open a pull request. Never set `starting_ref`. Never amend, rebase, squash, or force-push. Never reset, except the local Nvidia Ask teardown in the Ask section.

## Paths

Primary cook, proofs, and commits are this Iris checkout.

- Iris path: `C:\Users\eb-wjt\Downloads\Jarvis\Trident`. Cursor worker: `trident-iris`. EB-W machine id: `84403f85-8162-436b-9567-dd9255e82a60`.
- Nvidia Cursor worker: `trident-nvidia`. Checkout: `C:\Users\px-wjt\Downloads\Jarvis\Trident`. Leave it alone unless Wojciech authorizes. Review-only when that review is routed. Ask uses it only when the go names Nvidia.
- A Grok Linux box is not a primary for cook or proofs.
- Locked LAN addresses live in `artifacts/reference/tracks/DEVICE_ROUTER.md`. That router is not built. Do not implement it from a drawing.

## This machine

PowerShell on this PC does not accept `&&`. Use `;` or separate commands. Re-measure if the machine changes. A note from another computer is memory of that computer.

- `python` is Python 3.11.9. The `py` launcher is not on `PATH`.
- CMake 4.4.2 and Git are on `PATH`.
- Visual Studio Build Tools 2022 (17.14) are at `C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools`. `install.txt` asks for generator `Visual Studio 17 2022`, architecture `x64`.
- Vulkan SDK: `C:\VulkanSDK\1.4.357.0`. `install.vulkan_sdk` is empty, so the installer picks the newest SDK that contains `Bin\glslc.exe`.
- The GPU is Intel Iris Xe. There is no CUDA toolkit. `gemma\scripts\detect_gpu.ps1` prints `cuda` only when an NVIDIA adapter and `nvcc` are both present; otherwise `vulkan`. With `install.gemma_backend` set to `auto`, this machine builds the brain and the gate with Vulkan.
- `install.build_gemma` is `C:\tgemma`. The Vulkan shader step fails on a long path. Leave that short path in place.

## Install

```
git clone https://github.com/wgabrys88/Trident.git
git checkout runner-h
git pull origin runner-h
python install.py install.txt
```

On an existing checkout, pull `runner-h`. `python install.py install.txt` is the only install command. The installer reads `install.txt` and nothing else for its parameters. It creates `.venv` when needed and re-runs itself with that interpreter. It clones the pinned ggml, llama.cpp, and NeMo-Speech trees under `.install`, configures CMake, builds, downloads the models named in `install.txt`, and bakes nano, turbo, and v3 with `chatterbox-bake.exe`. `install.publish` is `off`. Leave it off.

What lands in the repository root: `vad.exe`, `ear.exe`, `chatterbox.exe`, `chatterbox-bake.exe`, `onnxruntime.dll`, `nemo-speech.exe` and the DLLs beside it, plus `gemma-brain.exe` and `sense.exe` copied from `C:\tgemma`, and the model files the templates name. The mouth links ggml statically. `ear.exe` and `hear.py` launch `nemo-speech.exe transcribe`.

This checkout already has those outputs, `.venv`, and `.install`. They are local. Do not commit them. If a build directory's CMake cache was generated for a different source tree, remove that cache and configure this source. Fix a compile error in this repository's source. Do not add a library path that exists only on one computer.

## Audio

Cable proofs and `hear.py` are different entries. Enumerate endpoints before a proof. `vad.exe` matches `vad.device` to the WASAPI capture friendly name (`PKEY_Device_FriendlyName`) and fails if that string is not an active capture endpoint. It uses no device index. Shared mode, mix format, resample to `vad.rate` (16000 in `vad.txt`).

- Cable capture: `CABLE Output (VB-Audio Virtual Cable)`. That string is `vad.device` in `vad.txt`.
- Cable playback: `CABLE Input (VB-Audio Virtual Cable)`.
- Also present: `CABLE In 16ch (VB-Audio Virtual Cable)`. Proofs use the Output/Input pair.
- Real speakers: `Speakers (Realtek(R) Audio)`.
- Room microphone: the Intel Smart Sound microphone array. Cable proofs leave it unused. `hear.py` uses that array.

`chatterbox.exe` with `chatterbox.play on` plays the wav with `PlaySoundW` on the default waveform device. `mouth.py` plays with `PlaySoundW` on that same default (`SND_FILENAME | SND_NODEFAULT`). There is no device flag. Before a chain proof or a Mouth one-shot, set that default to `Speakers (Realtek(R) Audio)`.

If the virtual cable is missing, install VB-Audio Virtual Cable. The person may call it a BB cable. Keep cable proofs on that cable.

## Run

Each resident takes one text file and no flags. Usage is `usage: program file.txt`. Run it from the directory where the result should appear. Paths inside the text file resolve from that file's directory.

| Command | Reads | Writes in the current directory |
| --- | --- | --- |
| `vad.exe vad.txt` | WASAPI capture named by `vad.device`. Silero ONNX, window 512. | One utterance, then exit. `HH-MM-SS-mmm_vad_out_NNN.txt` names the wav. |
| `ear.exe ear.txt` | `ear.input`, one wav. | `HH-MM-SS-mmm_ear_out_NNN.txt`, recognizer text unchanged. Empty `ear.language` omits `--language`. |
| `sense.exe sense.txt` | `sense.text`, the whole prompt. CPU, `sense.gpu-layers` 0. | `HH-MM-SS-mmm_sense_out_NNN.txt`, generation unchanged. |
| `gemma-brain.exe gemma.txt` | `gemma.text` in Gemma 4 form. `gemma.image` is raw base64 or empty. An image requires `<__media__>` already in the prompt. | `HH-MM-SS-mmm_gemma_out_NNN.txt`, generation unchanged. |
| `chatterbox.exe chatterbox.txt` | Variant `nano`, `turbo`, or `v3`. `chatterbox.text` spoken as written. `chatterbox.play` is `on` or `off`. | `HH-MM-SS-mmm_chatterbox_out_NNN.txt` names the wav. `on` plays it on the default speakers. `off` skips PlaySound. |
| `chatterbox-bake.exe bake.txt` | `bake.t3`, `bake.s3`, `bake.reference`. Cond-seconds 15 for nano and turbo, 6 for v3. | Rewrites those two model files. `HH-MM-SS-mmm_bake_out_NNN.txt`. |

One-shots from the repository root:

```
.\.venv\Scripts\python.exe mouth.py [--model nano|turbo|v3] [--lang TAG] TEXT [TEXT ...]
.\.venv\Scripts\python.exe hear.py SECONDS [--model PATH] [--device cpu] [--language TAG] [--mic NAME_OR_INDEX]
.\.venv\Scripts\python.exe gemma.py [--image PATH] [--verbose] "Question."
.\.venv\Scripts\python.exe qwen.py [--verbose] "Question."
.\.venv\Scripts\python.exe assistant.py [--once] [--seconds N] [--brain qwen|gemma] [--model nano|turbo|v3] [--lang TAG] [--image PATH]
.\.venv\Scripts\python.exe assistant.py --once --text "Say only: ready." --model nano
```

`mouth.py` does not synthesize. One process takes every TEXT chunk. `--model` defaults to `nano`. Omitted `--lang` is `en` for nano and turbo, and `pl` for v3. It rewrites `mouth.txt` with `chatterbox.play off`, runs `.\chatterbox.exe mouth.txt` once per chunk, and plays each wav on the default speakers while the next chunk synthesizes. `chatterbox.txt` stays untouched. Empty text, an unknown model, an empty language, and a text line whose entire content is `<<` exit 2 before the first `chatterbox.exe`.

`hear.py` records the PC microphone for `SECONDS`, runs `nemo-speech.exe transcribe` once, and prints the transcript on stdout. Further flags (`--format`, `--rate`, `--endpointing`, `--stop-history-eou-ms`, `--verbatim`, `--no-punctuation`, `--stream`) match that recognizer. Default model is `ear.gguf` beside the script. Cable proofs stay on the virtual cable.

`gemma.py` copies `gemma.txt` knobs into `gemma_run.txt` and leaves `gemma.txt` untouched. It opens the Gemma 4 thought channel, runs `gemma-brain.exe gemma_run.txt`, and prints the generation, thinking included. The child's stdout and stderr are discarded. `--verbose` passes that stderr through. Prefer `trident-nvidia` for speed; Iris Vulkan runs the same executable. `--image` is a file path stored as raw base64 in `gemma.image`. The script inserts `<__media__>` when the question lacks it.

`qwen.py` copies `sense.txt` knobs into `sense_run.txt`, wraps the question as Qwen3 turns, and runs `sense.exe sense_run.txt`. Stdout is the generation file only. The child's stdout and stderr are discarded. `--verbose` passes that stderr through. Qwen3-0.6B has no vision. `qwen.py --image` exits 2. Image input is `gemma.py --image` on Nvidia.

`assistant.py` is the local voice loop on Iris. It calls the repo virtualenv to run `hear.py`, then `qwen.py` or `gemma.py`, then `mouth.py`. Default brain is `qwen`. `--brain gemma` is opt-in: prefer Nvidia; Iris Vulkan can run it and can be slow. `--image` requires `--brain gemma`. `--seconds` defaults to 8 and is the `hear.py` duration. `--text` skips the mic and runs one brain-then-mouth round. `--once` is one heard turn. With neither flag, it loops until a heard `quit`, `exit`, or `stop`, or until Ctrl-C. Mouth language defaults match `mouth.py`: omitted `--lang` is `en` for nano and turbo, and `pl` for v3. The brain stdout is printed unchanged. The wrapper passes `--verbose` to the brain one-shot and prints that stderr only when the child exits non-zero. The mouth receives the speakable span of that stdout, chunked at 65 English words or 55 Polish words, in one `mouth.py` process. The script does not start the five residents, does not keep them loaded, and makes no network call. Grok stays off this path.

Sense sets batch threads from `sense.threads`. There is no `sense.threads-batch` key. Gemma reads `gemma.threads-batch` from its file.

The resident output file is created with `CREATE_NEW`. The number starts at `000`. An existing name is kept and the next number is used. Nothing in a resident watches another resident. Connect that chain by editing text files. Judge each resident by the file it wrote. Judge `hear.py`, `gemma.py`, and `qwen.py` by stdout, and a Mouth one-shot by the speakers as well. Judge `assistant.py` by the brain stdout and by `mouth.py` on the speakers. `assistant.py` passes one-shot text on the command line. That is not the residents' meeting place.

## Ask

Trident Ask is V2. The recreate file is `artifacts/reference/seats/ASK.md`. On Iris (`trident-iris`), run `qwen.py` in this shell. Do not launch a Cloud Agent or a Cursor coding agent for an Iris ask. Nvidia (`trident-nvidia`) is one Cloud Agent only, model `composer-2.5`, `fast` false, and only when the go names Nvidia. That prompt is the exact PowerShell or cmd line. The return is the exit code, the stdout answer, and a short stderr summary: at most 20 non-tensor lines, or a byte count plus the first and last 5 lines. A loader log is not a result. Repo edits, commits, pull requests, recovery agents, and multi-agent chains are out. An image ask is `gemma.py --image` on Nvidia.

After every Nvidia ASK GO Cloud Agent run, success or fail, tear that checkout down before FINAL. On the trident-nvidia Trident cwd: `git fetch origin runner-h`, then `git status -sb` and `git status --porcelain`. If HEAD is not `origin/runner-h` or porcelain is non-empty: `git checkout runner-h`, then `git reset --hard origin/runner-h`. Local reset only. Never force-push. Do not keep agent churn. Re-check that porcelain is empty and HEAD matches `origin/runner-h`. FINAL includes the tip SHA, the exit code, stdout, the short stderr summary, and nvidia git clean yes/no (before, then after, when a reset ran). A Cursor UI line such as `Changes +N/-M across N files` is not proof of a good ask and does not replace git status. `git status --porcelain` is the source of truth. Ask does not commit, push, open a pull request, create a recovery agent, or fix the tree. Teardown is reset to origin, not a second cook. Ask runs it on the worker shell after the Cloud Agent finishes. The agent prompt stays the exact command. If Ask cannot run git on that worker, FINAL is BLOCKER dirty-unknown so Executor can COOK the same reset. Prefer Ask running the hard reset on the same worker shell when the Cloud Agent path cannot.

## Prove

Prove on this Windows machine with the real executables.

1. One program at a time. A pass is that executable writing the file its template describes. A Mouth pass is `mouth.py` writing `mouth.txt` with `chatterbox.play off`, `chatterbox.exe` writing the out file and the wav, and sound from the real speakers. A hear pass is `hear.py` printing a transcript from the PC mic. An assistant dry pass is `assistant.py --once --text` printing the brain stdout and `mouth.py` reaching the speakers. That dry pass does not use the mic.
2. Then the whole chain on the cable. Speech goes in through `CABLE Input`. The text files carry the words. One utterance comes out of `Speakers (Realtek(R) Audio)` because the mouth played it.
3. Run a failed proof once more. If it fails again, leave the system able to start, write what happened and what will change into the commit delta, and change approach. Do not add a harness, a mock, or a script that pretends a program ran.

## Change the code

Read the source of every program you edit, in full, and every helper it calls, in full. Follow each key to the line that reads it. A description that does not match the code is an error. Delete duplicated logic, unused parameters, and any code that overrides the text file. One behavior has one owner.

The mouth one-shot is `mouth.py` plus `chatterbox.exe`. The hearing one-shot is `hear.py` plus `nemo-speech.exe`. The brain one-shots are `gemma.py` plus `gemma-brain.exe`, and `qwen.py` plus `sense.exe`. `assistant.py` only runs those Python one-shots. Do not add a second synthesizer or a second recognizer. Do not add a process that starts the five residents. The finish line in `GOAL.md` is those residents staying loaded, inside the same executable. `assistant.py` does not close that gap. The device router does not close that gap.

When behavior changes, rewrite `GOAL.md`, `AGENTS.md`, `RULES.md`, `CODE_REVIEW_CHECKLIST.md`, and `BOTS.md` from zero so they match the tree. Rewrite the seat file and the track file that state the same behavior in the same change. Keep them atemporal.

## Git

`.gitignore` is a whitelist. `*` ignores everything until a later `!` rule names it. Tracked files stay tracked. A new path is committed only after a whitelist rule names it. `!/mouth.py`, `!/hear.py`, `!/gemma.py`, `!/qwen.py`, and `!/assistant.py` keep those one-shots in the repository. The design PNGs, track files, and seat files stay tracked by the `!` rules that name them.

Leave `mouth.txt`, `gemma_run.txt`, `sense_run.txt`, generated audio, `hear_*` leftovers, `*_out_*.txt`, `*_chatterbox_out_*`, models, `.install`, `.venv`, `C:\tgemma`, and the other build trees uncommitted. Do not commit `*.pid`, `*.stop`, or run logs.

## Autonomy

You have this PC: PowerShell, the compilers, the installer, and the proofs. Stay inside the Trident workspace except for a read-only lookup of the toolchain or the WASAPI friendly names. Follow `RULES.md`. Iris commits only, on `runner-h`. Do not open a pull request.
