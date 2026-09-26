# AGENTS

Read `GOAL.md`, this file, and `RULES.md` before editing. This is the handoff for a session with no earlier chat. Commit messages after the commit that added these files carry only the delta for that change. Do not reconstruct the project by copying old commit bodies forward.

Work on branch `runner-h` in the local clone of https://github.com/wgabrys88/Trident. Fetch and update `origin/runner-h` before changing anything. The final commit stays on `runner-h`.

## This machine

This checkout is on Wojciech's private Windows worker. PowerShell on this PC does not accept `&&` as a statement separator. Use `;` or separate commands.

Toolchain found here:

- `python` is Python 3.11.9. The `py` launcher is not on `PATH`.
- CMake 4.4.2 and Git are on `PATH`.
- Visual Studio Build Tools 2022 (17.14) are at `C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools`. `install.txt` asks for generator `Visual Studio 17 2022`, architecture `x64`.
- Vulkan SDK `C:\VulkanSDK\1.4.357.0`. `install.vulkan_sdk` is empty, so the installer picks the newest SDK that contains `Bin\glslc.exe`.
- The GPU is Intel Iris Xe. There is no CUDA toolkit. `gemma\scripts\detect_gpu.ps1` prints `cuda` only when an NVIDIA adapter and `nvcc` are both present; otherwise it prints `vulkan`. With `install.gemma_backend` set to `auto`, this machine builds the brain and the gate with Vulkan.
- `install.build_gemma` is `C:\tgemma` because the Vulkan shader step fails on a long path. Leave that short path in place.

Re-check these facts if the machine changes. A note from another computer is memory of that computer.

## Audio on this machine

Enumerate endpoints yourself. `vad.exe` matches `vad.device` to the WASAPI capture friendly name (`PKEY_Device_FriendlyName`) and fails if that string is not an active capture endpoint. It does not use a device index. It opens the device in shared mode, reads the mix format, and resamples with `Audio::resample` to `vad.rate` (16000 in `vad.txt`).

Endpoints present when these docs were written:

- Capture for proofs: `CABLE Output (VB-Audio Virtual Cable)`. That string is already `vad.device` in `vad.txt`.
- Playback into the cable: `CABLE Input (VB-Audio Virtual Cable)`.
- Also present, and not the proof pair: `CABLE In 16ch (VB-Audio Virtual Cable)`.
- Real speakers: `Speakers (Realtek(R) Audio)`.
- Room microphone: the Intel Smart Sound microphone array. Do not use it.

`chatterbox.exe` plays the wav with `PlaySoundW` on the default waveform device. Before a chain proof, that default must be the real speakers, not `CABLE Input`, so the mouth is not the next thing capture hears.

If the virtual cable is missing, install VB-Audio Virtual Cable. The person may call it a BB cable. Do not switch the proof to the room microphone.

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
- `nemo-speech.exe` and the DLLs beside it. `ear.exe` launches `nemo-speech.exe transcribe` and writes the captured stdout.
- `gemma-brain.exe` and `sense.exe` from `C:\tgemma`.
- Model files named by the templates (`vad.model`, `ear.model`, `sense.model`, `gemma.model`, `gemma.mmproj`, and the nano, turbo, and v3 GGUF pairs).

This checkout already has those outputs, `.venv`, and `.install`. They are local. Do not commit them. If a build directory's CMake cache was generated for a different source tree, remove that cache and configure this source. A compile error is fixed in this repository's source by reading the error back to the include or the call that caused it. Do not satisfy a broken system header by adding a library path that exists only on one computer.

## Run one program

Each program takes one text file and no flags. The usage line is `usage: program file.txt`. Run it from the directory where the result should appear. Paths inside the text file are resolved from that file's directory, not from the current directory.

