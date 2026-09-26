# Code review checklist

Targeted review of the current `runner-h` tip, written so a session on the NVIDIA Windows worker can run it with no chat history.

Read `GOAL.md`, `AGENTS.md`, and `RULES.md` first. Those three files are the handoff. This file is the review procedure for this tip. It does not replace them.

Mark every item **Pass**, **Fail**, or **Blocked**. A Pass cites the source line or the command output that shows it. A Fail names the file, the line, and the contract it breaks. A Blocked names the missing fact (no cable, no CUDA toolkit, no build on this machine). Do not mark Pass because an older Iris run passed, and do not mark Pass because this document describes the code. Check the tree you have.

Do not open a pull request. New commits only. Never amend, rebase, squash, or force-push. A commit body is the delta for that change plus a pointer to `GOAL.md`, `AGENTS.md`, and `RULES.md`.

This review records defects. It does not close the residency gap by adding a supervisor, and it does not add a harness, a mock, or a second implementation of a role.

## How to read machine notes

`AGENTS.md` sections "This machine" and "Audio on this machine" were written on the Iris worker (Intel Iris Xe, no CUDA toolkit, VB-Audio endpoints, Realtek speakers). They are memory of that computer. Re-measure this one. A note from Iris is not a description of the NVIDIA worker and it is not an instruction to keep Vulkan, to keep `sm_61`, or to keep the Realtek device name.

Tree facts below are about this repository at the tip that added this file. Re-check them. If a line here disagrees with the source, the source wins and this checklist line is stale.

## Iris versus NVIDIA

Check these before judging a binary. The left column is what Iris recorded. The right column is what this review must measure.

| Iris recorded | What the NVIDIA review must do |
| --- | --- |
| GPU is Intel Iris Xe. `detect_gpu.ps1` printed `vulkan` because there was no CUDA toolkit. | Run `gemma\scripts\detect_gpu.ps1` here. Pass only if the printed word matches the adapters and `nvcc` you just found. Fail if you force `vulkan` because Iris had no CUDA, or force `cuda` because this PC is named NVIDIA when `nvcc` is absent. |
| `install.gemma_backend` is `auto`. On Iris that built Vulkan. | Leave `auto` unless you are deliberately changing `install.txt`. Record the backend the script printed. The brain binary under test must be one this machine linked for that backend. An `gemma-brain.exe` left in the repo root by Iris is not evidence. |
| `install.cuda_root` is `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6`. `install.cuda_max` is `12.6`. `detect_gpu.ps1` also falls back to that `v12.6` path. | Find `nvcc` on `PATH`, then under `CUDA_PATH`, then under the path in `install.txt`. Pass when those agree, or when you record which one is missing. Fail if the detector selects CUDA from a different toolkit than `install.cuda_root`, or if a newer `nvcc` is rejected by `install.cuda_max` and the review pretends the build is fine. |
| `install.cuda_architectures` is `61-real` (SASS for compute capability 6.1 only). | Read the capability from this GPU (`nvidia-smi` compute capability, or the CUDA device query). Pass when `61-real` is actually this GPU's capability, or when the mismatch is a Fail against `install.txt` rather than a silent CMake default. Fail if CMake grows a second architecture that overrides the file. |
| `install.build_gemma` is `C:\tgemma` because the Vulkan shader step hit `MAX_PATH` on a long path. | Re-check on this build. The short path may still be required on Windows. Fail if you delete it because the brain is now CUDA, without a configure that shows the long path works. Fail if you reuse a `C:\tgemma` CMake cache that was configured for Iris Vulkan and then judge that binary as this machine's CUDA build. `claim_build` in `install.py` resets the cache only when the source directory differs, not when the backend differs. The gemma stamp includes the backend, so a backend change must configure again. |
| Mouth is Vulkan (`install.mouth_ggml_vulkan on`). Brain backend is separate. | Keep that split unless `install.txt` changes. Fail if the mouth is switched to CUDA only because the brain is CUDA. The mouth must stay statically linked: `build_mouth` rejects an exe that imports `ggml.dll` or `ggml-base.dll`, because `nemo-speech.exe` keeps its own ggml DLL in the repo root. |
| `install.ear_backend` is `cpu`. | Confirm the ear build used that value. A CUDA ear is a file change plus the same DLL collision, not an automatic translation from Iris. |
| `gemma.gpu` and `chatterbox.gpu` are `0`. On Iris, Vulkan device 0 was the Iris Xe. | Enumerate CUDA devices and Vulkan devices separately. Pass when index 0 in the API the binary actually uses is the GPU you intend, and `require_gpu` in `gemma\src\brain.cpp` prints that adapter. Fail if Vulkan device 0 is an integrated GPU and the mouth silently uses it, or if the brain still prints an Iris Xe line from a binary built on the other PC. The fix for a wrong GPU index is the text file, not a hard-coded index. This is not a WASAPI device index. WASAPI stays a friendly name. |
| Capture friendly name `CABLE Output (VB-Audio Virtual Cable)`. Playback into the cable `CABLE Input (VB-Audio Virtual Cable)`. Trap endpoint `CABLE In 16ch (VB-Audio Virtual Cable)`. Speakers `Speakers (Realtek(R) Audio)`. Room mic Intel Smart Sound. | Enumerate WASAPI capture and render endpoints yourself. Pass only with the friendly names this PC lists. Fail if the proof uses the room microphone, a device index, or the 16-channel cable endpoint when a stereo cable output exists. Fail if you require the Realtek string. |
| `chatterbox.exe` plays with `PlaySoundW` on the default waveform device. Iris set that default to the Realtek speakers. | Set and then re-read the default playback device on this PC. Pass when it is the real speakers and not the cable input. The device's friendly name will differ from Iris. |
| PowerShell on Iris does not accept `&&`. Python 3.11.9, no `py` launcher, CMake 4.4.2, VS 2022 Build Tools 17.14, Vulkan SDK `1.4.357.0`. | Re-discover the shell, the compiler, CMake, the Vulkan SDK, and `nvcc`. `install.vulkan_sdk` empty means the newest SDK under `C:\VulkanSDK` that contains `Bin\glslc.exe`. Fail if you point the build at a library path that exists only on Iris. |

