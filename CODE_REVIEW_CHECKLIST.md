# Code review checklist

Pass, Fail, or Blocked review of the current `runner-h` tree. A session with no chat history can run it, including a review on an NVIDIA Windows worker.

Read `GOAL.md`, `AGENTS.md`, `RULES.md`, and `BOTS.md` first. Those files are the handoff. This file is the procedure. It does not replace them.

Mark every item **Pass**, **Fail**, or **Blocked**. A Pass cites the source line or the command output that shows it. A Fail names the file, the line, and the contract it breaks. A Blocked names the missing fact (no cable, no CUDA toolkit, no build on this machine). Do not mark Pass because another machine's run passed, and do not mark Pass because this document describes the code. Check the tree you have. If a line here disagrees with the source, the source wins.

Do not open a pull request. New commits only. Never amend, rebase, squash, or force-push. A commit body is the delta for that change plus a pointer to `GOAL.md`, `AGENTS.md`, `RULES.md`, and `BOTS.md`. Iris is the commit checkout. A review on another machine does not commit unless that task says to commit from there.

This review records defects. It does not close the residency gap by adding a supervisor, and it does not add a harness, a mock, or a second implementation of a role.

## How to read machine notes

`AGENTS.md` "This machine" and "Audio on this machine" were measured on the Iris worker (Intel Iris Xe, no CUDA toolkit, VB-Audio endpoints, Realtek speakers). Re-measure the computer you are on. An Iris note is not a description of an NVIDIA worker, and it is not an instruction to keep Vulkan, to keep `sm_61`, or to keep the Realtek device name.

## Iris versus NVIDIA

Check these before judging a binary. The left column is the Iris measurement to treat as a memory. The right column is what this review measures here.

| Iris memory | What this review measures |
| --- | --- |
| GPU is Intel Iris Xe. `detect_gpu.ps1` prints `vulkan` when there is no CUDA toolkit. | Run `gemma\scripts\detect_gpu.ps1` here. Pass only if the printed word matches the adapters and `nvcc` you just found. Fail if you force `vulkan` because Iris had no CUDA, or force `cuda` because this PC is named NVIDIA when `nvcc` is absent. |
| `install.gemma_backend` is `auto`. On Iris that selects Vulkan. | Leave `auto` unless you are deliberately changing `install.txt`. Record the backend the script printed. The brain binary under test must be one this machine linked for that backend. A `gemma-brain.exe` left in the repo root by another machine is not evidence. |
| `install.cuda_root` is `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6`. `install.cuda_max` is `12.6`. `detect_gpu.ps1` also falls back to that `v12.6` path. | Find `nvcc` on `PATH`, then under `CUDA_PATH`, then under the path in `install.txt`. Pass when those agree, or when you record which one is missing. Fail if the detector selects CUDA from a different toolkit than `install.cuda_root`, or if a newer `nvcc` is rejected by `install.cuda_max` and the review pretends the build is fine. |
| `install.cuda_architectures` is `61-real` (SASS for compute capability 6.1 only). | Read the capability from this GPU (`nvidia-smi` compute capability, or the CUDA device query). Pass when `61-real` is this GPU's capability, or when the mismatch is a Fail against `install.txt` rather than a silent CMake default. Fail if CMake grows a second architecture that overrides the file. |
| `install.build_gemma` is `C:\tgemma`. The Vulkan shader step fails on a long Windows path. | Re-check on this build. The short path may still be required. Fail if you delete it because the brain is now CUDA, without a configure that shows the long path works. Fail if you reuse a `C:\tgemma` CMake cache configured for a different backend and then judge that binary as this machine's build. `claim_build` in `install.py` resets the cache only when the source directory differs. The gemma stamp includes the backend, so a backend change must configure again. |
| Mouth is Vulkan (`install.mouth_ggml_vulkan on`). Brain backend is separate. | Keep that split unless `install.txt` changes. Fail if the mouth is switched to CUDA only because the brain is CUDA. The mouth stays statically linked: `build_mouth` rejects an exe that imports `ggml.dll` or `ggml-base.dll`, because `nemo-speech.exe` keeps its own ggml DLL in the repo root. |
| `install.ear_backend` is `cpu`. | Confirm the ear build used that value. A CUDA ear is a file change plus the same DLL collision, not an automatic translation from Iris. |
| `gemma.gpu` and `chatterbox.gpu` are `0`. On Iris, Vulkan device 0 was the Iris Xe. | Enumerate CUDA devices and Vulkan devices separately. Pass when index 0 in the API the binary actually uses is the GPU you intend, and `require_gpu` in `gemma\src\brain.cpp` prints that adapter. Fail if Vulkan device 0 is an integrated GPU and the mouth silently uses it, or if the brain prints another machine's adapter. The fix for a wrong GPU index is the text file, not a hard-coded index. This is not a WASAPI device index. WASAPI stays a friendly name. |
| Cable capture `CABLE Output (VB-Audio Virtual Cable)`. Cable playback `CABLE Input (VB-Audio Virtual Cable)`. Trap endpoint `CABLE In 16ch (VB-Audio Virtual Cable)`. Speakers `Speakers (Realtek(R) Audio)`. Room mic Intel Smart Sound, used by `hear.py`, not by cable proofs. | Enumerate WASAPI capture and render endpoints yourself. Pass only with the friendly names this PC lists. Fail if a cable proof uses the room microphone, a device index, or the 16-channel cable endpoint when a stereo cable output exists. Fail if you require the Realtek string on a machine whose speakers have another name. |
| Playback is `PlaySoundW` on the default waveform device. Iris sets that default to the real speakers. | Set and then re-read the default playback device on this PC. Pass when it is the real speakers and not the cable input. The friendly name will differ from Iris. |
| PowerShell on Iris does not accept `&&`. Python 3.11, no `py` launcher, VS 2022 Build Tools, Vulkan SDK under `C:\VulkanSDK`. | Re-discover the shell, the compiler, CMake, the Vulkan SDK, and `nvcc`. `install.vulkan_sdk` empty means the newest SDK under `C:\VulkanSDK` that contains `Bin\glslc.exe`. Fail if you point the build at a library path that exists only on Iris. |