| Command | Reads | Writes in the current directory |
| --- | --- | --- |
| `vad.exe vad.txt` | WASAPI capture named by `vad.device`. Silero ONNX, window 512. | One finished utterance, then exit. `HH-MM-SS-mmm_vad_out_NNN.txt` contains the wav file name. The wav sits beside it. |
| `ear.exe ear.txt` | `ear.input`, one wav. | `HH-MM-SS-mmm_ear_out_NNN.txt`, the recognizer text unchanged. Empty `ear.language` omits `--language`. |
| `sense.exe sense.txt` | `sense.text`, the whole prompt, including the Qwen3 turn markers the user wrote. CPU, `sense.gpu-layers` 0. | `HH-MM-SS-mmm_sense_out_NNN.txt`, the generation unchanged. |
| `gemma-brain.exe gemma.txt` | `gemma.text`, the whole prompt in Gemma 4's own form. `gemma.image` is raw base64 or empty. When the image is set, the prompt must already contain `<__media__>`. | `HH-MM-SS-mmm_gemma_out_NNN.txt`, the generation unchanged. |
| `chatterbox.exe chatterbox.txt` | `chatterbox.variant` is `nano`, `turbo`, or `v3`. The GGUF architecture selects the engine. `chatterbox.text` is spoken as written. | `HH-MM-SS-mmm_chatterbox_out_NNN.txt` names the wav. The wav is played on the default speakers. |
| `chatterbox-bake.exe bake.txt` | `bake.t3`, `bake.s3`, `bake.reference`. `bake.cond-seconds` is 15 for nano and turbo, 6 for v3. | Rewrites those two model files in place, writes `HH-MM-SS-mmm_bake_out_NNN.txt` with both paths, and exits. |

The output file is created with `CREATE_NEW`. The number starts at `000`. An existing name is kept and the next number is used. Nothing in the program watches another program.

To run the chain, connect the files yourself. Play known speech into `CABLE Input`. Point `vad.device` at `CABLE Output`. Put the wav name `vad.exe` wrote into `ear.input`. Put the recognizer text into the next prompt file as that model's own prompt, still unchanged by code. Put the sentence the brain wrote into `chatterbox.text`. Judge each step by the file that step wrote.

## Prove

Prove on Windows, on this machine, with the real executables.

1. Prove one program at a time. A pass is that executable writing the file its template describes.
2. Then prove the whole chain on the same cable. Speech goes in through `CABLE Input`. The text files carry the words. One utterance comes out of the real speakers because `chatterbox.exe` played it. Those speakers are not the cable input.
3. Run a failed proof once more. If it fails again, leave the system able to start, write what happened and what will change into the commit delta, and change approach. Do not add a harness, a mock, or a script that pretends a program ran.

## Change the code

Read the source of every program you edit, in full, and every helper it calls, in full. Follow each call to the function it names. Follow each key in each template to the line that reads it. Follow each comment, printed line, and usage line to the behavior it describes. A description that does not match the code is an error. An illogical branch, a second way to do the same work, and an unused path are errors.

Delete duplicated logic, unused parameters, and any code that overrides the text file. One behavior has one owner. Prefer fewer lines. What should remain is the executable, the text file it reads, and the files it writes.

The finish line in `GOAL.md` says the five residents stay loaded. This tip still exits after one result. Reach that finish line inside the same executable. Do not add a process that starts the others.

## What Git will take

`.gitignore` is a whitelist. `*` ignores everything until a later `!` rule names it. Tracked files stay tracked, which is why edits to these three documents show up after this commit. A brand-new path is committed only after a whitelist rule names it, or it is not in the commit.

Leave generated audio, `*_out_*.txt`, models, `.install`, `.venv`, `C:\tgemma`, and the other build trees uncommitted. Do not commit `*.pid`, `*.stop`, or run logs. The patterns at the bottom of `.gitignore` exist to keep those out even if a broader rule would have allowed them.

## Autonomy

You have this PC for the job: PowerShell, the compilers, the installer, and the proofs. Stay inside the Trident workspace except for a read-only lookup of the toolchain or the WASAPI friendly names. Follow `RULES.md` for commits and history. Do not open a pull request.