`gemma.gpu-layers 999`, `sense.gpu-layers 0`, `gemma.temp 1.0`, flash attention `off`, and f16 KV are values in the templates, not Iris hardware. The program must use them as written on either machine. Do not "correct" them in code because this GPU is NVIDIA.

## A. Architecture and contracts

- [ ] **A1. Five residents, one baker.** The running roles are `vad.exe`, `ear.exe`, `sense.exe`, `gemma-brain.exe`, and `chatterbox.exe`. `chatterbox-bake.exe` bakes one reference voice and exits. **Pass:** each role is still one executable, and the baker is not started as a resident. **Fail:** a second executable or a script implements the same role, or the baker stays running.

- [ ] **A2. No orchestrator.** Nothing in the running system starts the five or carries their messages. `install.py` is the installer, not a resident. `scripts\convert_t3.py`, `scripts\convert_s3.py`, and `scripts\quant.py` are converters used at build time. **Pass:** no supervisor, harness, message bus, service, pid file, or stop file exists in the source. **Fail:** any of those return, including a Python loop that launches the five.

- [ ] **A3. `ear.exe` may spawn only its recognizer.** `src\ear.cpp` starts `nemo-speech.exe transcribe` and writes that process's stdout. That child is the recognizer's engine, not a sixth role. **Pass:** the child is `nemo-speech.exe` beside `ear.exe`, and ear does not start vad, sense, gemma, or chatterbox. **Fail:** ear becomes a supervisor, or a second transcriber (a script, or a patched NeMo resident) does the same job.

- [ ] **A4. One argument, no flags.** `trident::load_settings` rejects anything except `argc == 2` with the text `usage: program file.txt`. **Pass:** each `main` goes through that function and the usage line matches. **Fail:** a flag, a help switch, a default path, or a zero-argument mode exists.

- [ ] **A5. The text file is the only settings.** There is no shared `trident.txt`. Templates are `vad.txt`, `ear.txt`, `sense.txt`, `gemma.txt`, `chatterbox.txt`, `bake.txt`. The installer reads `install.txt` only. **Pass:** each program reads keys only from its argument file. **Fail:** a program opens a second settings file, or a key from one template is read by another role.

- [ ] **A6. File values win.** `need` rejects a missing or empty required key. `cfg_key` allows empty. `cfg_on`, `cfg_int`, and `cfg_float` parse the file text and reject anything else. **Pass:** no literal replaces a number the file set, and an empty value stays empty when the template says it may be empty. **Fail:** a C++ default, a llama.cpp `common_params` default, or a `Knobs` member initializer wins over a key the user wrote. Known empty-allowed keys to trace: `ear.language`, `gemma.image`, `gemma.tensor-split`, `gemma.cpu-mask`, `gemma.cpu-mask-batch`.

- [ ] **A7. Templates match the readers.** Follow every key in each template to the line that reads it, and every comment, printed line, and usage line to the behavior it describes. **Pass:** each key is read, each comment is true, and no unread key remains. **Fail:** a comment describes a resident loop, a pid file, a tool parser, or a device index that the code does not implement, or the code implements something the template does not name.

- [ ] **A8. Text in, text out, until a tool is named.** The gate and the brain write the generation unchanged. The code does not add a token, build a tool schema, parse a tool call, or search the disk for a picture. **Pass:** `sense.cpp` and `brain.cpp` contain no tool registry and no rewrite of the generation. **Fail:** `speak`, `see`, `<tool_call>`, a history file, or a prompt wrapper returns.

- [ ] **A9. Two mouth engines, one role.** `chatterbox_make_engine` selects `gpt2::Engine` for GGUF architecture `chatterbox-gpt2` (nano, turbo) or `llama::Engine` for `chatterbox-llama` (v3). **Pass:** the architecture string is the only switch, and both engines remain. **Fail:** a variant name in code overrides the GGUF architecture, or one engine is deleted as "unused" while its variant is still in `chatterbox.txt`.