`gemma.gpu-layers 999`, `sense.gpu-layers 0`, `gemma.temp 1.0`, flash attention `off`, and f16 KV are values in the templates, not Iris hardware. The program must use them as written on either machine. Do not correct them in code because this GPU is NVIDIA.

## A. Architecture and contracts

- [ ] **A1. Five residents, one baker.** The running roles are `vad.exe`, `ear.exe`, `sense.exe`, `gemma-brain.exe`, and `chatterbox.exe`. `chatterbox-bake.exe` bakes one reference voice and exits. `mouth.py` is the speak one-shot: it writes `mouth.txt` and runs `chatterbox.exe` once per chunk. It does not synthesize. `hear.py` is the hearing one-shot. It is not a sixth resident. **Pass:** each role is still one executable, the baker is not a resident, and the two scripts only drive the existing executables. **Fail:** a second synthesizer, a second recognizer, a second resident, or a baker that stays running.

- [ ] **A2. No orchestrator.** Nothing in the running system starts the five or carries their messages. `install.py` is the installer. `mouth.py` starts only `chatterbox.exe`, once per chunk, then exits with the first non-zero synthesize code or 0. `hear.py` starts only `nemo-speech.exe transcribe`, once, and prints stdout. `scripts\convert_t3.py`, `scripts\convert_s3.py`, and `scripts\quant.py` are build-time converters. **Pass:** no supervisor, harness, message bus, service, pid file, or stop file exists in the source. **Fail:** any of those return, including a Python loop that launches the five.

- [ ] **A3. `ear.exe` may spawn only its recognizer.** `src\ear.cpp` starts `nemo-speech.exe transcribe` and writes that process's stdout. That child is the recognizer's engine, not a sixth role. `hear.py` may start the same executable once for the mic one-shot. **Pass:** the child is `nemo-speech.exe` beside the caller, and neither path starts vad, sense, gemma, or chatterbox. **Fail:** ear or `hear.py` becomes a supervisor.

- [ ] **A4. One argument, no flags, on residents.** `trident::load_settings` rejects anything except `argc == 2` with the text `usage: program file.txt`. **Pass:** each resident `main` goes through that function and the usage line matches. **Fail:** a resident flag, a help switch, a default path, or a zero-argument mode exists. `mouth.py` and `hear.py` are the one-shot CLIs named in `GOAL.md`; their flags do not become resident flags.

- [ ] **A5. The text file is the only settings.** There is no shared `trident.txt`. Templates are `vad.txt`, `ear.txt`, `sense.txt`, `gemma.txt`, `chatterbox.txt`, `bake.txt`. The installer reads `install.txt` only. **Pass:** each resident reads keys only from its argument file. **Fail:** a program opens a second settings file, or a key from one template is read by another role. `mouth.py` may read `chatterbox.txt` and write `mouth.txt` for `chatterbox.exe`.

- [ ] **A6. File values win.** `need` rejects a missing or empty required key. `cfg_key` allows empty. `cfg_on`, `cfg_int`, and `cfg_float` parse the file text and reject anything else. **Pass:** no literal replaces a number the file set, and an empty value stays empty when the template says it may be empty. **Fail:** a C++ default, a llama.cpp `common_params` default, or a `Knobs` member initializer wins over a key the user wrote. Known empty-allowed keys to trace: `ear.language`, `gemma.image`, `gemma.tensor-split`, `gemma.cpu-mask`, `gemma.cpu-mask-batch`.

- [ ] **A7. Templates match the readers.** Follow every key in each template to the line that reads it, and every comment, printed line, and usage line to the behavior it describes. **Pass:** each key is read, each comment is true, and no unread key remains. `chatterbox.play` is read in `src\chatterbox.cpp`. **Fail:** a comment describes a resident loop, a pid file, a tool parser, or a device index that the code does not implement, or the code implements something the template does not name.

- [ ] **A8. Text in, text out, until a tool is named.** The gate and the brain write the generation unchanged. The code does not add a token, build a tool schema, parse a tool call, or search the disk for a picture. **Pass:** `sense.cpp` and `brain.cpp` contain no tool registry and no rewrite of the generation. **Fail:** `speak`, `see`, `<tool_call>`, a history file, or a prompt wrapper returns.

- [ ] **A9. Two mouth engines, one role.** `chatterbox_make_engine` selects `gpt2::Engine` for GGUF architecture `chatterbox-gpt2` (nano, turbo) or `llama::Engine` for `chatterbox-llama` (v3). **Pass:** the architecture string is the only switch, and both engines remain. **Fail:** a variant name in code overrides the GGUF architecture, or one engine is deleted as unused while its variant is still in `chatterbox.txt`.

- [ ] **A10. Language is a model parameter, not a translation.** v3 (`src\llama\engine.cpp`) refuses a tag that is not in `chatterbox.tokenizer.language_tokens`, then the MTL tokenizer NFKD-normalizes and lowercases for that model. gpt2 (`src\gpt2\engine.cpp`) takes the language argument and does not use it. **Pass:** a Polish sentence is not rewritten into English; a language the GGUF lists is accepted; a language it does not list fails closed on v3. **Fail:** an English-only gate, a translation step, or a v3 refusal of a tag the model file contains.

- [ ] **A11. Same mouth knobs must reach the engine the file selected.** `chatterbox.cpp` copies every `chatterbox.*` knob into `Knobs`. The engines do not consume that struct evenly. `end_trim` is applied in `src\llama\engine.cpp`. `min_p`, `cfg_weight`, and `exaggeration` are used on the llama path and not in `src\gpt2`. `top_k` is used in `src\gpt2\t3.cpp` and not in `src\llama`. The template's default variant is `turbo`, which is gpt2, so `chatterbox.end-trim-samples` does not affect the default mouth. **Pass:** the review records, for the variant under test, which keys change the audio and which are ignored. **Fail:** the review treats every `chatterbox.*` line as honored for turbo, or a new literal default replaces the file.

- [ ] **A12. Duplicated behavior has one owner.** Two wav writers exist (`write_wav` in `src\vad.cpp` and `chatterbox_write_wav` in `src\common\chatterbox_runtime.cpp`). Two samplers exist inside the two mouth architectures. **Pass:** the duplication is either required by the two GGUF architectures, or it is recorded as a defect with the keys that diverge (A11). **Fail:** a third writer or a scripted synthesizer appears, or the two wav writers disagree on PCM layout (16-bit mono at the rate from the file).

- [ ] **A13. One-shot scripts stay thin.** `mouth.py` copies `chatterbox.txt` into `mouth.txt` with `chatterbox.play off` and runs `chatterbox.exe`. `hear.py` records a wav and runs `nemo-speech.exe transcribe` once. **Pass:** neither script synthesizes or transcribes in Python. **Fail:** a Python TTS stack, a second NeMo binding, or a script that edits the recognizer text.

## B. Residency versus one-shot

The finish line in `GOAL.md` says the five residents stay loaded, the microphone stays open, and voice activity detection runs by itself. The same file says each executable still does one unit of work and exits, and that closing the gap does not mean adding a second program to supervise the five. `mouth.py` and `hear.py` do not close that gap.

- [ ] **B1. The tip still exits.** **Pass:** `vad.cpp` returns after the first finished utterance. `ear.cpp`, `sense.cpp`, `gemma\src\brain.cpp`, and `chatterbox.cpp` each write one output and return. The baker writes one output and returns. `mouth.py` returns after its chunks. `hear.py` returns after one transcript. **Fail:** the review reports residency as already done, or a `main` blocks forever without a contract for the next input.

- [ ] **B2. Capture is the program that can stay on the microphone.** A stay-loaded `vad.exe` would keep the WASAPI client and the Silero session and write a new `HH-MM-SS-mmm_vad_out_NNN.txt` plus wav for each finished utterance. **Pass:** any recommended change stays inside `vad.exe` and still uses `vad.device`, `vad.rate`, `vad.window`, `vad.threshold`, `vad.min-silence-ms`, and `vad.speech-pad-ms` from the file. **Fail:** a watcher process, a pid file, or a stop file is the proposed way to keep the microphone open. `hear.py` fixed-duration capture is not that resident.

- [ ] **B3. The other four do not watch a neighbor.** The person connects outputs to the next input by editing text files. **Pass:** a stay-loaded design, if one is judged, re-reads that program's own text file and still writes generation or audio unchanged. **Fail:** `sense.exe` tails ear's output, `gemma-brain.exe` tails sense, or `chatterbox.exe` tails gemma, or any of them grows a stop file.

- [ ] **B4. No hidden memory across turns.** `sense.cpp` clears the llama memory at the start of `answer`. `brain.cpp` `reset()` clears memory, the sampler, and pending media at the start of `answer`. **Pass:** those clears stay, including in a future resident loop. **Fail:** a history file, a kept KV cache from the previous user text, or a previous picture is reused.

- [ ] **B5. NeMo stay-loaded patches stay out.** `install.py` `restore_nemo` checks out `app\transcribe.cpp` and `app\live_terminal.cpp` when `TRIDENT_STAY` or `TRIDENT_FLUSH` is present, and deletes `.trident-stay` and `.trident-flush`. **Pass:** those markers are not in this repo's source, and the ear build still restores them if a clone gets patched. **Fail:** a resident mode is reintroduced inside the NeMo tree as a second ear.

- [ ] **B6. This review does not implement residency.** **Pass:** the review stops at Pass/Fail on B1–B5. **Fail:** the review adds an orchestrator, a service, or a script so the finish line is met.

## C. Process boundaries

- [ ] **C1. Installer boundary.** `python install.py install.txt` is the only install command. It may run `chatterbox-bake.exe` while building voices. **Pass:** that bake is under the `.install` cache and copies model files into the names in `chatterbox.txt`. **Fail:** `install.py` is required at runtime to pass a sample between roles, or `install.publish` is turned on.

- [ ] **C2. Publish stays off.** `install.publish` is `off`. `publish()` uploads every root `.exe` and `.dll` with `gh release upload` when it is `on`. **Pass:** the key is `off` and no review step enables it. **Fail:** a build publishes a release.

- [ ] **C3. Working directory versus file directory.** Output files are created in the current directory (`reserve_output`). Paths inside a text file resolve from that file's directory (`cfg_path` uses `settings_file.parent_path()`). **Pass:** a proof launched from another directory still finds `ear.input` beside the text file, and the output lands in the directory the reviewer chose on purpose. **Fail:** the program walks parent directories looking for settings, or writes the result next to the executable regardless of the current directory.

- [ ] **C4. `nemo-speech.exe` lifetime.** Ear creates a pipe, starts the child with `CreateProcessW`, reads stdout to EOF, and waits. Stderr is the parent's stderr. The exit code is returned and no output file is written when the child is non-zero. `hear.py` runs the same executable once with argument list, `shell=False`, UTF-8 pipes, and `errors=replace`, then writes stdout to its own stdout. **Pass:** those are still the whole protocols. **Fail:** ear or `hear.py` parses, translates, or drops the transcript, or a non-zero ear child still writes a success file.

- [ ] **C5. Static mouth, separate ear DLL.** After the mouth build, `pe_import_dlls` must not find `ggml.dll` or `ggml-base.dll` in `chatterbox.exe`, `chatterbox-bake.exe`, `ear.exe`, or `vad.exe`. The ear build then copies `nemo-speech.exe` and its DLLs into the repo root. **Pass:** the check still runs and the root can contain the ear's ggml DLL without the mouth importing it. **Fail:** a CUDA or Vulkan build copies a second `ggml.dll` over the ear's DLL, or the mouth starts importing `ggml.dll`.

- [ ] **C6. Brain and gate share a build and stay two processes.** Both come from `gemma\CMakeLists.txt`. `GEMMA_CUDA` is defined only for `gemma-brain`. Sense links `llama-common` and does not call `require_gpu`. **Pass:** `sense.exe` and `gemma-brain.exe` are separate images, and sense uses `sense.gpu-layers` from its file (the template says `0`). **Fail:** the processes are merged, or sense forces GPU layers in code.

## D. File-arg IPC

- [ ] **D1. Output name.** `reserve_output` uses local time `HH-MM-SS-mmm`, an underscore, the role, `_out_`, and a number from `000`. The year, month, and day are not in the name. The roles are `vad`, `ear`, `sense`, `gemma`, `chatterbox`, and `bake`. **Pass:** the pattern matches `GOAL.md` and the template comments, including `gemma` rather than `gemma-brain`. **Fail:** a date is added, a role is renamed in only one place, or an output is written under a fixed name such as `ear.response.txt`. `hear.py` prints stdout and does not use this pattern.