- [ ] **A10. Language is a model parameter, not a translation.** v3 (`src\llama\engine.cpp`) refuses a tag that is not in `chatterbox.tokenizer.language_tokens`, then the MTL tokenizer NFKD-normalizes and lowercases for that model. gpt2 (`src\gpt2\engine.cpp`) takes the language argument and does not use it. **Pass:** a Polish sentence is not rewritten into English; a language the GGUF lists is accepted; a language it does not list fails closed on v3. **Fail:** an English-only gate, a translation step, or a v3 refusal of a tag the model file contains.

- [ ] **A11. Same mouth knobs must reach the engine the file selected.** `chatterbox.cpp` copies every `chatterbox.*` knob into `Knobs`. The engines do not consume that struct evenly. At this tip, `end_trim` is applied only in `src\llama\engine.cpp`. `min_p`, `cfg_weight`, and `exaggeration` are used on the llama path and not in `src\gpt2`. `top_k` is used in `src\gpt2\t3.cpp` and not in `src\llama\t3.cpp`. The template's default variant is `turbo`, which is gpt2, so `chatterbox.end-trim-samples` does not affect the default mouth. **Pass:** the review records, for the variant under test, which keys change the audio and which are ignored. **Fail:** the review treats every `chatterbox.*` line as honored for turbo, or a new literal default replaces the file.

- [ ] **A12. Duplicated behavior has one owner.** Two wav writers exist (`write_wav` in `src\vad.cpp` and `chatterbox_write_wav` in `src\common\chatterbox_runtime.cpp`). Two samplers exist inside the two mouth architectures. **Pass:** the duplication is either required by the two GGUF architectures, or it is recorded as a defect with the keys that diverge (A11). **Fail:** a third writer or a scripted recognizer appears, or the two wav writers disagree on PCM layout (not 16-bit mono at the rate from the file).

## B. Residency versus one-shot

The finish line in `GOAL.md` says the five residents stay loaded, the microphone stays open, and voice activity detection runs by itself. The same file says that on this tip each executable still does one unit of work and exits, and that closing the gap does not mean adding a second program to supervise the five.

- [ ] **B1. The tip still exits.** **Pass:** `vad.cpp` sets `done` after the first finished utterance and returns. `ear.cpp`, `sense.cpp`, `gemma\src\brain.cpp`, and `chatterbox.cpp` each write one output and return. The baker writes one output and returns. **Fail:** the review reports residency as already done, or a `main` blocks forever without a contract for the next input.

- [ ] **B2. Capture is the program that can stay on the microphone.** A stay-loaded `vad.exe` would keep the WASAPI client and the Silero session and write a new `HH-MM-SS-mmm_vad_out_NNN.txt` plus wav for each finished utterance. **Pass:** any recommended change stays inside `vad.exe` and still uses `vad.device`, `vad.rate`, `vad.window`, `vad.threshold`, `vad.min-silence-ms`, and `vad.speech-pad-ms` from the file. **Fail:** a watcher process, a pid file, or a stop file is the proposed way to keep the microphone open.

- [ ] **B3. The other four do not watch a neighbor.** `GOAL.md` says the person connects outputs to the next input by editing text files, and the code does not watch a neighbor. **Pass:** a stay-loaded design, if one is judged, re-reads that program's own text file and still writes generation or audio unchanged. **Fail:** `sense.exe` tails `ear`'s output, `gemma-brain.exe` tails `sense`, or `chatterbox.exe` tails `gemma`, or any of them grows a stop file.

- [ ] **B4. No hidden memory across turns.** `sense.cpp` clears the llama memory at the start of `answer`. `brain.cpp` `reset()` clears memory, the sampler, and pending media at the start of `answer`. **Pass:** those clears stay, including in a future resident loop. **Fail:** a history file, a kept KV cache from the previous user text, or a previous picture is reused. Iris already lost picture turns that way, on the older shape: a stored sentence about a red image answered later colors.

- [ ] **B5. NeMo stay-loaded patches stay out.** `install.py` `restore_nemo` checks out `app\transcribe.cpp` and `app\live_terminal.cpp` when `TRIDENT_STAY` or `TRIDENT_FLUSH` is present, and deletes `.trident-stay` and `.trident-flush`. **Pass:** those markers are not in this repo's source, and the ear build still restores them if a clone gets patched. **Fail:** a resident mode is reintroduced inside the NeMo tree as a second ear.

- [ ] **B6. This review does not implement residency.** **Pass:** the review stops at Pass/Fail on B1–B5. **Fail:** the review adds an orchestrator, a service, or a script "so the finish line is met."

## C. Process boundaries

- [ ] **C1. Installer boundary.** `python install.py install.txt` is the only install command. It may run `chatterbox-bake.exe` while building voices. **Pass:** that bake is under `.install` cache and copies model files into the names in `chatterbox.txt`. **Fail:** `install.py` is required at runtime to pass a sample between roles, or `install.publish` is turned on.

- [ ] **C2. Publish stays off.** `install.publish` is `off`. `publish()` uploads every root `.exe` and `.dll` with `gh release upload` when it is `on`. **Pass:** the key is `off` and no review step enables it. **Fail:** a build publishes a release.