- [ ] **D2. Create-new, never replace.** The file is opened with `CREATE_NEW`. `ERROR_FILE_EXISTS` tries the next number, up to 100000, then fails. **Pass:** an existing file is left intact, including when the same clock time repeats on a later day. **Fail:** `CREATE_ALWAYS`, truncation of a pre-existing name, or an overwrite of a neighbor's output.

- [ ] **D3. Body is unchanged bytes.** `write_output` and `write_named` write the buffer as binary. Ear writes the captured stdout. Sense and gemma write the concatenated generation, skipping only end-of-generation tokens. `hear.py` writes the recognizer stdout to its stdout. **Pass:** an empty recognizer stdout on exit 0 still creates the ear file. Polish and other non-ASCII bytes are preserved. **Fail:** the text is re-encoded lossy, a think block or tool call is stripped, or an empty transcript is treated as "no turn" and no file is written.

- [ ] **D4. Chain is manual.** The person copies the wav name from the vad output into `ear.input`, copies the recognizer text into the next prompt as that model's own prompt, and copies the spoken sentence into `chatterbox.text` when driving the mouth from the template. One or more sentences can instead go through `mouth.py`. **Pass:** the resident chain still has no program that watches a neighbor, and `mouth.py` does not read vad, ear, sense, or gemma output. **Fail:** code copies one resident's output into the next role, or a chain proof depends on a script that does that copy.

- [ ] **D5. Parser contract.** Both `src\common\config.h` and `install.py` `load_file` strip a UTF-8 BOM, skip comments, split on the first space, and accept a `<<` block ended by a line that is only `<<`. `mouth.py` drops the same keys and refuses a TEXT line that is only `<<`. **Pass:** the templates and `install.txt` parse under both readers the same way for the keys they share in form. **Fail:** a template key is swallowed by an unclosed `<<`, a duplicate key silently changes which value wins, or the C++ reader and the installer disagree on a key the installer rewrites (`write_bake_file`).

- [ ] **D6. Picture contract.** `gemma.image` is raw base64 or empty. `filled()` treats a whitespace-only image as empty. Non-empty data is decoded in process and passed to the mmproj already named by `gemma.mmproj`. The prompt is not edited. The template requires `<__media__>` to be present already; the C++ does not insert it and does not search the disk. **Pass:** a path, a `data:image` URL, or a filename is not opened as the picture. **Fail:** the program adds `<__media__>`, reads an image file, or keeps the previous bitmap.

- [ ] **D7. `gemma.seed -1`.** The value is cast to `uint32_t` in `apply_config`. **Pass:** on the pinned llama.cpp (`install.llama_rev`) that bit pattern still means a random seed if that is what `-1` means in this tree, and the file value is what was passed. **Fail:** the cast becomes a fixed seed and the review still describes `-1` as random, or a second seed default in `common_params` wins.

## E. Per-program trace

- [ ] **E1. vad.** Keys read: `vad.rate`, `vad.device`, `vad.model`, `vad.window`, `vad.threshold`, `vad.min-silence-ms`, `vad.speech-pad-ms`. Capture is shared-mode WASAPI. Resample is `Audio::resample`. Frames are `vad.window` samples at `vad.rate`. One wav is written beside the text file; the text file contains the wav's file name only. **Pass:** all of that is still true. **Fail:** a device index, linear interpolation, or a chunk directory returns.

- [ ] **E2. vad hidden constants.** `pull_16k` is called with a keep of `960` native samples. Silero context length is `vad.window / 8`. Speech end uses `threshold - 0.15` plus `vad.min-silence-ms`. State shape is fixed `2 x 1 x 128`. **Pass:** `vad.threshold`, `vad.min-silence-ms`, `vad.window`, and `vad.rate` are still the values from the file, and these constants are recorded as algorithm internals. **Fail:** any of those file keys is replaced by a literal, or a different ONNX signature is silently rebound.

- [ ] **E3. Silero signature.** The session must have inputs `input`, `state`, `sr` and outputs `output`, `stateN`. `sr` is one int64. A mismatch fails with the names ONNX Runtime reported. **Pass:** that check is still in `src\common\silero_vad.cpp`, and there is no `scripts\vad.py`. **Fail:** a wrong graph is papered over, or a Python VAD returns.

- [ ] **E4. ear.** Keys read: `ear.model`, `ear.input`, `ear.device`, `ear.format`, `ear.endpointing`, `ear.stop-history-eou-ms`, `ear.language` (omitted from the command when empty), `ear.stream`, `ear.verbatim`, `ear.no-punctuation` (each flag only when `on`). **Pass:** the command is exactly those fields, quoted, with no extra defaults inserted. **Fail:** a language is invented when the key is empty, or a flag is passed when the file says `off`.

- [ ] **E5. sense.** Keys read: `sense.model`, `sense.ctx`, `sense.n-predict`, `sense.batch`, `sense.threads`, `sense.gpu-layers`, `sense.temp`, `sense.top-k`, `sense.top-p`, `sense.text`. `sense.text` is the whole prompt. Member initializers `n_batch = 512` and `n_predict = 128` are overwritten from the params that came from the file. **Pass:** the file wins, and llama.cpp defaults do not override those keys. **Fail:** the gate wraps the prompt, keeps a system string the file does not contain, or forces `gpu-layers` to 0 in code.

- [ ] **E6. gemma.** Every `gemma.*` key in `gemma.txt` is read by `apply_config` or by `main` (`gemma.text`, `gemma.image`, `gemma.mmproj-timings`). Sampling in the template is temp `1.0`, top-p `0.95`, top-k `64`, flash attention `off`, KV `f16`. **Pass:** those values reach `common_params` unchanged. **Fail:** a code default lowers the temperature, enables flash attention, or turns on `gemma.no-host`.

- [ ] **E7. gemma GPU check.** `require_gpu` runs before load. The CUDA build prints `cuda[i]`. The Vulkan build prints `vulkan[i]`. The binary dies if GPU offload is missing. **Pass:** the line you capture on this machine names this machine's device. **Fail:** you accept another machine's adapter line, or a Vulkan log from a binary that `detect_gpu.ps1` said should be CUDA.