- [ ] **C3. Working directory versus file directory.** Output files are created in the current directory (`reserve_output`). Paths inside a text file resolve from that file's directory (`cfg_path` uses `settings_file.parent_path()`). **Pass:** a proof launched from another directory still finds `ear.input` beside the text file, and the output lands in the directory the reviewer chose on purpose. **Fail:** the program walks parent directories looking for settings, or writes the result next to the executable regardless of the current directory.

- [ ] **C4. `nemo-speech.exe` lifetime.** Ear creates a pipe, starts the child with `CreateProcessW`, reads stdout to EOF, and waits. Stderr is the parent's stderr. The exit code is returned and no output file is written when the child is non-zero. **Pass:** that is still the whole protocol. **Fail:** ear parses, translates, or drops the stdout, or a non-zero child still writes a success file.

- [ ] **C5. Static mouth, separate ear DLL.** After the mouth build, `pe_import_dlls` must not find `ggml.dll` or `ggml-base.dll` in `chatterbox.exe`, `chatterbox-bake.exe`, `ear.exe`, or `vad.exe`. The ear build then copies `nemo-speech.exe` and its DLLs into the repo root. **Pass:** the check still runs and the root can contain the ear's ggml DLL without the mouth importing it. **Fail:** a CUDA or Vulkan build copies a second `ggml.dll` over the ear's DLL, or the mouth starts importing `ggml.dll`.

- [ ] **C6. Brain and gate share a build and stay two processes.** Both come from `gemma\CMakeLists.txt`. `GEMMA_CUDA` is defined only for `gemma-brain`. Sense links `llama-common` and does not call `require_gpu`. **Pass:** `sense.exe` and `gemma-brain.exe` are separate images, and sense uses `sense.gpu-layers` from its file (the template says `0`). **Fail:** the processes are merged, or sense forces GPU layers in code.

## D. File-arg IPC

- [ ] **D1. Output name.** `reserve_output` uses local time `HH-MM-SS-mmm`, an underscore, the role, `_out_`, and a number from `000`. The year, month, and day are not in the name. The roles written today are `vad`, `ear`, `sense`, `gemma`, `chatterbox`, and `bake`. **Pass:** the pattern matches `GOAL.md` and the template comments, including `gemma` rather than `gemma-brain`. **Fail:** a date is added, a role is renamed in only one place, or an output is written under a fixed name such as `ear.response.txt`.

- [ ] **D2. Create-new, never replace.** The file is opened with `CREATE_NEW`. `ERROR_FILE_EXISTS` tries the next number, up to 100000, then fails. **Pass:** an existing file is left intact, including when the same clock time repeats on a later day. **Fail:** `CREATE_ALWAYS`, truncation of a pre-existing name, or a overwrite of a neighbor's output.

- [ ] **D3. Body is unchanged bytes.** `write_output` and `write_named` write the buffer as binary. Ear writes the captured stdout. Sense and gemma write the concatenated generation, skipping only end-of-generation tokens. **Pass:** an empty recognizer stdout on exit 0 still creates the file. Polish and other non-ASCII bytes are preserved. **Fail:** the text is re-encoded lossy, a think block or tool call is stripped, or an empty transcript is treated as "no turn" and no file is written.

- [ ] **D4. Chain is manual.** The person copies the wav name from the vad output into `ear.input`, copies the recognizer text into the next prompt as that model's own prompt, and copies the spoken sentence into `chatterbox.text`. **Pass:** the review can do that with the templates and no extra program. **Fail:** the code performs that copy, or the proof depends on a script that does it.

- [ ] **D5. Parser contract.** Both `src\common\config.h` and `install.py` `load_file` strip a UTF-8 BOM, skip comments, split on the first space, and accept a `<<` block ended by a line that is only `<<`. **Pass:** the six templates and `install.txt` parse under both readers the same way for the keys they share in form. **Fail:** a template key is swallowed by an unclosed `<<`, a duplicate key silently changes which value wins, or the C++ reader and the installer disagree on a key the installer rewrites (`write_bake_file`).

- [ ] **D6. Picture contract.** `gemma.image` is raw base64 or empty. `filled()` treats a whitespace-only image as empty. Non-empty data is decoded in process and passed to the mmproj already named by `gemma.mmproj`. The prompt is not edited. The template requires `<__media__>` to be present already; the C++ does not insert it and does not search the disk. **Pass:** a path, a `data:image` URL, or a filename is not opened as the picture. A missing marker fails inside tokenization without the program rewriting `gemma.text`. **Fail:** the program adds `<__media__>`, reads an image file, or keeps the previous bitmap.

- [ ] **D7. `gemma.seed -1`.** The value is cast to `uint32_t` in `apply_config`. **Pass:** on the pinned llama.cpp (`install.llama_rev`) that bit pattern still means a random seed if that is what `-1` means in this tree, and the file value is what was passed. **Fail:** the cast becomes a fixed seed and the review still describes `-1` as random, or a second seed default in `common_params` wins.

## E. Per-program trace