- [ ] **E8. chatterbox playback and `chatterbox.play`.** The key is required and is `on` or `off` (`cfg_on`). The wav is written and the text file receives the wav file name either way. `on` calls `PlaySoundW(..., SND_FILENAME)` with no `SND_NODEFAULT` and no endpoint id. `off` skips that call. **Pass:** `off` returns 0 after the wav exists, `on` returns non-zero when PlaySound fails, and the proof checked the default device (G1). **Fail:** the program selects the cable input in code, or a missing wav is allowed to play the system default sound and count as speech.

- [ ] **E9. bake.** Keys read match `bake.txt`, including `bake.cond-seconds`. Nano and turbo use 15, v3 uses 6, from `install.nano_cond_seconds`, `install.turbo_cond_seconds`, and `install.v3_cond_seconds` at install time, and from `bake.txt` when the baker is run by hand. The baker rewrites `bake.t3` and `bake.s3` in place and writes both paths to the output. **Pass:** architectures `chatterbox-gpt2` and `chatterbox-llama` are the ones accepted, and the program exits. **Fail:** the baker stays resident or bakes a voice the file did not name.

- [ ] **E10. `mouth.py` overlap.** Validate every TEXT before the first `chatterbox.exe`. Same `--model` and `--lang` for every chunk. Each chunk rewrites `mouth.txt` from `chatterbox.txt` with `chatterbox.play off`, UTF-8, no BOM, and runs `.\chatterbox.exe mouth.txt` with `shell=False`. Playback is `PlaySoundW` with `SND_FILENAME | SND_NODEFAULT` on the default device. The next chunk is synthesized while the current wav plays. A non-zero synthesize exit stops the loop. `chatterbox.txt` is not overwritten. Optional `--diag-log` must not change Speakers play or overlap when omitted. **Pass:** two chunks produce two chatterbox outputs, `mouth.txt` ends on `chatterbox.play off`, and the speakers (not the cable) are what played. **Fail:** `mouth.txt` leaves play `on`, playback is into `CABLE Input`, or Python synthesizes the audio.

- [ ] **E11. `hear.py` one-shot.** Positional `SECONDS` must be greater than 0. Defaults: model `ear.gguf` beside the script, `--device cpu`, `--format text`, `--rate 16000`, `--endpointing on`, `--stop-history-eou-ms 1200`, and `--quiet` always. Language is added only when non-empty. With no `--mic`, prefer the Windows default input when its name does not contain `cable`, else the first non-cable input whose name contains `microphone` or `mic`. A name substring skips cable names. A numeric `--mic` is that index. Subprocess capture is UTF-8 with `errors=replace`. Stdout is the transcript. The exit code is the recognizer's. **Pass:** a short run names a non-cable mic on stderr and prints stdout unchanged. **Fail:** the default path captures VB-Cable, the transcript is rewritten, or the script speaks. **Blocked:** no mic, or `nemo-speech.exe` is missing.

## F. Build and config

- [ ] **F1. One install command.** `python install.py install.txt`. The `py` launcher is not assumed. **Pass:** the command is unchanged and it reads that file only. **Fail:** a second install script or a remembered command line from another PC is required.

- [ ] **F2. Pins.** `install.ggml_rev`, `install.llama_rev`, and `install.nemo_rev` are the revisions the build checks out. Empty `install.llama_rev` would track llama.cpp `master`. Empty `install.nemo_rev` would keep the cloned revision. **Pass:** the file's pins are what `pin_sources` checks out. **Fail:** the review floats those repos to latest as a hardware optimization.

- [ ] **F3. Stale CMake cache.** `claim_build` deletes a build directory whose `CMAKE_HOME_DIRECTORY` is a different source tree. **Pass:** a cache from another checkout is removed before configure. **Fail:** a binary built from another tree, or from another machine, is copied forward and called this build.

- [ ] **F4. Backend flags.** `build_gemma` sets `GGML_CUDA` and `GGML_VULKAN` from the detected or chosen backend, and passes `install.cuda_architectures` only on the CUDA path, plus `CUDA_DEFS` (`MMQ`, `cuBLAS`, flash attention, graphs, NCCL). **Pass:** the CMake cache on this machine shows the same backend `detect_gpu.ps1` printed and the architectures from `install.txt`. **Fail:** both `GGML_CUDA` and `GGML_VULKAN` are on for the brain, or an architecture other than the file's value is injected in `gemma\CMakeLists.txt`.

- [ ] **F5. CUDA version gate.** `cuda_env` rejects an `nvcc` newer than `install.cuda_max`. **Pass:** the toolkit you found is within that max, or the Fail is filed against the file value with the version you measured. **Fail:** the check is bypassed with a hard-coded path from another machine.

- [ ] **F6. CPU arch comes from this CPU.** `gemma\scripts\detect_cpu.ps1` prints `/arch:AVX512`, `AVX2`, `AVX`, or empty. `install.msvc_arch` empty means that result is used. **Pass:** the flag matches this CPU. **Fail:** an `/arch` flag copied from another machine is forced in CMake.

- [ ] **F7. UTF-8 manifest.** `utf8.manifest` sets the active code page to UTF-8 and is embedded on the mouth targets and on the copied ear exe. **Pass:** the manifest is still embedded. **Fail:** a non-ASCII prompt depends on the console code page. `hear.py` UTF-8 pipes are a separate check (E11).

- [ ] **F8. What is copied to the root.** Installer copies `chatterbox.exe`, `chatterbox-bake.exe`, `ear.exe`, `vad.exe`, `onnxruntime.dll`, `nemo-speech.exe` and its DLLs, `gemma-brain.exe`, `sense.exe`, and the model files named by the templates. **Pass:** the review lists the root DLLs after a build on this machine and C5 still holds. **Fail:** models, `.install`, `.venv`, or `C:\tgemma` are committed.

- [ ] **F9. Whitelist gitignore.** `*` ignores everything until a `!` rule names it. Tracked files stay tracked. `!/mouth.py`, `!/hear.py`, and `!/BOTS.md` are what let those paths be committed. `mouth.txt`, generated audio, `*_out_*.txt`, `*_chatterbox_out_*`, models, `.install`, `.venv`, pid files, and stop files stay untracked. **Pass:** a new root file is named by a `!` rule or it is not in the commit, and `mouth.py`, `hear.py`, and `BOTS.md` are not excluded. **Fail:** `git add -A` picks up a wav, a model, a run log, or `mouth.txt`, or a rule hides one of those three tracked paths.