- [ ] **E1. vad.** Keys read: `vad.rate`, `vad.device`, `vad.model`, `vad.window`, `vad.threshold`, `vad.min-silence-ms`, `vad.speech-pad-ms`. Capture is shared-mode WASAPI. Resample is `Audio::resample`. Frames are `vad.window` samples at `vad.rate`. One wav is written beside the text file; the text file contains the wav's file name only. **Pass:** all of that is still true. **Fail:** a device index, linear interpolation, or a chunk directory from the old shape returns.

- [ ] **E2. vad hidden constants.** `pull_16k` keeps `960` native samples. Silero context length is `vad.window / 8`. Speech end uses `threshold - 0.15` plus `vad.min-silence-ms`. State shape is fixed `2 x 1 x 128`. **Pass:** `vad.threshold`, `vad.min-silence-ms`, `vad.window`, and `vad.rate` are still the values from the file, and these constants are recorded as algorithm internals. **Fail:** any of those file keys is replaced by a literal, or a different ONNX signature is silently rebound.

- [ ] **E3. Silero signature.** The session must have inputs `input`, `state`, `sr` and outputs `output`, `stateN`. `sr` is one int64. A mismatch fails with the names ONNX Runtime reported. **Pass:** that check is still in `src\common\silero_vad.cpp`, and there is no `scripts\vad.py`. **Fail:** a wrong graph is papered over, or the Python VAD returns.

- [ ] **E4. ear.** Keys read: `ear.model`, `ear.input`, `ear.device`, `ear.format`, `ear.endpointing`, `ear.stop-history-eou-ms`, `ear.language` (omitted from the command when empty), `ear.stream`, `ear.verbatim`, `ear.no-punctuation` (each flag only when `on`). **Pass:** the command is exactly those fields, quoted, with no extra defaults inserted. **Fail:** a language is invented when the key is empty, or a flag is passed when the file says `off`.

- [ ] **E5. sense.** Keys read: `sense.model`, `sense.ctx`, `sense.n-predict`, `sense.batch`, `sense.threads`, `sense.gpu-layers`, `sense.temp`, `sense.top-k`, `sense.top-p`, `sense.text`. `sense.text` is the whole prompt. Member initializers `n_batch = 512` and `n_predict = 128` are overwritten from the params that came from the file. **Pass:** the file wins, and llama.cpp defaults do not override those keys. **Fail:** the gate wraps the prompt, keeps a system string the file does not contain, or forces `gpu-layers` to 0 in code.

- [ ] **E6. gemma.** Every `gemma.*` key in `gemma.txt` is read by `apply_config` or by `main` (`gemma.text`, `gemma.image`, `gemma.mmproj-timings`). Sampling in the template is temp `1.0`, top-p `0.95`, top-k `64`, flash attention `off`, KV `f16`. The comment says a lower temperature collapses the turn. **Pass:** those values reach `common_params` unchanged. **Fail:** a code default lowers the temperature, enables flash attention, or turns on `gemma.no-host`.

- [ ] **E7. gemma GPU check.** `require_gpu` runs before load. The CUDA build prints `cuda[i]`. The Vulkan build prints `vulkan[i]`. The binary dies if GPU offload is missing. **Pass:** the line you capture on this machine names this machine's device. **Fail:** you accept an Iris Xe log, or a Vulkan log from a binary that `detect_gpu.ps1` said should be CUDA.

- [ ] **E8. chatterbox playback.** The wav is written, the text file receives the wav file name, then `PlaySoundW(..., SND_FILENAME)` plays it. There is no `SND_NODEFAULT` and no endpoint id. **Pass:** playback failure returns non-zero, and the proof checked the default device first (W1). **Fail:** the program selects the cable input in code, or a missing wav is allowed to play the system default sound and count as speech.

- [ ] **E9. bake.** Keys read match `bake.txt`, including `bake.cond-seconds`. Nano and turbo use 15, v3 uses 6, from `install.nano_cond_seconds`, `install.turbo_cond_seconds`, and `install.v3_cond_seconds` at install time, and from `bake.txt` when the baker is run by hand. The baker rewrites `bake.t3` and `bake.s3` in place and writes both paths to the output. **Pass:** unsupported architectures `chatterbox-gpt2` and `chatterbox-llama` are the only ones accepted, and the program exits. **Fail:** the baker stays resident or bakes a voice the file did not name.

## F. Build and config

- [ ] **F1. One install command.** `python install.py install.txt`. The `py` launcher is not assumed. **Pass:** the command is unchanged and it reads that file only. **Fail:** a second install script or a remembered Iris command line is required.

- [ ] **F2. Pins.** `install.ggml_rev`, `install.llama_rev`, and `install.nemo_rev` are the revisions the build checks out. Empty `install.llama_rev` would track llama.cpp `master`. Empty `install.nemo_rev` would keep the cloned revision. **Pass:** the file's pins are what `pin_sources` checks out. **Fail:** the review floats those repos to latest as an NVIDIA optimization.

- [ ] **F3. Stale CMake cache.** `claim_build` deletes a build directory whose `CMAKE_HOME_DIRECTORY` is a different source tree. **Pass:** a cache from another checkout is removed before configure. **Fail:** a binary built from another tree, or from Iris, is copied forward and called this build.

- [ ] **F4. Backend flags.** `build_gemma` sets `GGML_CUDA` and `GGML_VULKAN` from the detected or chosen backend, and passes `install.cuda_architectures` only on the CUDA path, plus `CUDA_DEFS` (`MMQ`, `cuBLAS`, flash attention, graphs, NCCL). **Pass:** the CMake cache on this machine shows the same backend `detect_gpu.ps1` printed and the architectures from `install.txt`. **Fail:** both `GGML_CUDA` and `GGML_VULKAN` are on for the brain, or an architecture other than the file's value is injected in `gemma\CMakeLists.txt`.

- [ ] **F5. CUDA version gate.** `cuda_env` rejects an `nvcc` newer than `install.cuda_max`. **Pass:** the toolkit you found is within that max, or the Fail is filed against the file value with the version you measured. **Fail:** the check is bypassed with a hard-coded Iris path.

- [ ] **F6. CPU arch comes from this CPU.** `gemma\scripts\detect_cpu.ps1` prints `/arch:AVX512`, `AVX2`, `AVX`, or empty. `install.msvc_arch` empty means that result is used. **Pass:** the flag matches this CPU. **Fail:** an `/arch` flag copied from Iris is forced in CMake.

- [ ] **F7. UTF-8 manifest.** `utf8.manifest` sets the active code page to UTF-8 and is embedded on the mouth targets and on the copied ear exe. **Pass:** the manifest is still embedded. **Fail:** a non-ASCII prompt depends on the console code page.

- [ ] **F8. What is copied to the root.** Installer copies `chatterbox.exe`, `chatterbox-bake.exe`, `ear.exe`, `vad.exe`, `onnxruntime.dll`, `nemo-speech.exe` and its DLLs, `gemma-brain.exe`, `sense.exe`, and the model files named by the templates. **Pass:** the review lists the root DLLs after a build on this machine and C5 still holds. **Fail:** models, `.install`, `.venv`, or `C:\tgemma` are committed.

- [ ] **F9. Whitelist gitignore.** `*` ignores everything until a `!` rule names it. Tracked files stay tracked. Generated audio, `*_out_*.txt`, models, `.install`, `.venv`, pid files, and stop files stay untracked. **Pass:** a new root file is named by a `!` rule or it is not in the commit. **Fail:** `git add -A` picks up a wav, a model, or a run log.

## G. Windows audio and the cable proof

Prove on this machine with the real executables. Do not use the room microphone. The person may call the cable a BB cable. If it is missing, install VB-Audio Virtual Cable. Do not substitute a different capture device.

- [ ] **G1. Enumerate.** List WASAPI capture and render friendly names (`PKEY_Device_FriendlyName`). **Pass:** the list is in the review notes, measured here. **Fail:** the Iris names are copied without a listing.

- [ ] **G2. Friendly name, shared mode, mix format.** `src\common\wasapi_capture.cpp` compares the friendly name and does not parse an all-digit string as an index. It uses a hardcoded property key `{a45c254e-df1c-4efd-8020-67d146a850e0}, 14` because Windows SDK `functiondiscoverykeys_devpkey.h` on the Iris build did not define `DEFINE_PROPERTYKEY`. It opens shared mode and reads the mix format. **Pass:** the key is still that friendly-name key, the header is still not included, and `vad.device` matches one active capture endpoint exactly. **Fail:** a device index returns, or the code includes the broken header and fails to compile.

- [ ] **G3. Mix format is float or PCM16.** Other formats take the silent branch and produce zeros, which looks like "no speech." **Pass:** you recorded `wFormatTag` / extensible subtype, channel count, and `nSamplesPerSec` for the endpoint you opened, and it is float or PCM16. **Fail:** a 24-bit or other mix is captured as silence and blamed on Silero.

- [ ] **G4. Resample is the polyphase FIR.** `Audio::resample` builds a 641-tap least-squares low-pass and does rational up/down. The vad template says this is not linear interpolation. The old Python path used `np.interp` from 48000 to 16000, aliased, and chopped one sentence into many wavs, which made ear transcripts empty. **Pass:** capture still calls `Audio::resample`, and there is no linear interpolator on that path. **Fail:** `np.interp` or a linear resampler returns.

- [ ] **G5. One utterance, then the file.** Play known speech into the cable input. Leave a silence longer than `vad.min-silence-ms` (400 in `vad.txt`). **Pass:** `vad.exe` writes one text file whose body is a wav file name, that wav exists beside it, and the process exits. **Fail:** no file, a replaced file, or more than one utterance in a single one-shot process (that would mean B1 changed).

- [ ] **G6. Ear on that wav.** Put that wav's file name into `ear.input`. The committed `ear.txt` says `utterance.wav`, which is only a placeholder. **Pass:** `ear.exe` writes `*_ear_out_*.txt` containing the recognizer text, and the words match the clip you played. An empty file with exit 0 is a real result and a Fail of the proof, not a missing run. **Fail:** the proof uses the room mic, or judges the console instead of the file.