## G. Windows audio and the cable proof

Prove on this machine with the real executables. Cable proofs do not use the room microphone. The person may call the cable a BB cable. If it is missing, install VB-Audio Virtual Cable. Do not substitute a different capture device for a cable proof. `hear.py` is the separate mic one-shot (E11, G13).

- [ ] **G1. Enumerate.** List WASAPI capture and render friendly names (`PKEY_Device_FriendlyName`). **Pass:** the list is in the review notes, measured here. **Fail:** another machine's names are copied without a listing.

- [ ] **G2. Friendly name, shared mode, mix format.** `src\common\wasapi_capture.cpp` compares the friendly name and does not parse an all-digit string as an index. The friendly-name property key is hardcoded `{a45c254e-df1c-4efd-8020-67d146a850e0}, 14`. It opens shared mode and reads the mix format. **Pass:** the key is still that friendly-name key, `vad.device` matches one active capture endpoint exactly, and the cable proof uses the stereo cable output. **Fail:** a device index returns.

- [ ] **G3. Mix format is float or PCM16.** Other formats take the silent sample branch and produce zeros, which looks like "no speech." **Pass:** you recorded `wFormatTag` / extensible subtype, channel count, and `nSamplesPerSec` for the endpoint you opened, and it is float or PCM16. **Fail:** a 24-bit or other mix is captured as silence and blamed on Silero.

- [ ] **G4. Resample is the polyphase FIR.** `Audio::resample` builds a 641-tap low-pass and does rational up/down. **Pass:** capture still calls `Audio::resample`, and there is no linear interpolator on that path. **Fail:** a linear resampler returns.

- [ ] **G5. One utterance, then the file.** Play known speech into the cable input. Leave a silence longer than `vad.min-silence-ms` (400 in `vad.txt`). **Pass:** `vad.exe` writes one text file whose body is a wav file name, that wav exists beside it, and the process exits. **Fail:** no file, a replaced file, or more than one utterance in a single one-shot process (that would mean B1 changed).

- [ ] **G6. Ear on that wav.** Put that wav's file name into `ear.input`. The committed `ear.txt` says `utterance.wav`, which is only a placeholder. **Pass:** `ear.exe` writes `*_ear_out_*.txt` containing the recognizer text, and the words match the clip you played. An empty file with exit 0 is a real result and a Fail of the proof, not a missing run. **Fail:** the proof uses the room mic, or judges the console instead of the file.

- [ ] **G7. Gate and brain on that text.** Put the recognizer text into `sense.text` as the whole Qwen3 prompt, and into `gemma.text` as the whole Gemma 4 prompt, without the code adding tokens. **Pass:** each exe writes its generation unchanged to its own output file and exits. **Fail:** a wrapper, a tool call parser, or a picture search appears in order to make the sentence "better."

- [ ] **G8. Mouth on the real speakers.** For the resident path, put one sentence into `chatterbox.text` with `chatterbox.play on`. Default playback is the real speakers, not `CABLE Input`. **Pass:** `*_chatterbox_out_*.txt` names a wav that exists, and that wav is what was heard from the speakers. **Fail:** the default device is the cable input, so capture would hear the mouth, or the proof never checks the default device.

- [ ] **G9. Whole chain, once.** Speech enters the cable input. The text files carry the words. One utterance comes out of the real speakers because `chatterbox.exe` played it. During that proof the mouth is not playing into the cable. **Pass:** you can point at the vad, ear, and chatterbox files from that same utterance. **Fail:** a harness plays the chain, or the mouth's audio is the next vad input.

- [ ] **G10. Failed proof.** Run a failed proof once more. If it fails again, leave the system able to start, record what happened and what will change, and change approach. **Pass:** that rule is followed. **Fail:** a mock, a unit-test double, or a script that pretends an exe ran is added so the item can be marked Pass.

- [ ] **G11. No inherited cable pass.** A cable Pass is files this review produced on this machine. **Pass:** this review either produces those files here or marks the cable items Blocked. **Fail:** another machine's run, or a run from an older program shape, is cited as a Pass for this tree.

- [ ] **G12. Mouth one-shot overlap.** Run `mouth.py` with two TEXT chunks on the default speakers. **Pass:** exit 0, two chatterbox output files and wavs, `mouth.txt` contains `chatterbox.play off`, and the sound came from the speakers while the next synthesize overlapped the current play. **Fail:** play stayed `on` inside `mouth.txt`, a Python synthesizer spoke, or playback went into the cable.

- [ ] **G13. `hear.py` on the PC mic.** Run `.\.venv\Scripts\python.exe hear.py` for a few seconds with no cable `--mic`. **Pass:** stderr names a non-cable mic and stdout is the transcript. **Fail:** the default device is VB-Cable, or the review treats a rewritten summary as the transcript. **Blocked:** no non-cable mic, or `nemo-speech.exe` is absent. This item does not satisfy G5–G9.

## H. GPU and CUDA rediscovery

Do this before trusting `gemma-brain.exe` or `sense.exe` in the repo root. Re-measure here. Do not copy an Iris log.

- [ ] **H1. Adapters.** Record every `Win32_VideoController` name. **Pass:** the list is from this PC. **Fail:** it says Iris Xe because `AGENTS.md` says Iris Xe, on a machine that is not Iris.

- [ ] **H2. Detector.** Run `gemma\scripts\detect_gpu.ps1`. It prints `cuda` only when some adapter name matches `NVIDIA` and some `nvcc.exe` exists on `PATH`, or under `CUDA_PATH`, or at the `v12.6` path in the script. Otherwise it prints `vulkan`. **Pass:** you show the adapters, the `nvcc` path, and the one word it printed, and they agree. **Fail:** the word is taken from another machine's notes.

- [ ] **H3. Binary matches the detector.** Rebuild, or prove the existing exe was linked on this machine for that backend. Capture the `cuda[` or `vulkan[` line from `gemma-brain.exe`. **Pass:** the line matches H2 and names this GPU. **Fail:** a binary built on another machine is timed as if it were this build, or `C:\tgemma` is reused across backends without a reconfigure.