- [ ] **G7. Gate and brain on that text.** Put the recognizer text into `sense.text` as the whole Qwen3 prompt, and into `gemma.text` as the whole Gemma 4 prompt, without the code adding tokens. **Pass:** each exe writes its generation unchanged to its own output file and exits. **Fail:** a wrapper, a tool call parser, or a picture search appears in order to make the sentence "better."

- [ ] **G8. Mouth on the real speakers.** Put one sentence into `chatterbox.text`. Default playback is the real speakers, not `CABLE Input`. **Pass:** `*_chatterbox_out_*.txt` names a wav that exists, and that wav is what was heard from the speakers. **Fail:** the default device is the cable input, so capture would hear the mouth, or the proof never checks the default device.

- [ ] **G9. Whole chain, once.** Speech enters the cable input. The text files carry the words. One utterance comes out of the real speakers because `chatterbox.exe` played it. During that proof the mouth is not playing into the cable. **Pass:** you can point at the vad, ear, and chatterbox files from that same utterance. **Fail:** a harness plays the chain, or the mouth's audio is the next vad input.

- [ ] **G10. Failed proof.** Run a failed proof once more. If it fails again, leave the system able to start, record what happened and what will change, and change approach. **Pass:** that rule is followed. **Fail:** a mock, a unit-test double, or a script that pretends an exe ran is added so the item can be marked Pass.

- [ ] **G11. No inherited cable pass.** The commit that introduced one text file per program recorded that no cable proof was run. Older Iris passes (playlist rows, a spoken "The picture is red") belonged to the shared-settings, tool-call shape. **Pass:** this review either produces new files on this machine or marks the cable items Blocked. **Fail:** those old passes are cited as a Pass for this tip.

## H. GPU and CUDA rediscovery

Do this before trusting `gemma-brain.exe` or `sense.exe` in the repo root.

- [ ] **H1. Adapters.** Record every `Win32_VideoController` name. **Pass:** the list is from this PC. **Fail:** it says Iris Xe because `AGENTS.md` says Iris Xe.

- [ ] **H2. Detector.** Run `gemma\scripts\detect_gpu.ps1`. It prints `cuda` only when some adapter name matches `NVIDIA` and some `nvcc.exe` exists on `PATH`, or under `CUDA_PATH`, or at the hardcoded `v12.6` path. Otherwise it prints `vulkan`. **Pass:** you show the adapters, the `nvcc` path, and the one word it printed, and they agree. **Fail:** the word is taken from Iris notes.

- [ ] **H3. Binary matches the detector.** Rebuild, or prove the existing exe was linked on this machine for that backend. Capture the `cuda[` or `vulkan[` line from `gemma-brain.exe`. **Pass:** the line matches H2 and names this GPU. **Fail:** a Vulkan Iris binary is timed as if it were the CUDA build, or `C:\tgemma` is reused across backends without a reconfigure (see the table above).

- [ ] **H4. Capability versus `61-real`.** Measure the GPU's compute capability. Compare it to `install.cuda_architectures`. **Pass:** they match, or the mismatch is a Fail with both numbers written down. **Fail:** the review assumes a modern GPU cannot run the pinned SASS and "fixes" it with a CMake default the file does not contain.

- [ ] **H5. Toolkit versus `cuda_max`.** Compare `nvcc --version` to `install.cuda_max` and `install.cuda_toolset`. **Pass:** configure uses the `nvcc` from `install.cuda_root` and the version is allowed. **Fail:** PATH `nvcc` and `install.cuda_root` differ and the review does not notice.

- [ ] **H6. Mouth GPU is Vulkan device `chatterbox.gpu`, not the CUDA device.** Enumerate Vulkan devices. **Pass:** device `chatterbox.gpu` is the GPU that should bake and speak, and an Iris `uma: 1` log is not used to move the brain off the GPU. **Fail:** the mouth is rebuilt CUDA-only to match the brain, or the brain is moved to CPU because a Vulkan bake print said UMA.

- [ ] **H7. Sense stays on its file.** Template `sense.gpu-layers` is `0` and the comment says CPU only. The binary may still be CUDA-linked if the brain build is CUDA. **Pass:** the process is separate and the layer count comes from the file. **Fail:** sense is switched onto the GPU in code because NVIDIA hardware is present.

- [ ] **H8. Offload actually offloads.** `gemma.gpu-layers 999` means every layer on the GPU the binary was built for. `llama_supports_gpu_offload` must be true. **Pass:** the stderr timings or the backend log show the layers on that GPU. **Fail:** a CUDA build silently runs the model on the CPU and the review still marks H8 Pass.

## I. Security and hygiene

- [ ] **I1. No shell.** Ear uses `CreateProcessW` with the exe path as `lpApplicationName`. The installer uses argument arrays. **Pass:** no `cmd.exe`, `system()`, or `ShellExecute` runs a text-file value. **Fail:** a command string is handed to the shell.

- [ ] **I2. Quoting.** `quote()` in `src\ear.cpp` wraps double quotes and backslash-escapes inner quotes. Windows command-line parsing also treats `\"` specially. **Pass:** a path or language containing a quote cannot break out of the argument, or the limitation is a recorded Fail with an example. **Fail:** the review calls the quoting safe without reading it.