- [ ] **H4. Capability versus `61-real`.** Measure the GPU's compute capability. Compare it to `install.cuda_architectures`. **Pass:** they match, or the mismatch is a Fail with both numbers written down. **Fail:** the review assumes a GPU cannot run the pinned SASS and changes it with a CMake default the file does not contain.

- [ ] **H5. Toolkit versus `cuda_max`.** Compare `nvcc --version` to `install.cuda_max` and `install.cuda_toolset`. **Pass:** configure uses the `nvcc` from `install.cuda_root` and the version is allowed. **Fail:** PATH `nvcc` and `install.cuda_root` differ and the review does not notice.

- [ ] **H6. Mouth GPU is Vulkan device `chatterbox.gpu`, not the CUDA device.** Enumerate Vulkan devices. **Pass:** device `chatterbox.gpu` is the GPU that should bake and speak. **Fail:** the mouth is rebuilt CUDA-only to match the brain, or the brain is moved to CPU because a Vulkan bake print mentioned UMA.

- [ ] **H7. Sense stays on its file.** Template `sense.gpu-layers` is `0`. The binary may still be CUDA-linked if the brain build is CUDA. **Pass:** the process is separate and the layer count comes from the file. **Fail:** sense is switched onto the GPU in code because NVIDIA hardware is present.

- [ ] **H8. Offload actually offloads.** `gemma.gpu-layers 999` means every layer on the GPU the binary was built for. `llama_supports_gpu_offload` must be true. **Pass:** the stderr timings or the backend log show the layers on that GPU. **Fail:** a CUDA build silently runs the model on the CPU and the review still marks H8 Pass.

## I. Security and hygiene

- [ ] **I1. No shell.** Ear uses `CreateProcessW` with the exe path as `lpApplicationName`. The installer, `mouth.py`, and `hear.py` use argument arrays. `mouth.py` and `hear.py` set `shell=False`. **Pass:** no `cmd.exe`, `system()`, or `ShellExecute` runs a text-file value. **Fail:** a command string is handed to the shell.

- [ ] **I2. Quoting.** `quote()` in `src\ear.cpp` wraps double quotes and backslash-escapes inner quotes. Windows command-line parsing also treats `\"` specially. **Pass:** a path or language containing a quote cannot break out of the argument, or the limitation is a recorded Fail with an example. **Fail:** the review calls the quoting safe without reading it.

- [ ] **I3. The text file is operator input.** It is trusted as the user's own parameters. The program must still not search the disk for a picture, a second settings file, or a tool definition the user did not write. **Pass:** A8 and D6 hold. **Fail:** a path walk "helps" find `gemma.image` or a missing model.

- [ ] **I4. Path join.** `cfg_path` joins the settings directory with the value. A value the user wrote with `..` is still the user's value. **Pass:** the program does not invent `..` or an absolute path of its own. **Fail:** code searches parent directories for config.

- [ ] **I5. `exe_dir` buffer.** `GetModuleFileNameW` is called with `MAX_PATH` and the length is not checked. **Pass:** ear still finds `nemo-speech.exe` beside itself for a normal install path, or a long-path failure is recorded. **Fail:** a truncated path launches the wrong exe and the review marks ear as Pass.

- [ ] **I6. Secrets and releases.** No tokens belong in the templates or the commit. Model URLs are public Hugging Face pins. **Pass:** `install.publish` is off and the diff has no credentials. **Fail:** a token, a local model, or a release upload is committed.

- [ ] **I7. Generated files stay out.** Do not commit wavs, `*_out_*.txt`, `mouth.txt`, `hear_*` leftovers, `.install`, `.venv`, `C:\tgemma`, `*.pid`, `*.stop`, or run logs. `reference.wav` is the tracked bake reference, not a run output. Section L is the disk check for leftovers the installer never removes. **Pass:** `git status` shows only the review's intended files. **Fail:** a proof artifact is staged.

- [ ] **I8. Process law.** No pull request. No amend, rebase, squash, reset, or force-push. Commit messages do not paste `GOAL.md`, this checklist, or another computer's endpoint list. **Pass:** the branch is `runner-h` and the new commit is a delta plus a pointer to the living docs, including `BOTS.md`. **Fail:** history is rewritten or a PR is opened.

## J. Tests and proofs

There is no unit-test suite. `install.llama_build_tests`, `install.ear_tests`, and `install.mouth_ggml_build_tests` are `off`. That is the contract. The proof is the executable and the file it writes, or `hear.py`'s stdout for the mic one-shot.

- [ ] **J1. No harness.** **Pass:** the review does not add a test runner, a mock exe, or a Python synthesizer. `mouth.py` still runs `chatterbox.exe`. `hear.py` still runs `nemo-speech.exe`. **Fail:** a mock exe or a Python stand-in speaks or transcribes without the real binary.

- [ ] **J2. One program, then the chain.** Order is G5, G6, G7, G8, then G9. G12 and G13 are separate one-shots. **Pass:** each step's file exists before the next step is judged. **Fail:** the chain is judged from memory or from another machine's log.

- [ ] **J3. Vision, if exercised, uses the file.** A picture turn puts raw base64 in `gemma.image` and already contains `<__media__>` in `gemma.text`. **Pass:** the output file is the generation, and the pixels are the base64 that was written. **Fail:** the test points `gemma.image` at a filename or relies on a history file.

- [ ] **J4. Blocked is allowed.** Missing cable, missing toolchain, or no build on this machine is Blocked, with the command output. **Pass:** Blocked items are not converted into Pass by skipping to the room mic for a cable item. **Fail:** a Blocked proof is marked Pass. G13 may use the room mic. G5–G9 may not.

## K. Open gaps

Confirm each one in the source. This list is not a patch set. A fix that adds an orchestrator fails B6 even if the symptom goes away. Re-measure. Do not treat an old listing as the result.

- [ ] **K1. One-shot exit.** B1. The finish-line gap stays open while each executable returns after one result.

- [ ] **K2. Mouth knob split.** A11. `end_trim` does not run on gpt2, so the default `turbo` mouth ignores `chatterbox.end-trim-samples`. `top_k` does not run on v3. `min_p`, `cfg_weight`, and `exaggeration` do not run on nano or turbo.

- [ ] **K3. Playback device is the Windows default.** `chatterbox.exe` with `chatterbox.play on` calls `PlaySoundW` with `SND_FILENAME` only: no endpoint id and no `SND_NODEFAULT`. `mouth.py` adds `SND_NODEFAULT` and still uses the default waveform device. The audio default is a separate fact from the GPU. G8, G12, and E8.

- [ ] **K4. Non-float, non-PCM16 capture becomes silence.** G3.

- [ ] **K5. `gemma.image` whitespace is treated as empty.** D6. `filled()` collapses a whitespace body. Confirm whether that collapse is acceptable against the template's empty value.

- [ ] **K6. Text-only Gemma still loads `gemma.mmproj`.** That is the named projector, not a picture search. **Pass:** a missing projector fails closed and no image file is opened. **Fail:** load failure causes a directory search.

- [ ] **K7. `temperature` of `0` is not applied.** Both mouth samplers scale logits only when temperature is `> 0` and not `1`. A written `0` behaves like no scaling. **Pass:** the template's non-zero temperatures are what the proof uses, and `0` is recorded. **Fail:** the review claims `0` means greedy.

- [ ] **K8. Cable evidence is local.** G11. Files from another machine are not a Pass on this one.

- [ ] **K9. Binaries and `C:\tgemma` may already sit in this checkout from another build.** H3. Judge a binary only after you know which machine and which backend linked it.

- [ ] **K10. The installer does not fully clean up after itself.** L3. A finished install can leave empty staging directories, the ONNX Runtime zip, and converter bytecode. The skip caches in L4 are intentional retention. Re-measure the paths. Do not copy byte sizes from another listing.

## L. Workspace and install cleanup

Re-measure on this machine. Another machine's sizes are not a Pass here. Do not delete the skip caches in L4 as if they were crashed downloads. Do not commit any of them.

`install.py` removes some temps only on the success path of that step. It has no final cleanup pass.

Removed when that step succeeds:

- `download` writes `dest` plus `.part`, then replaces the destination with the part file.
- GGUF conversion writes `*.gguf.converting`, then replaces the destination with that file.
- `bake_voice` deletes `.install\cache\baking\<variant>` after the baked GGUFs are copied out.
- `embed_utf8` deletes the temporary manifest it wrote beside an ear exe.
- `onnx_root` renames the inner `onnxruntime-win-x64-*` folder onto `.install\cache\onnxruntime`.

Not removed by any step: the parent `.install\cache\baking`, the empty `.install\cache\onnxruntime-src`, `.install\cache\onnxruntime.zip`, and `scripts\__pycache__`.

Kept so the next `python install.py install.txt` can skip work: `.venv`, `.install\src`, `.install\build`, `.install\cache\ckpt`, `.install\cache\ckpt-v3`, `.install\cache\gguf`, `.install\cache\onnxruntime`, `.install\cache\stamps`, and the directory in `install.build_gemma`.

- [ ] **L1. No proof debris in the workspace.** From the repo root, look for `*_out_*.txt`, `*.pid`, `*.stop`, `*.run.log`, `*.run.err`, `mouth.txt`, `hear_*`, and `*.wav` other than the tracked `reference.wav`. Also look for a root `build\`, `models\`, `wav\`, `gemma\build\`, `gemma\models\`, or `gemma\llama.cpp\`. **Pass:** none of those proof leftovers are present, and `git status` does not stage install trees or run outputs. **Fail:** any of them are present as if they were source, or a proof wav other than `reference.wav` is tracked. A local `mouth.txt` from a one-shot may exist and must stay untracked.

- [ ] **L2. No abandoned partials.** Search the repo and the `install.build_gemma` directory for files ending in `.part`, `.gguf.converting`, or `.tmp` (not the upstream `*.tmpl` shader templates inside the pinned ggml trees), and for a non-empty `.install\cache\baking\<variant>` that still holds `t3.gguf`, `s3.gguf`, or `bake.txt`. **Pass:** none of those files or variant directories exist. **Fail:** any exist. They are a crashed install, not a skip cache.

- [ ] **L3. Staging the success path leaves behind.** After a finished install, check these three paths. Record presence and size here. **Pass:** each one is absent, or it is recorded as a Fail of `install.py` with the path and size you just measured. **Fail:** the review calls the workspace clean while any of them remain, or it pastes another machine's sizes as this measurement.

  - `.install\cache\baking` — parent directory. `bake_voice` deletes only `baking\<variant>`.
  - `.install\cache\onnxruntime-src` — empty shell left after `onnx_root` renames the extracted folder away.
  - `.install\cache\onnxruntime.zip` — the download archive. `onnx_root` does not delete it once `.install\cache\onnxruntime` is complete.

- [ ] **L4. Skip caches stay local and are named.** List `.venv`, `.install\src`, `.install\build`, `.install\cache\ckpt`, `.install\cache\ckpt-v3`, `.install\cache\gguf`, `.install\cache\onnxruntime`, `.install\cache\stamps`, and the `install.build_gemma` directory. Record that they exist and their sizes on this machine. **Pass:** they are untracked, they match a finished install on this machine, and the gemma build directory is the backend from H2. **Fail:** they are committed, or they are deleted in order to mark L3 Pass.

- [ ] **L5. Converter bytecode.** `scripts\__pycache__` is created when the voice converters import `quant.py`. The installer never deletes it. **Pass:** the directory is absent, or it is recorded as an uncleaned side effect and it is not staged. **Fail:** it is committed, or a `.pyc` is cited as part of the running assistant.

- [ ] **L6. Root products versus junk.** A finished install copies executables, `onnxruntime.dll`, the NeMo DLLs, and the model files named by the templates into the repo root. Those are local products. **Pass:** the root contains those products and the tracked sources, and nothing else that L1–L3 would flag as debris. **Fail:** a second copy of a role, a log, or a partial download sits in the root and is ignored.

## Review closeout

Write the result as a list of ids with Pass, Fail, or Blocked and the evidence. Do not paste this checklist into a commit message.

A Fail is a defect report. Change code only when the review task also asks for a fix. Any fix stays inside the role's executable and that role's text file. The residency gap, if it is fixed later, is the same executable staying loaded. It is not a new process.

Leave generated audio, output text files, `mouth.txt`, models, `.install`, `.venv`, and `C:\tgemma` uncommitted.