- [ ] **I3. The text file is operator input.** It is trusted as the user's own parameters. The program must still not search the disk for a picture, a second settings file, or a tool definition the user did not write. **Pass:** A8 and D6 hold. **Fail:** a path walk "helps" find `gemma.image` or a missing model.

- [ ] **I4. Path join.** `cfg_path` joins the settings directory with the value. A value the user wrote with `..` is still the user's value. **Pass:** the program does not invent `..` or an absolute path of its own. **Fail:** code searches parent directories for config.

- [ ] **I5. `exe_dir` buffer.** `GetModuleFileNameW` is called with `MAX_PATH` and the length is not checked. **Pass:** ear still finds `nemo-speech.exe` beside itself for a normal install path, or a long-path failure is recorded. **Fail:** a truncated path launches the wrong exe and the review marks ear as Pass.

- [ ] **I6. Secrets and releases.** No tokens belong in the templates or the commit. Model URLs are public Hugging Face pins. **Pass:** `install.publish` is off and the diff has no credentials. **Fail:** a token, a local model, or a release upload is committed.

- [ ] **I7. Generated files stay out.** Do not commit wavs, `*_out_*.txt`, `.install`, `.venv`, `C:\tgemma`, `*.pid`, `*.stop`, or run logs. `reference.wav` is the tracked bake reference, not a run output. **Pass:** `git status` shows only the review's intended files. **Fail:** a proof artifact is staged.

- [ ] **I8. Process law.** No pull request. No amend, rebase, squash, reset, or force-push. Commit messages do not paste `GOAL.md`, this checklist, or another computer's endpoint list. **Pass:** the branch is `runner-h` and the new commit is a delta. **Fail:** history is rewritten or a PR is opened.

## J. Tests and proofs

There is no unit-test suite. `install.llama_build_tests`, `install.ear_tests`, and `install.mouth_ggml_build_tests` are `off`. That is the contract. The proof is the executable and the file it writes.

- [ ] **J1. No harness.** **Pass:** the review does not add a test runner, a mock exe, or a Python stand-in for a role. **Fail:** any of those appear so a box can be checked without the real binary.

- [ ] **J2. One program, then the chain.** Order is G5, G6, G7, G8, then G9. **Pass:** each step's file exists before the next step is judged. **Fail:** the chain is judged from memory or from an Iris log.

- [ ] **J3. Vision, if exercised, uses the file.** A picture turn puts raw base64 in `gemma.image` and already contains `<__media__>` in `gemma.text`. **Pass:** the output file is the generation, and the pixels are the base64 that was written. **Fail:** the test points `gemma.image` at `picture.png` or relies on `gemma.history.txt`. Those were the old shape, and they failed green and blue after red.

- [ ] **J4. Blocked is allowed.** Missing cable, missing toolchain, or no build on this machine is Blocked, with the command output. **Pass:** Blocked items are not converted into Pass by skipping to the room mic. **Fail:** a Blocked proof is marked Pass.

## K. Defects already visible on this tip

Confirm each one. Do not treat this list as a patch set. A fix that adds an orchestrator fails B6 even if the symptom goes away.

- [ ] **K1. One-shot exit.** B1. This is the finish-line gap, still open.

- [ ] **K2. Mouth knob split.** A11. `end_trim` does not run on gpt2, so the default `turbo` mouth ignores `chatterbox.end-trim-samples`. `top_k` does not run on v3. `min_p`, `cfg_weight`, and `exaggeration` do not run on nano or turbo.

- [ ] **K3. `PlaySoundW` does not name an endpoint and does not set `SND_NODEFAULT`.** G8 and E8. The default device is a machine setting. On a machine with both an integrated GPU and an NVIDIA GPU, the audio default is still a separate fact from the CUDA device.

- [ ] **K4. Non-float, non-PCM16 capture becomes silence.** G3.

- [ ] **K5. `gemma.image` whitespace is treated as empty.** D6. An empty value is empty; a whitespace body is not the empty string the template describes. Confirm whether that collapse is acceptable.

- [ ] **K6. Text-only Gemma still loads `gemma.mmproj`.** That is the named projector, not a picture search. **Pass:** a missing projector fails closed and no image file is opened. **Fail:** load failure causes a directory search.

- [ ] **K7. `temperature` of `0` is not applied.** Both mouth samplers scale logits only when temperature is `> 0` and not `1`. A written `0` behaves like no scaling. **Pass:** the template's non-zero temperatures are what the proof uses, and `0` is recorded. **Fail:** the review claims `0` means greedy.

- [ ] **K8. Cable proof for this tip is not on record.** G11.

- [ ] **K9. Iris binaries and `C:\tgemma` may already sit on a shared disk or in this checkout.** H3. Do not judge them as the NVIDIA build.

## Review closeout

Write the result as a list of ids with Pass, Fail, or Blocked and the evidence. Do not paste this checklist into a commit message.

A Fail is a defect report. Change code only when the review task also asks for a fix. Any fix stays inside the role's executable and that role's text file. The residency gap, if it is fixed later, is the same executable staying loaded. It is not a new process.

Leave generated audio, output text files, models, `.install`, `.venv`, and `C:\tgemma` uncommitted.
