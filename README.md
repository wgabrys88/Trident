# Trident Runner X — Windows Bare-Metal Architecture and Session Handover

This file is the authoritative human-language handover for the current Runner X baseline. It is intended to be copied into a completely fresh AI coding session, even with a different agent or model, so that the new session can understand the project, the architecture, the experiments, the mistakes already found, the reasons behind current decisions, the expected user workflow, and the validation methodology without rediscovering the same facts.

The tracked source is always the final source of truth. This README explains why the source looks the way it does and preserves historical evidence that matters when changing it. Git history is evidence of intent and experiments, not an instruction to restore old designs.

## 1. Current status in one paragraph

Trident is a **Windows x64-only, local, bare-metal voice conversation application**. The only supported user command is `python main.py`. On first run that command creates `.venv`, installs the pinned Python runtime, re-executes inside `.venv`, installs or repairs all native runtimes/models/reference voices, builds the local Chatterbox TTS resident when necessary, and launches the Gradio application at `http://127.0.0.1:7860`. The live speech pipeline is browser microphone → canonical 16 kHz float PCM → Silero VAD → Smart Turn → Parakeet ASR → Gemma conversation stream → sentence-aware speech segmentation → Chatterbox TTS → streamed browser audio. Three model residents stay warm on fixed localhost ports. Nano is the fresh default TTS family. VB-CABLE is **not** a runtime dependency; it is useful only as an optional external black-box test harness for simulating microphone speech and barge-in through the actual browser capture boundary.

## 2. Non-negotiable engineering rules

These rules came from the user and apply to all future work.

### Diagnose the real failure first

Do not patch the visible symptom until the actual failing layer and causal chain have been proven. Trace failures through the exact owners involved: user/UI → audio ingress → Conversation → local protocol → resident ownership → native process → model/runtime → installer/build state. A fix is correct only if it repairs the owning layer.

### Current source is truth

Read the current tracked source before planning. Read relevant Git history to understand intent, previous experiments, and why something was changed or reverted. Do not restore an old design just because it once worked. If current source and old documentation disagree, current source wins unless the user explicitly chooses a new behavior.

### One source of truth per state

Configuration and live state must have one authoritative owner. Current examples:

- `data/live-settings.json` owns live application settings.
- `requirements.txt` owns live Python dependencies.
- `resident.py` owns resident process identity/lifecycle.
- `media.py` owns media and PCM normalization.
- `conversation.py` owns the conversational execution pipeline, queues, turn history, and TTS interruption.
- Installer revision markers own whether the deployed native TTS runtime is current.

Do not create a second settings table, second queue graph, second audio converter, second resident manager, or second logging sink.

### Efficiency over interface preservation

Do not preserve old interfaces, transports, worker boundaries, compatibility formats, or process ownership just because they existed. The old command-line surface and in-project VB-CABLE transport were intentionally deleted. If a simpler architecture can own the same behavior with less code and fewer failure modes, change the architecture and delete the obsolete path.

### No speculative defensive coding

Do not add broad catches, retries, fallback algorithms, silent recovery, compatibility wrappers, or alternate transports to make failures disappear. Fail at the real defect. Retain only cleanup boundaries and error-delivery boundaries that are required to release owned resources and surface the original error.

The two broad `Exception` boundaries currently in `conversation.py` are intentional: one annotates/logs a TTS request failure and re-raises it; the other is the worker-thread error-delivery boundary that records the worker failure and sends it to the UI. They are not fallback paths.

### Consolidate instead of abstracting duplicates

When two implementations perform the same job, reuse the existing execution path and remove the duplicate. Standalone ASR/TTS, UI conversation, file inputs, and self-test should converge on the same media/resident/Conversation functions instead of maintaining parallel implementations.

### Reduce code only by reducing ownership

The objective is a smaller and easier-to-maintain system without functionality regression. Do not minify readable code, remove useful telemetry, or delete required behavior to hit an arbitrary LOC number. The final Windows-only source is about 15% smaller than `runner-x/fb10adf` across Python + C++ + headers + CMake, while carrying later bootstrap, resident, logging, barge-in, and installer fixes.

### Keep source code self-explanatory

The source baseline intentionally avoids explanatory comments. Durable rationale belongs in this README and Git history. Clear module/function ownership should make the implementation understandable without comment-based parallel documentation.

## 3. What the user should do now

The delivery ZIP is designed to replace the working tree of a fresh GitHub clone while preserving that clone's `.git` directory.

### Fresh clone with GitHub Desktop

1. Open GitHub Desktop.
2. Clone the Trident repository into a **new folder**.
3. In GitHub Desktop, switch the checked-out branch to **`runner-x`**.
4. Close any Trident process using that folder.
5. Open the cloned folder in Windows Explorer.
6. Keep the folder named `.git` exactly as it is.
7. Delete every other file and folder from the cloned working tree.
8. Extract the final delivery ZIP into a separate temporary directory.
9. Copy **all** files and folders from the extracted ZIP into the fresh clone directory.
10. Return to GitHub Desktop and confirm the current branch still says `runner-x`.
11. Review the Changes list before committing. The README itself should appear as a new tracked file.
12. Run the bare-metal validation below **before** committing.
13. If validation is satisfactory, commit through GitHub Desktop and push `runner-x`.

The final ZIP intentionally contains no `.git` directory, no `.venv`, no models, no runtime binaries, no generated data, and no build cache.

### Python and `.venv`, line by line

Open PowerShell in the fresh clone directory.

1. Verify the launcher Python:

   `python --version`

   It must be Python 3.11 or newer. Trident is Windows x64 only.

2. Start Trident:

   `python main.py`

3. On the first run, `main.py` creates `.venv` automatically with `venv.EnvBuilder(with_pip=True)`.

4. It hashes `requirements.txt`. If `.venv/.trident-runtime` is absent or its hash does not match, it runs `.venv\Scripts\python.exe -m pip install -r requirements.txt`.

5. It re-executes itself under `.venv\Scripts\python.exe`. Manual activation is not required.

6. The same process enters the installer. Missing native/runtime/model assets are installed or repaired, then Gradio launches.

7. After `.venv` exists, this explicit command is also valid for diagnosis:

   `.venv\Scripts\python.exe main.py`

   The normal user command remains `python main.py`.

8. To completely rebuild only the Python environment, close Trident, delete `.venv`, then run:

   `python main.py`

9. Do not manually create a second environment and do not install a second requirements file. `requirements.txt` is the sole runtime dependency definition.

10. Do not pass application arguments. `main.py` intentionally rejects them. The old `install`, `agent`, `resident`, `tts`, `asr`, `run`, `--ui`, `--family`, and tuning CLI interfaces were removed rather than preserved as aliases.

### First bare-metal run expectations

The first run may download several gigabytes and may take substantial time because it can install compiler/Vulkan prerequisites, fetch release runtimes, fetch/convert models, build native Chatterbox, and download reference voices.

The application owns these install-time components:

- Python `.venv` and pinned Python packages.
- CMake 4.4.2 from the Python package.
- Visual Studio Build Tools C++ workload if `cl.exe` is absent.
- LunarG Vulkan SDK 1.4.357.0 in a project-local directory.
- Parakeet Vulkan release runtime.
- llama.cpp Vulkan release runtime for Gemma.
- Pinned Chatterbox and ggml source archives used to build `trident-tts-server.exe`.
- Pinned models and reference voices.
- A temporary conversion environment for Chatterbox model conversion.

The Windows GPU/display driver and Vulkan ICD are host/hardware responsibilities; Trident cannot safely replace the machine's graphics driver.

If the Visual Studio installer asks Windows for elevation, approve it. The Vulkan SDK path uses LunarG's `copy_only=1` mode and does not require normal system SDK registration.

## 4. The only supported application command

`python main.py`

That command means **bootstrap + install/repair + launch**.

This one-command contract is deliberate. A separate installer CLI, UI installer button, and family install flags were duplicate state/ownership. All TTS families are installed so the Gradio UI can switch families without a later manual setup command.

## 5. Application topology

Trident uses one Python orchestration process and three persistent native model residents.

| Component | Implementation | Backend | Address |
| --- | --- | --- | --- |
| Parakeet ASR | parakeet.cpp | Vulkan | `127.0.0.1:17931` |
| Gemma LLM | llama.cpp | Vulkan | `127.0.0.1:17932` |
| Chatterbox TTS | Trident C++ server over chatterbox.cpp/ggml | Vulkan | `127.0.0.1:17933` |
| Silero VAD | `silero-vad-notorch` | ONNX Runtime CPU | in Python process |
| Smart Turn v3.2 | ONNX | ONNX Runtime CPU | in Python process |
| UI | Gradio | browser + Python | `127.0.0.1:7860` |

The fixed resident ports are part of the architecture. Do not start duplicate copies on random ports to hide identity/lifecycle defects.

## 6. End-to-end speech pipeline

### Human hands-free path

1. The browser captures microphone audio through native Gradio microphone support.
2. Gradio supplies NumPy audio chunks to `ui.py`.
3. `media.audio_pcm()` converts channel layout/sample type/sample rate into canonical **16 kHz mono float32 little-endian PCM bytes**.
4. `Conversation.feed_audio()` enqueues those bytes to the ASR worker.
5. The ASR worker appends the audio to the current turn WAV and retains the last eight seconds in memory for Smart Turn.
6. Silero VAD receives 512-sample frames using the current threshold/silence settings.
7. A Silero speech-start event calls `_interrupt_tts()` so new human speech can barge into an assistant response.
8. A Silero speech-end event triggers Smart Turn v3.2 over the retained eight-second tail.
9. If Smart Turn says the utterance is complete, the turn is finalized and transcribed with Parakeet.
10. The transcript is appended to the conversation transcript and dispatched to the LLM queue.
11. Gemma streams the assistant response.
12. `_SpeechSegmenter` emits speakable text units according to the selected family `first_chars` and hard `chunk_chars` boundaries, preferring sentence punctuation.
13. The first speech unit starts a TTS turn. Later units enter the same TTS queue.
14. Chatterbox streams PCM16 frames through the local resident socket.
15. Python emits those frames to Gradio as WAV stream chunks for browser playback.
16. The completed user and assistant messages are retained in Gemma history for future turns.

### Push-to-talk path

The same microphone component is used. When recording stops, the completed audio is converted by the same `media.audio_pcm()` function and passed to `Conversation.submit_audio()`. That method interrupts current TTS, enqueues the audio, and immediately queues a `PTT` cut. PTT intentionally does not use Silero/Smart Turn endpointing because the user's stop-recording action is the endpoint.

### Text turn path

A manual text turn enters `Conversation.submit()`. It interrupts active TTS and dispatches the supplied text directly through the same Gemma → segmentation → TTS pipeline. It does not invent a second LLM/TTS implementation.

### Uploaded audio path

The Conversation tab can submit an uploaded audio turn. It is normalized by the same `media.audio_pcm()` function and uses the same PTT-style completed-audio path.

### Standalone ASR path

The Tools tab calls `transcribe_pcm()`, which writes canonical WAV, resolves/reuses the same Parakeet resident, runs the same long-file chunking/transcription function, and writes the same style of run artifacts.

### Standalone TTS path

The TTS tab resolves the same effective family settings, voice preparation, resident identity, and synthesis functions used by Conversation. It is not a separate native host.

### Internal self-test path

The Tools tab self-test calls `agent.run()`.

The self-test deliberately does **not** use VB-CABLE or any OS virtual microphone. It already owns the synthesized audio bytes, so routing them through an operating-system playback/capture loop would be redundant.

For each test prompt:

1. Chatterbox synthesizes the prompt as a WAV through the normal resident path.
2. `media.wav_pcm()` converts it to 16 kHz float PCM.
3. In continuous mode it is sent in microphone-sized chunks through `Conversation.feed_audio()` plus enough trailing silence to trigger the configured endpointing path. This means Silero and Smart Turn are genuinely exercised.
4. In PTT mode it uses `Conversation.submit_audio()`.
5. The self-test waits with an idle-based 120-second progress budget and 1.5-second completion settle period.
6. Expected regexes are checked against the **LLM answer**, not the transcript.

This internal test proves the model pipeline without introducing a second audio transport. External VB-CABLE testing, described later, is still valuable because it tests the browser/Windows microphone boundary itself.

## 7. Conversation concurrency and ownership

There are **three worker threads plus the caller/UI thread**, not four worker threads.

- ASR worker: audio accumulation, Silero, Smart Turn, Parakeet, dispatch.
- LLM worker: serialized Gemma turns, streaming text, history, speech segmentation.
- TTS worker: serialized speech-unit synthesis and audio output.
- UI/caller thread: control, configuration, Gradio event handling, cleanup.

Communication uses `queue.SimpleQueue`.

The queues are deliberately separate because they enable overlap: Gemma can continue generating while TTS speaks completed units. Do not merge worker ownership merely to reduce LOC unless measurements prove no loss of useful overlap/latency.

## 8. Barge-in behavior

Barge-in means **interrupt TTS only**, not discard conversation context and not start a second LLM pipeline.

When new speech starts in continuous mode, or when a manual/PTT submission starts:

1. `_interrupt_tts()` marks all TTS through the current conversation turn as cancelled.
2. It emits `audio-reset`; the Gradio output component is cleared so current browser playback stops.
3. The active `local_api.chatterbox_stream()` request observes its cancellation callback.
4. The client closes the current resident socket request instead of terminating the resident process.
5. The TTS worker drains/skips remaining queued units for that cancelled turn.
6. The LLM worker is **not** force-killed. It remains serialized and finishes its current answer/history before processing the next LLM queue item.
7. The newly captured human turn is transcribed and queued normally.
8. Gemma history therefore remains coherent. Only speech synthesis/playback is interrupted.

This distinction is important. The project requirement is conversational interruption, not arbitrary model cancellation.

## 9. Long speech and context

Parakeet long-file transcription uses sequential 30-second windows with four-second overlap. Word timestamps are filtered at overlap midpoints so the transcript does not duplicate overlap words. The design intentionally bounds inference memory instead of allocating one huge ASR input.

Continuous natural speech may produce more than one conversational turn when a natural pause is recognized by Silero and Smart Turn regards the preceding sentence as semantically complete. This is normal. Multi-sentence input should be tested for both semantic continuity and context preservation, not for an artificial requirement that one spoken paragraph always equals one engine turn.

Gemma retains the last six conversation turns as up to twelve role messages (`LIVE_AUDIO["llm_history_turns"] = 6`, multiplied by two for user/assistant messages). A long black-box test must verify that earlier facts remain available inside that window.

## 10. VAD and Smart Turn

Silero runs in the Python process through ONNX Runtime. The current defaults are:

- ASR/VAD rate: 16 kHz.
- VAD frame: 512 samples.
- VAD threshold: 0.5.
- Candidate end silence: 200 ms.
- Smart Turn history window: eight seconds.

Smart Turn runs CPU-only with ONNX Runtime sequential execution, one inter-op thread, and one intra-op thread. Historical bare-metal measurement was roughly 61–72 ms per decision, so increasing Smart Turn threading was not justified.

At engine shutdown, a turn is transcribed only if Silero has actually observed speech. Silence-only tails are discarded. This removed the historical phantom final ASR pass.

## 11. Gemma conversation brain

The brain is Gemma 4 E2B QAT q4_0 served by pinned llama.cpp `b10453` on Vulkan.

Current runtime intent includes:

- all GPU layers;
- 4096 context;
- one parallel slot;
- f16 KV cache;
- mmap load mode;
- two generation/batch CPU threads;
- one HTTP thread;
- prompt caching;
- reasoning/thinking disabled;
- flash attention disabled on generic/Iris Xe and enabled on the Pascal profile.

Generation defaults are temperature 1.0, top-p 0.95, top-k 64, min-p 0, repeat penalty 1, seed 42, max tokens 1024.

The system prompt is live-configurable and instructs Gemma to produce spoken prose only in the selected TTS language, without Markdown/code/URLs/emoji/tags.

## 12. TTS families

Three Chatterbox families are supported through the same resident server.

| Family | Languages | T3 max tokens | CFM steps | First speech-unit target | Hard speech-unit limit | Voice CFG |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Nano | English | 768 | 2 | **180** | 280 | none |
| Turbo | English | 768 | 2 | 120 | 280 | none |
| V3 | multilingual | 768 | 5 | 180 | 300 | CFG 0.5 / exaggeration 0.5 |

Nano is the fresh default family.

Turbo/Nano reject nonzero `min_p`, CFG weight, or exaggeration because those controls are not wired by their model path. V3 supports multilingual language selection and its additional sampling/voice controls.

### Critical Nano 180 versus 80 history

The current final baseline deliberately ships **Nano `first_chars = 180` because the user explicitly requested it**.

Do not misrepresent the historical evidence:

- Older Runner X work used 180-character Nano streaming packs. Commit `9783207` was titled “Stream Nano packs at 180 chars and flush NDJSON per pack”. Commit `6f8a354` also recorded Nano at 180/2048/cfm=2.
- Commit `fb10adf` later changed Nano from **180 to 80**. Its commit message records a bare-metal A/B on identical prompts where three-sentence answers completed in **28.5 s at 80 versus 34.2 s at 180**, first audio began about one second earlier, and later units synthesized at roughly 1.3–1.5× realtime while overlapping playback. Single-sentence replies were unaffected.
- Therefore the Git evidence available in this repository says **80 was faster in that A/B**.
- The final user nevertheless believes the important faster-than-realtime period may have corresponded to 180 and explicitly wants 180 restored in this final baseline.

The next agent must treat 180 as current source truth but **must rebenchmark 180 vs 80 on the same warmed Iris Xe machine before making a performance claim**. Compare identical text, voice, family, model files, power state, and contention. Record first-audio/TTFA, per-unit T3 and S3Gen timings, total wall time, audio duration, RTF, and x-realtime.

### Critical CFM-step history

Commit `2ed25ea` experimented with one CFM step for Nano on Iris Xe because S3Gen dominated latency. Commit `4ca69b3` deliberately reverted that experiment (“Back to MILESTONE-17”). Current source uses **two CFM steps**. Do not casually restore the one-step experiment.

### CPU versus Vulkan history

Nano was tested on CPU with `gpu_layers=0`, eight logical threads on the i5-1145G7 (four physical cores/eight HT threads). Historical results were roughly RTF 1.6–3.3, only 0.3–0.63× realtime. Vulkan won decisively. Do not move Nano back to CPU based on generic vendor claims measured on different CPUs.

### Iris Xe contention history

Historical Nano idle performance on the target machine was approximately RTF 0.6–0.8 (1.3–1.6× realtime) with roughly 1.6–2.0 s first audio. When Gemma and TTS contend simultaneously for the same Iris Xe Vulkan/UMA resources, TTS can fall to roughly RTF 0.9–2.2 and TTFA can degrade substantially. Long-answer performance must distinguish idle TTS from concurrent LLM+TTS.

Turbo historically ran around realtime/slightly above realtime when idle. V3 was materially slower than realtime on Iris Xe and is not the recommended real-time family for this hardware.

## 13. Iris Xe and Pascal hardware policy

Windows hardware detection uses PowerShell `Win32_VideoController` names.

Profiles:

- `irisxe`: positive “Iris” + “Xe” identification.
- `pascal`: GTX 1050/1060/1070/1080, Titan Pascal family, Quadro P family.
- `generic`: everything else.

On Iris Xe, each Chatterbox codec conversion is changed to q4_0 and uses the `-irisxe-q4_0-rawf32-v1.gguf` filename policy. This was introduced for the real target hardware and must not be removed as generic cleanup without a measured replacement.

Pascal retains the Vulkan FP16-disable policy. The environment flag is used for release residents, while the pinned ggml source is also adjusted during local TTS building so pre-Turing NVIDIA behavior is deterministic.

## 14. Native Chatterbox server

`tts/` is a small Windows C++ server around pinned chatterbox.cpp/ggml.

It is built as C++17 with Vulkan enabled, CUDA disabled, static libraries, no ccache, no upstream tests/executables, and MSVC `/MP /bigobj`. It links the existing `tts-cpp` target plus `ws2_32`.

It listens on localhost TCP port 17933. The request contains text length, output-path length, streaming flag, join mode, then text/path bytes. Responses are framed as:

- kind `2`: streamed PCM16 bytes;
- kind `0`: successful final result/metrics string;
- kind `1`: error string.

This protocol is internal and intentionally small. Do not add HTTP/WebSocket/WebRTC merely because they are fashionable unless they remove more code/ownership than they add.

The resident keeps the model/reference conditioning warm. A changed family/language/reference/model/argv identity causes a resident restart through `resident.py`.

## 15. Resident lifecycle and single-instance safety

Resident state is under `tools/runtime/.resident/`.

`resident.py` is Windows-only now and uses `msvcrt.locking` to serialize ownership decisions per resident. This is not optional bookkeeping: without a lock, two callers can observe a closed port and both attempt to spawn the same resident.

Identity behavior:

1. Build a manifest of the resident server/model/reference/codec file paths, sizes, and modification timestamps plus effective arguments.
2. If the manifest matches the cached state, reuse the cached content hash without rehashing multi-gigabyte files.
3. If the manifest changed, hash the actual file contents plus effective arguments.
4. If the readiness probe is already healthy and identity matches, reuse the process.
5. If the owned resident is healthy but identity differs, kill it, wait for the fixed port to close, then start the new identity.
6. If the port is occupied but no Trident-owned PID is recorded, fail instead of killing a foreign process.
7. Spawn with Windows detached/new-process-group flags.
8. Write state, wait for readiness.
9. If readiness fails, kill the exact spawned process, remove ownership state, wait for port close, then propagate the real failure.

Stopping uses `taskkill /PID <pid> /T /F` because the resident may own child processes.

## 16. Installer architecture

The installer is run automatically by `main.py` every start. It is intended to be idempotent by deployed runtime/model identity, not by preserving build trees.

### Python dependencies

`requirements.txt` currently contains:

- `cmake==4.4.2`
- `gradio==6.25.0`
- `imageio-ffmpeg==0.6.0`
- `numpy>=2,<3`
- `onnxruntime==1.29.0`
- `silero-vad-notorch==6.2.1.1`

There is no SoundDevice, comtypes, PipeWire, PulseAudio, Zig, Ninja, or Linux-specific dependency.

### Native prerequisites

If TTS needs a rebuild, the installer checks for:

- MSVC `cl.exe` under Visual Studio Build Tools.
- project-local LunarG SDK files: `Include/vulkan/vulkan.h`, `Lib/vulkan-1.lib`, `Bin/glslc.exe`.

If MSVC is missing, the pinned Visual Studio Build Tools bootstrapper is downloaded and run quietly with the C++ workload.

If Vulkan SDK is missing, the pinned LunarG 1.4.357.0 Windows installer is downloaded and run with:

`--root <project tools/VulkanSDK/1.4.357.0> --accept-licenses --default-answer --confirm-command install copy_only=1`

The `copy_only=1` argument is critical.

### The Vulkan installer failure that was already diagnosed

A real bare-metal run failed with exit status 1 after LunarG extracted its payload, then rolled everything back. The failing command omitted `copy_only=1`. LunarG normal unattended installation performs system-level install operations and can require elevation; Trident only needs a private SDK directory because it explicitly supplies its build environment. Adding `copy_only=1` repaired the actual prerequisite-owner defect. Do not “fix” this by swallowing the installer exit code or adding a system-SDK fallback.

### Source acquisition

Pinned Chatterbox and ggml source are fetched as immutable GitHub commit tarballs, not Git clones. Git is not a Trident runtime prerequisite.

Current revisions:

- Chatterbox source: `77e9b0501aa76a46845d8b13cf956c21d060b593`
- ggml source: `58c3805840b516b2a88ff867ccf7bb41dba79951`

`installer.py` validates archive paths before extraction.

### ggml Vulkan tuning

The installer edits the exact pinned ggml Vulkan FP16 policy only when the expected source pattern matches exactly. If the pinned source shape changes, installation fails. This is deliberate: silently applying a guessed patch to changed upstream source would be defensive/speculative behavior.

### TTS runtime identity

The native TTS deployed-runtime revision includes pinned source revisions, hardware profile, the known Vulkan tuning policy, CMake integration policy, Vulkan SDK version, MSVC toolchain identity, and the contents of Trident's own `tts/` source tree.

The deployed marker lives beside the deployed runtime. Build/source trees can therefore be deleted after successful deployment without making the next install think the runtime is stale merely because caches were pruned.

### Model conversion

Converted Chatterbox models use a temporary isolated Python environment under `tools/convert`. It pins CPU PyTorch 2.6.0 plus conversion dependencies. The converter environment is installation-only; it is deleted after a successful install.

### Cleanup

After successful installation, source/build/download/Vulkan/conversion caches are pruned. The deployed runtime and models remain. A future source/revision change can redownload/rebuild from the pinned definitions.

## 17. Models and assets

Shared models:

- Parakeet TDT 0.6B v3 Q4_K GGUF, pinned to `mudler/parakeet-cpp-gguf` revision `bf0af9f425fa01809cadec671b3cb672709d13e9`.
- Smart Turn v3.2 multilingual CPU INT8 ONNX, pinned to `pipecat-ai/smart-turn-v3` revision `f766f81d3cfdf7737ac64aad813d91bbfd56bf93`, SHA-256 tracked.
- Gemma 4 E2B q4_0, pinned to `google/gemma-4-E2B-it-qat-q4_0-gguf` revision `675cff42a74c774d6cb76f76d8eacb49b48c9b93`.

Release runtimes:

- Parakeet `v0.5.0` Windows Vulkan x64 ZIP.
- llama.cpp `b10453` Windows Vulkan x64 ZIP.

TTS family model revisions are pinned in `config.py`. Do not update model/source revisions independently unless the conversion/runtime compatibility chain is traced end to end.

Reference voices are pinned downloads for Trump, Obama, and Kamala. A custom uploaded/recorded reference is normalized to 24 kHz mono PCM16 and stored as the live voice path.

## 18. Media ownership

`media.py` is the single media conversion owner.

It uses the FFmpeg executable supplied by `imageio-ffmpeg`; no system FFmpeg installation is required.

Responsibilities:

- normalize arbitrary media to mono PCM16 WAV at the requested model rate;
- cache prepared Chatterbox reference WAVs;
- convert Gradio NumPy audio to canonical 16 kHz float PCM;
- convert 16 kHz float PCM to PCM16/WAV;
- convert generated WAV back to canonical PCM for internal self-test;
- split long Parakeet input into sequential overlap windows.

Do not add another resampler in UI, agent, or resident code.

## 19. Gradio UI

Gradio is the only application control surface. No custom microphone component and no FastRTC layer is used because native Gradio already owns the needed browser audio lifecycle and a custom frontend would add Node/npm/WebRTC ownership without removing a missing capability.

The UI has one live-settings section and five tabs.

### Live settings

Controls:

- hands-free vs push-to-talk;
- TTS family;
- language;
- preset/custom voice;
- chunks vs crossfade join;
- streaming vs buffered TTS;
- VAD threshold;
- VAD silence;
- system prompt.

Saving writes `data/live-settings.json`. An active Conversation receives the same new settings. VAD configuration is sent to the ASR worker. Family/voice/language changes invalidate the cached Chatterbox URL so the next TTS turn resolves the new resident identity.

### Conversation tab

One native Gradio microphone component serves both hands-free and PTT. The code must not regress to separate microphone implementations.

Also supports manual text, uploaded audio turn, transcript, answer, streamed response audio, and real backend status.

### TTS tab

Standalone text synthesis, streaming/buffered output, same family/language/voice settings, advanced model overrides. Overrides are typed through the single `TTS_FIELDS` mapping that also drives resident arguments.

### Tools tab

Standalone ASR and internal end-to-end self-test.

### Runtime tab

Resident status and stop operations.

### Logs tab

Browses the existing `data/runs/*/trident.log` files directly. Do not build a second logging database or UI telemetry service.

## 20. Live settings single source of truth

`data/live-settings.json` must contain exactly the schema in `LIVE_SETTINGS_DEFAULT`. Unknown/missing keys cause schema mismatch rather than being silently defaulted.

Fresh defaults:

- ingestion: `continuous`;
- family: `nano`;
- language: `en`;
- voice: `trump`;
- join: `crossfade`;
- TTS delivery: streaming (`real`);
- VAD threshold: 0.5;
- VAD silence: 200 ms;
- spoken-response system prompt from `config.py`.

Existing valid live settings remain authoritative. If a test expects fresh defaults, delete `data/live-settings.json` deliberately rather than changing tracked defaults around stale local state.

## 21. Logging and run artifacts

Each operation creates a run directory under:

`data/runs/<timestamp>-<command>/`

Stable artifact names inside a run include:

- `trident.log`
- `transcript.txt`
- `answer.txt`
- `input.wav`
- `output.wav`
- `meta.txt`
- prompt WAVs for self-test;
- per-turn/per-unit TTS WAVs where applicable.

The timestamp belongs to the run directory, not each artifact filename.

Important log events include:

- pipeline start/finish;
- resident start/ready/restart/stop;
- Parakeet duration/request/RTF;
- Smart Turn complete/probability/latency;
- Conversation dispatch turn/reason/text;
- Gemma TTFA/total/chars/answer;
- TTS per-unit audio duration, total/T3/S3Gen/TTFA metrics, RTF, x-realtime;
- TTS interruption request/interrupted completion;
- configuration changes.

When diagnosing performance, reconstruct one timeline from `trident.log`. Do not use UI impressions alone.

## 22. External VB-CABLE black-box QA — not a product dependency

VB-CABLE was once integrated into Trident because the automated coding agent needed to make TTS output appear as microphone input. That implementation was deleted because it duplicated an audio transport that the internal self-test does not need.

However, VB-CABLE remains useful as an **external Windows QA tool** because it can test the browser's real microphone boundary, including natural multi-sentence timing and barge-in while assistant audio is actually playing.

Do not add VB-CABLE, SoundDevice, PowerShell COM routing, or cable state back into the repository to perform this test. Keep the test harness external/disposable.

### Historical VB-CABLE facts from the target machine

The old implementation established these machine-specific facts:

- browser capture was bound to the selected/default recording device when capture started;
- the old CABLE Output endpoint exposed roughly 44.1 kHz and 16 channels on that machine;
- CABLE Input rejected direct 24 kHz playback in the old PortAudio path and required playback at the device's native rate;
- the old bridge mean-mixed channels and interpolated to 16 kHz before `Conversation.feed_audio()`;
- exact endpoint switching through comtypes/raw ctypes was unreliable on Python 3.11/comtypes 1.4.16; PowerShell + C# PolicyConfig was the route that worked at the time.

These are historical QA facts, not current runtime requirements.

### Manual black-box setup

1. Install the official VB-Audio Virtual Cable driver outside the repository.
2. Confirm Windows shows `CABLE Input` as a playback device and `CABLE Output` as a recording device.
3. Start Trident with `python main.py`.
4. Before pressing Conversation **Start**, configure the browser/site microphone to `CABLE Output`. If the browser is using the Windows default input, set `CABLE Output` as the default recording device before capture begins.
5. Keep assistant playback routed to physical speakers/headphones. Do **not** route the browser's assistant output back into CABLE Input or the assistant will hear itself.
6. Route only the external prompt-playback application to `CABLE Input` using Windows per-app output routing or an external test utility.
7. Prepare human-prompt WAV files. They can be produced by Trident's standalone TTS tab and downloaded, by another TTS tool, or by prerecorded human speech.
8. Play those prompts into CABLE Input while the Conversation tab is running in hands-free mode.
9. Inspect both visible transcript/answer behavior and `data/runs/.../trident.log`.

If an automated external helper is needed, it may use a disposable environment with SoundDevice or another audio library, but that dependency must remain outside Trident. The helper must query the actual CABLE Input sample rate instead of assuming 24 kHz. Do not restore the old in-repo cable transport just for test automation.

### Long-speech Scenario A — multi-sentence natural turn

Inject a prompt with several complete sentences and natural pauses, for example:

“My name is John. I live in New York. I work as a software engineer. What is the capital of France?”

Success criteria:

- audio is captured through the browser microphone boundary;
- Silero/Smart Turn may dispatch one or multiple turns depending on pauses/semantic completeness;
- every dispatched transcript is sensible and not duplicated;
- Gemma answers the questions coherently;
- later turns can recall “John”, “New York”, and “software engineer” while they remain inside history;
- no silent engine stall occurs.

Do not define success as “the whole paragraph must be exactly one engine turn”. Natural endpointing is allowed to split it.

### Long-speech Scenario B — true barge-in during assistant TTS

1. Inject a question designed to produce a long answer, e.g. “Explain the history of the European Union in detail and compare the major institutions.”
2. Watch status/logs until TTS for that turn is actively streaming.
3. While the assistant is audibly speaking, inject a second microphone prompt through CABLE Input, e.g. “Stop. Instead, tell me only what the European Commission does.”
4. The new speech-start event must cause `audio-reset` and TTS cancellation.
5. The current Chatterbox request should end without killing the resident.
6. The new prompt must still be captured/transcribed.
7. Gemma should answer the interrupt using the existing conversation context.
8. The prior answer may finish generating internally because the design interrupts TTS, not the LLM. This is acceptable and helps preserve serialized history.
9. No previous TTS units should resume after the new turn starts.

Log evidence should include interruption request/interrupted TTS and the new Conversation dispatch/LLM answer.

### Long-speech Scenario C — context memory

Use several turns:

1. “My name is Alice.”
2. “I am testing a voice pipeline on an Iris Xe laptop.”
3. “What name did I give you?”
4. “What hardware did I mention?”
5. “Repeat both facts in one sentence.”

Success means the answers preserve context within the configured six-turn history window.

### Long-speech Scenario D — rapid follow-up while prior TTS is still draining

Ask several short follow-ups with little pause. Verify:

- no double dispatch of the same audio;
- no queue interleaving error between TTS turn numbers;
- no response to a later question includes text from a future turn;
- cancelled TTS units are drained, not replayed;
- new human speech remains responsive.

### Failure modes worth treating as real defects

- TTS continues audibly after barge-in.
- A new turn waits behind old TTS instead of cancelling it.
- Gemma history is lost after interruption.
- Same audio dispatches twice.
- Smart Turn produces a permanent “continue” state with no later recovery from real speech.
- A worker thread fails but UI keeps pretending the session is healthy.
- Resident identity causes duplicate Vulkan model loads.
- First-audio or RTF regresses materially on warmed Nano without a measured explanation.

## 23. Performance measurement methodology

Never compare TTS performance using different text, different voice, different model state, or different system power state and then attribute the difference to one knob.

For Nano/Iris Xe experiments:

1. Warm all residents.
2. Use the same reference voice.
3. Use the same prompt text.
4. Record whether Gemma is concurrently generating or TTS is isolated.
5. Record first audio / `ttfa_ms`.
6. Record `t3_ms` and `s3gen_ms` separately.
7. Record total synthesis wall time.
8. Record audio duration.
9. Compute/record RTF and x-realtime.
10. Repeat enough times to distinguish a warm stable result from first-run/power-state noise.
11. Judge quality as well as latency.

Historical lesson: total RTF alone hid which stage moved. Nano latency work showed S3Gen could dominate while T3 stayed comparatively stable.

Do not tune chunk sizes from output-file size or one isolated RTF measurement. Sequence length and concurrent Vulkan contention matter.

## 24. Installer validation methodology

For every installer change, test these states separately:

1. Clean `.venv` / no runtime.
2. Existing `.venv` whose requirements hash matches.
3. Missing native TTS runtime.
4. Existing TTS runtime with matching revision marker.
5. TTS source/revision changed so rebuild is required.
6. Missing converted model with TTS runtime already current.
7. Existing correctly sized models.
8. Failed prerequisite install.
9. Failed native build.
10. Successful cleanup followed by another application launch.

Do not call something idempotent merely because rerunning it “usually works”. The deployed-state decision must remain valid after build caches are deleted.

## 25. Resident validation methodology

For resident changes prove:

- same manifest + same identity reuses the resident;
- content change forces rehash and restart;
- argument/voice/language/family change changes Chatterbox identity;
- two concurrent callers serialize at the lock;
- failed startup terminates the spawned process and removes ownership state;
- a foreign process on the fixed port is not killed;
- stop closes the port before a replacement spawn;
- no duplicate model load occurs.

## 26. Release-delivery methodology

Before giving the user a new replacement ZIP:

1. Run Python compilation over all tracked Python files.
2. Run `git diff --check`.
3. Run an unused-import/static reference scan.
4. Verify no prohibited/dead platform or virtual-audio dependencies remain.
5. Assert critical source invariants such as Nano defaults, CFM steps, first-chunk setting, and Vulkan `copy_only=1` command.
6. Build or at least configure native code against the real pinned dependency tree on the target platform when available.
7. Freeze the delivery without `.git`, `.venv`, models, generated data, runtimes, SDKs, or caches.
8. Re-extract the ZIP into a clean directory and rerun source/static tests there.
9. Simulate the exact user workflow: clean clone → `runner-x` → keep only `.git` → copy ZIP → inspect Git delta.
10. Generate a patch against the actual base commit if practical and independently apply it to another clean clone.
11. Compare source manifests byte-for-byte.
12. Calculate and report final SHA-256 values.

Do not call a ZIP final before the ZIP itself—not only the working directory—has been validated.

## 27. Known history of important defects and why they happened

### Fresh-clone NumPy failure

A recent cable integration imported `cable.py` from `main.py` at module startup. `cable.py` imported NumPy/SoundDevice before the installer/environment bootstrap could run. A clean clone therefore failed with `ModuleNotFoundError: numpy` before even reaching `--help` or install logic. Existing developer machines masked the defect because their Python environment already contained the packages.

The architectural fix was not a lazy NumPy catch. `main.py` became a stdlib-only bootstrap that establishes `.venv` before importing application modules.

### Fresh clone branch mismatch

The historical archive used `runner-x`, but its remote HEAD pointed at `main`. A plain clone could therefore select a different code line than the handover archive. The user's workflow now explicitly switches to `runner-x` before copying the delivery.

### Live-settings split

Historical UI/agent paths read `data/live-settings.json` while other CLI paths still had hard-coded defaults. This let an old local ignored settings file make a development machine run Nano even when a fresh tracked checkout defaulted differently. The duplicate CLI configuration system was removed; live settings are now the application authority.

### Installer readiness depended on deleted build cache

An older readiness test looked for build-tree evidence that cleanup later removed. A successful install could therefore make the next install think TTS needed rebuilding. The fix was a deployed runtime revision marker independent of temporary source/build state.

### Resident startup ownership leak

An earlier `_ensure` could spawn a process, fail readiness, remove state, and leave the process running. Current ownership cleanup kills the exact spawned PID before state removal.

### No serialization around resident creation

Two callers could both observe a closed port and race to spawn. Current Windows resident management serializes each component with `msvcrt.locking`.

### Logging had competing run owners

Agent and Conversation historically opened separate current run contexts. The self-test now supplies its run into Conversation so the complete causal trace belongs to one run tree.

### Documented barge-in existed before implementation

The old long-speech document required TTS interruption, but the tracked engine at that time had no cancellation path. Current Conversation implements explicit cancelled-turn state, local-socket cancellation, TTS queue draining, and Gradio audio reset.

### VB-CABLE became global dependency

VB-CABLE was originally added so a coding agent could inject synthesized speech as a fake microphone. That made NumPy/SoundDevice/Windows endpoint routing part of global startup and added a second audio transport. Once the agent could feed its owned PCM directly into Conversation, the virtual device was unnecessary and was removed from product code.

### Linux support added complexity and was later removed

A cross-platform experiment added Linux release assets, POSIX process/lock code, Zig/Ninja dependencies, Linux Vulkan SDK extraction, Linux GPU discovery, executable-bit handling, and POSIX socket branches. The user decided the actual deployment target is Windows bare metal and the maintenance cost was not valuable. This final baseline removes Linux support at the source level instead of leaving dormant branches.

### LunarG Windows rollback failure

After the cross-platform/one-command installer work, a bare-metal Windows run showed the Vulkan installer extract its payload then abort/rollback with exit code 1. Diagnosis showed Trident had used LunarG normal unattended installation even though it only needed a private SDK directory. `copy_only=1` was the exact fix and remains required.

## 28. Important Git experiments and milestones

The following history should be remembered when future changes touch the same area.

- `4fe1105` raised Nano context and aligned packing with sentence punctuation after 512 context starved T3 following conditioning.
- `9783207` streamed Nano packs at 180 characters.
- `6f8a354` fixed T3 EOS behavior, sentence-pause glue, contexts; recorded Nano 180/2048/cfm=2.
- `f832e56` fixed accidental speech of prompt/session labels and text-space packing errors.
- `e2e11ff` (MILESTONE-17) reworked speech UI/runtime feedback, kept overlapping native batch TTS speech units, preserved long-file ASR chunking, restored RTF visibility, rejected unsupported Turbo/Nano controls, and used native Gradio controls instead of custom components.
- `2ed25ea` tried one Nano CFM step on Iris Xe and added stage-specific timing.
- `4ca69b3` reverted that one-step experiment back to MILESTONE-17.
- `d8756d6` consolidated TTS field mapping and live settings, removed duplicate CLI/UI TTS execution.
- `127d682` added the old VB-CABLE injection transport after tracing Chromium/default-device behavior.
- `2c314ad` completed an automated VB-CABLE loop and recorded a roughly 6.384-second bare-metal speech cycle with detailed ASR/LLM/TTS timings.
- `fe07860` cached resident endpoints, fixed answer-regex semantics, and changed long-turn waits from absolute timeout to progress-idle timeout.
- `3bb3a85` removed the silence-only stop transcription.
- `55aca13` added Smart Turn latency logging.
- `fb10adf` changed Nano first speech-unit target from 180 to 80 and recorded the A/B described above.
- Later session work repaired bootstrap, settings ownership, resident content identity, installer deployed-runtime identity, logging ownership, and true TTS cancellation/barge-in.
- Cross-platform work temporarily added Linux support, then this final baseline removed it by explicit user decision.
- `d820b26` contains the LunarG `copy_only=1` repair baseline from which this final Windows-only source was derived.

## 29. Old documentation that is now superseded

The original project handover chunks are preserved later in this file for historical context, but several claims are no longer current:

- VB-CABLE is no longer a product dependency.
- `sounddevice` is no longer a runtime dependency.
- Linux support is no longer present.
- Gradio is pinned to 6.25.0, not the old 6.26.0 handover claim.
- `python main.py install --family all`, `agent`, `resident`, and `--ui` are not current interfaces.
- The product has three worker threads plus UI/caller, not four worker threads.
- Current Nano first_chars is 180 by final user override, not the old 80 documentation.
- Resident identity now hashes file contents with a cached manifest, not only path/size/mtime signatures.
- VB-CABLE long-speech testing should be done externally through the browser microphone boundary rather than by restoring cable code inside Trident.

## 30. What not to reintroduce

Unless a new measured requirement proves otherwise, do not reintroduce:

- Linux/WSL/POSIX support branches.
- VB-CABLE runtime integration.
- SoundDevice in `requirements.txt`.
- comtypes or raw PolicyConfig COM code.
- custom Gradio microphone components.
- FastRTC/WebRTC transport inside the local pipeline.
- separate CLI command hierarchy.
- separate installer UI path.
- separate requirements files.
- duplicate audio resampling helpers.
- duplicate TTS/ASR implementations for tools versus conversation.
- retries/fallback resident ports.
- environment-variable configuration as a second live settings system.
- build-cache-dependent deployed-runtime readiness.

## 31. Recommended first tasks for the next fresh session

A new agent should not begin by refactoring. It should first reproduce the user's bare-metal state.

1. Read this README completely.
2. Read current `git status`, current branch, HEAD, and the diff that created the current working tree/commit.
3. Read `main.py`, `config.py`, `installer.py`, `resident.py`, `conversation.py`, `agent.py`, `ui.py`, `media.py`, `local_api.py`, `vad.py`, and `tts/` before proposing changes.
4. Run `python main.py` on the real Windows target.
5. Capture the exact install or runtime failure if one occurs. Do not add fallback behavior.
6. Verify Nano is selected by fresh live settings and that the resident reports the expected effective family/arguments.
7. Measure Nano at first_chars=180 on the exact warmed Iris Xe setup.
8. Only if performance investigation is requested, run a controlled 180-vs-80 A/B and compare the historical `fb10adf` result.
9. Run internal self-test in both hands-free and PTT modes.
10. Run manual human microphone conversation.
11. Run the external VB-CABLE long-speech/barge-in protocol to validate the real browser microphone boundary and conversation context.
12. Use `trident.log` for causal diagnosis and quantitative latency attribution.

## 31A. Validation status of the final Windows-only delivery

The final source package was derived from the user-uploaded baseline repository on `runner-x` at `d820b26db481f05587de0f32c6a4be10997ee917` (`LunarG Fix`). The `.git` directory is deliberately excluded from the delivery ZIP so the user can apply the package onto a fresh GitHub Desktop clone while preserving the clone's own repository metadata.

The executable source count used for complexity comparison includes all tracked top-level Python files, `tts/CMakeLists.txt`, all `tts/src/*.cpp`, and all `tts/include/*.hpp`. The uploaded `d820b26` baseline contains 3,596 such lines. The final Windows-only tree contains 3,463, a reduction of 133 lines / 3.70% from that immediate baseline. Compared with `fb10adf`, which contains 4,083 lines under the same counting rule, the final tree is 620 lines / 15.18% smaller. README/history text is not counted as executable-source reduction.

The final package has no Linux/POSIX implementation in executable source: no Linux runtime assets, `fcntl`, POSIX signals/process groups, `MSG_NOSIGNAL`, Zig, Ninja, PipeWire, PulseAudio, SoundDevice, Linux GPU probing, or Linux native-build path remains. Windows-specific boundaries are explicit: AMD64 bootstrap, PowerShell GPU discovery, MSVC, LunarG Windows SDK, WinSock, `msvcrt` file locking, Windows process groups/taskkill, `.exe` runtime assets, and ZIP release bundles.

The following tests were run against a clean extraction of the final ZIP without using the developer working tree:

- Python compilation of every top-level module.
- Nano fresh default = `nano`.
- Nano CFM steps = 2.
- Nano first streaming unit = 180 characters by explicit final user choice.
- Iris Xe detection selects the Iris Xe codec conversion policy.
- Windows Parakeet and Gemma Vulkan release assets are selected.
- One-command bootstrap rejects legacy command-line arguments.
- Windows LunarG invocation ends in `--confirm-command install copy_only=1`.
- Windows release runtime extraction accepts ZIP and rejects non-ZIP bundles.
- No VB-CABLE, SoundDevice, PipeWire, PulseAudio, Zig, or Ninja runtime dependency remains.
- Resident content identity changes when file bytes change even when metadata is held constant.
- Windows resident lock ownership path is exercised with an `msvcrt` validation stub on the non-Windows test host.
- Failed resident startup kills the exact owned PID, removes state, waits for port closure, and re-raises the real readiness failure.
- TTS socket cancellation closes an active streaming request without producing a fallback result.
- Conversation barge-in advances the cancelled-through turn and emits `audio-reset`.
- The speech segmenter honors a 180-character first-unit floor.
- The native Gradio UI contains one microphone control and connects streaming input plus completed-recording PTT handling to the same Conversation owner.
- The internal pipeline self-test uses `Conversation.feed_audio()` for continuous simulated microphone chunks and `Conversation.submit_audio()` for PTT, with no virtual audio device.
- Python AST unused-import scan is clean, excluding the intentional `__future__.annotations` compiler directive.
- `git diff --check` is clean.
- `git fsck --full` reports only the inherited unreachable empty blob `e69de29bb2d1d6434b8b29ae775ad8c2e48c5391`; reachable Git objects are intact and the blob is not part of the working tree.

Release reproducibility was tested three ways: extract the ZIP into a clean directory; clone the untouched `d820b26` repository, preserve only `.git`, then copy the ZIP contents exactly as the user intends to do with GitHub Desktop; and apply the generated binary Git patch independently to another clean `d820b26` clone. All three produced the same 23-file SHA-256 source manifest. This means the ZIP and patch describe the same source tree, and the user's replacement workflow has been reproduced rather than merely assumed.

The validation environment is not a Windows/Iris Xe bare-metal machine, so it cannot truthfully prove the actual LunarG executable transaction, MSVC native Chatterbox compilation, Vulkan driver/ICD behavior, model downloads, Iris Xe inference speed, browser device selection, physical playback, or the external VB-CABLE black-box scenarios. Those are the next real acceptance tests on the target computer. If one fails, preserve the first failure and `data/runs/.../trident.log`, trace it to the owning layer, and fix that layer directly rather than adding a fallback.

## 32. Commit message for this final baseline

### Suggested summary

`Strip Runner X to Windows bare metal and restore Nano 180-unit baseline`

### Suggested extended commit description

This commit makes Runner X deliberately Windows x64-only and removes the Linux portability layer introduced during the previous one-command refactor. The target deployment is the real Windows/Iris Xe machine, so maintaining Linux assets, Zig/Ninja, POSIX locks/process groups, Linux Vulkan extraction, Linux GPU detection, executable-bit handling, and POSIX socket branches added maintenance cost without serving the actual product. Those paths are deleted rather than hidden behind compatibility abstractions.

The user-facing contract remains one command: `python main.py`. The stdlib bootstrap creates and owns `.venv`, installs the single `requirements.txt`, re-executes under `.venv`, automatically installs or repairs the Windows native/model runtime, and launches Gradio. There is no separate install CLI and no manual venv activation requirement.

The Windows LunarG prerequisite keeps the diagnosed `copy_only=1` fix. Trident needs a private project SDK and explicitly supplies its Vulkan/CMake environment, so it must not use LunarG's normal system installation transaction. The earlier bare-metal rollback after extraction was caused by that missing mode, not by the speech pipeline.

Nano remains the fresh default and uses two CFM steps. This commit deliberately changes Nano first speech-unit target from 80 back to 180 at the user's request. Git history must remain part of future analysis: `fb10adf` recorded a controlled A/B where 80 completed a three-sentence answer in 28.5 seconds versus 34.2 seconds at 180 and started audio about one second earlier. Therefore 180 is current configuration by explicit product decision, not a historically proven faster setting. Future performance work must benchmark 180 and 80 under identical warmed Iris Xe conditions before claiming a winner.

The runtime still has one speech pipeline: native Gradio microphone/file/text ingress converges on Conversation; hands-free audio uses Silero and Smart Turn; Parakeet transcribes; Gemma generates with preserved conversation history; sentence-aware speech units feed the same Chatterbox resident; streamed TTS is interruptible. Barge-in cancels only TTS/audio output, drains the cancelled turn's queued speech units, and keeps the serialized LLM/history path coherent.

VB-CABLE remains removed from product code and dependencies. The internal self-test owns synthesized PCM and feeds it directly through the Conversation ingress, which is the shorter and more reliable path. VB-CABLE is documented only as an external black-box Windows QA harness for exercising the real browser microphone boundary, multi-sentence natural speech, interruption during actual assistant playback, and context preservation.

Resident ownership remains content-derived, serialized by Windows file locking, fixed-port, and fail-hard. Installer readiness remains based on deployed revision identity rather than temporary build cache. Live settings remain authoritative in `data/live-settings.json`. Media conversion remains centralized in `media.py`. Logs remain run-owned and are the source for causal/performance diagnosis.

The final engineering methodology is documented in README.md. Future sessions must diagnose the real failing layer, read current source and relevant Git history before changing it, preserve one source of truth, delete duplicate ownership instead of layering abstractions, avoid speculative defensive coding/fallbacks, and validate long natural conversation—including barge-in and memory—on the actual Windows machine before calling the pipeline complete.

## 33. Session conversation record — content-complete technical dump

This section preserves the substance of the entire working session in chronological order. It is not a character-for-character export of ChatGPT UI/tool metadata or hidden reasoning. It records every user objective, every major finding, every architectural decision, every reversal, and every delivery/testing consequence needed by a future engineer.

### Session start: audit request

The user supplied twelve handover Markdown chunks plus a Trident repository archive and imposed strict rules: diagnosis first; current tracked source plus relevant Git history before planning; one source of truth; architectural efficiency over backward compatibility; no speculative defensive coding; consolidate duplicate implementations; reduce code without reducing functionality; and use modern packages/services only when they genuinely reduce LOC/ownership.

The first task was to cross-reference all documentation against the archive, trace claims to source/history, determine whether the system really behaved as documented, and diagnose why a fresh clone failed with `ModuleNotFoundError: numpy`.

The audit proved the NumPy failure was real. Recent VB-CABLE integration had put `cable.py` on `main.py`'s global import path and `cable.py` imported NumPy/SoundDevice before installer/bootstrap logic. A previously prepared machine masked this because its environment already contained those packages. Earlier architecture had allowed `python main.py` to enter a stdlib control plane first. The correct fix was to move dependency ownership ahead of application imports, not catch NumPy errors.

The audit also found that the archived branch was `runner-x` while stored remote HEAD pointed at `main`, so a plain clone did not necessarily reproduce the handover state. Fresh-clone instructions needed an explicit Runner X checkout.

Documentation/config mismatch was found around Nano. Local ignored `data/live-settings.json` could select Nano while tracked defaults selected another family. This showed the advertised single source of truth was not actually single across all entry points.

The audit traced additional mismatches: resident identity/ownership guarantees were stronger in documentation than in source; installer cleanup could delete evidence used by later readiness checks; agent and Conversation could own different run logs; documentation claimed barge-in even though tracked source initially had no active TTS cancellation; and several handover statements described measurements or intended tests rather than current enforceable behavior.

### First correction/delivery phase

The user requested a full corrected ZIP suitable for replacing Runner X and committing. The working tree was repaired around ownership instead of symptom patches: stdlib bootstrap and `.venv` re-exec; one Python dependency definition; live-settings authority; resident startup cleanup/serialization/content identity; deployed TTS revision identity independent of build caches; one agent/conversation run owner; stable run artifact names; and actual TTS cancellation/queue draining/audio reset for barge-in.

Source/platform-independent tests were added around those repaired boundaries. Packaging was initially withheld until the archive itself could be re-extracted and validated. A corrected ZIP, patch, and validation report were then produced.

The user confirmed a GitHub Desktop workflow: clone a new repository folder, switch to `runner-x`, preserve the clone's `.git`, delete the rest, copy the delivery contents, commit, and push. The assistant warned not to overwrite `.git` and not merely copy over existing files because obsolete files needed to disappear.

### Cross-platform experiment

The user then asked for a detailed handover commit description and suggested making the project OS-independent. A Windows+Linux refactor was attempted. Core pipeline audio was made independent of VB-CABLE, Linux assets/toolchain/process primitives were introduced, and native Gradio was retained instead of adding a custom microphone plugin.

Web/source investigation at that stage concluded that a cross-platform virtual microphone would be the wrong abstraction. Linux virtual devices still require OS audio graphs (PipeWire/PulseAudio/ALSA), and the coding agent already owns PCM. Therefore the internal agent should feed PCM directly into the same Conversation ingress used by browser audio after capture.

VB-CABLE, SoundDevice, PowerShell routing, and old cable command paths were removed from product code. `ui_streaming.py` was also folded into `conversation.py`, and media normalization was consolidated in `media.py`.

Custom Gradio component/FastRTC ideas were investigated and rejected because native Gradio already supplied streaming microphone/audio behavior while custom components/WebRTC would add frontend build/package ownership and did not remove an actual missing Trident capability.

Gradio 6.26.0 was found to be a development/main version rather than the stable PyPI release at the time, so runtime was pinned to stable 6.25.0.

The user requested a one-command experience. The old CLI surface was removed in favor of `python main.py` as bootstrap/install/launch. Useful functionality moved into Gradio: conversation, standalone TTS, ASR, self-test, resident status/stop, and logs.

A line-count measurement was initially overstated at roughly 30% because one baseline count omitted native TTS sources. That was corrected publicly. The project did not pursue minification or capability deletion to manufacture a percentage.

### Nano performance-history correction

During the refactor an intermediate assumption said the faster Iris Xe Nano setting used one CFM step. Git history disproved that as current truth: `2ed25ea` experimented with one step, but `4ca69b3` explicitly reverted it. Two CFM steps remain current.

Likewise, later history `fb10adf` explicitly changed first_chars 180 → 80 and recorded an A/B improvement. This was initially treated as a performance invariant.

### Bare-metal installer work and Windows failure

The user wanted the entire app to start with `python main.py` and allow first-run installation automatically. Bootstrap took ownership of `.venv`, runtime dependencies, and CMake. Linux work temporarily added Zig/Ninja/LunarG extraction to avoid system package managers.

A real Windows bare-metal run then failed during LunarG SDK installation. The log showed SDK payload extraction succeeded and was rolled back because the installer exited 1. Diagnosis isolated the defect to the Windows prerequisite command: Trident had invoked normal unattended SDK installation even though it wanted only a private SDK tree. LunarG's project-local `copy_only=1` mode was the correct fix. Linux support was not causally involved in that failure.

A corrected Vulkan-fix ZIP was delivered.

### Final product-direction decision

The user then decided Linux was not worth the maintenance complexity because the actual target is bare-metal Windows. The user also explicitly requested Nano first streaming unit 180 characters, despite the `fb10adf` A/B evidence favoring 80.

The final task became: derive from the uploaded baseline repository; remove Linux support completely; keep Windows/Iris Xe behavior and the `copy_only=1` fix; set Nano first_chars to 180; prepare exhaustive architecture/handover documentation; preserve all important experiments and contradictions; include fresh-clone/.venv instructions; and preserve VB-CABLE as an external long-conversation/barge-in test protocol rather than a product dependency.

The Windows-only source pass removed Linux release assets, Zig/Ninja dependencies, POSIX lock/process code, Linux Vulkan extraction, Linux GPU detection, Linux executable handling, POSIX socket code, and CMake platform branches. Windows-specific residents now use `msvcrt`, Windows process groups/taskkill, WinSock, Windows x64 binaries, MSVC, and the private LunarG SDK directly.

The final code keeps one-command startup, native Gradio, one microphone control, direct internal PCM self-test, content-derived resident identity, deployed TTS revision markers, live-settings SSOT, run-owned logs, and implemented TTS interruption.

### Final evidence rule

A future agent must not collapse “current source setting” and “historically measured fastest setting”. Current Nano first_chars is 180 by explicit user choice. Historical `fb10adf` evidence says 80 was faster in a controlled three-sentence A/B. This contradiction is deliberate and should motivate a new bare-metal benchmark, not a guess.

## 33A. User-request transcript from this session

The following records the user's visible requests in chronological order. It is included because the user explicitly wants the handover to preserve not only the final architecture but the product intent that drove the decisions. Assistant/tool chatter is not reproduced verbatim; the technical outcomes are captured in Section 33 and throughout this README.

### User request 1 — source audit and architectural rules

The user required diagnosis before code changes, current tracked source plus Git history before planning, one source of truth, architectural efficiency over compatibility, no speculative defensive coding, consolidation of duplicate paths, aggressive but functional code reduction, and web research where useful. The concrete task was to cross-reference all twelve Markdown handovers against the Trident archive, prove whether documented behavior matched source, and diagnose why a fresh clone failed because NumPy was not installed.

### User request 2 — corrected replacement archive

The user asked for the full corrected ZIP so it could replace the current Runner X working tree, be committed as one new commit, and pushed. The correction had to follow the same diagnosis-first/no-fallback/code-reduction rules.

### User request 3 — continue until delivery

When the first correction had not yet been frozen into a fully validated archive, the user explicitly asked to continue working until downloadable deliveries were ready.

### User request 4 — GitHub Desktop replacement workflow

The user asked whether it was safe to clone Trident in a new folder, switch to Runner X, copy the corrected files into the clone, then commit and push using GitHub Desktop. The established safe workflow became: preserve the fresh clone's `.git`, replace the rest of the working tree, then commit/push through the GUI.

### User request 5 — commit handover and cross-platform idea

The user requested a commit summary and a long extended description that a future AI session could read as a handover. The same request suggested making the repository operating-system independent if practical.

### User request 6 — remove VB-CABLE runtime dependency and research modern Gradio

The user asked to remove VB-CABLE as a project dependency, research late-2026 Gradio/custom-control/FFmpeg alternatives, remove dead/duplicated code, prefer modern dependencies only if they actually reduce LOC/ownership, and keep the original engineering rules. This led to the conclusion that no virtual microphone was needed internally: the agent already owns PCM and should feed Conversation directly. Native Gradio was retained because a custom component/WebRTC layer would add rather than remove ownership.

### User request 7 — one-command startup and stronger reduction

The user asked to make installation and use as simple as possible, ideally only `python main.py`, remove unnecessary command-line options, align useful former CLI capabilities with the Gradio UI, make Nano default, preserve recent Iris Xe speed work, and reduce code/complexity without regressions. This produced the one-command bootstrap/install/launch contract and removed the legacy application CLI surface.

### User request 8 — continue toward bare-metal delivery and future-session handover

The user asked to continue until a reduced bare-metal system could be delivered, with a natural-language extended commit message/README capable of bootstrapping a new coding session from zero context. The user emphasized long natural conversation, not just smoke tests.

### User request 9 — real Windows Vulkan failure and Linux reconsideration

The user supplied a real bare-metal traceback showing the LunarG Windows SDK installer extracting files and then aborting/rolling back with exit status 1. The user asked to determine whether this was a small isolated fix, in which case Windows+Linux could remain, or evidence of broader cross-platform complexity, in which case Linux should be removed. Diagnosis proved this exact failure was one missing LunarG `copy_only=1` mode flag and was not caused by Linux.

### User request 10 — final Windows-only direction, Nano 180, exhaustive README, VB-CABLE black-box testing

The user then made the final product decision: Linux support should be removed anyway because the actual target is Windows bare metal and a smaller branch-free system is easier to maintain. The user requested Nano first streaming unit 180 characters, a final replacement ZIP, and a single comprehensive Markdown handover that preserves all project knowledge, experiments, facts, decisions, relationships, architecture, coding rules, methodologies, fresh-clone/.venv instructions, and long-conversation testing. The user specifically wants future sessions to test long natural speech and barge-in through VB-CABLE as a simulation of a real human conversation while ensuring only TTS is interrupted and conversation context is preserved.

This final baseline implements that direction. Linux support is removed from source; Nano first_chars is 180 by explicit user override; VB-CABLE is documented only as an external QA harness; and the complete technical handover is this README.

## 34. Legacy handover source material — preserved for traceability

The following twelve project Markdown chunks were the initial handover basis. They are preserved verbatim-ish in this README so a future session can trace historical claims without needing the original ChatGPT workspace. **They are historical and some statements are superseded by the current source and the sections above.**



---

### Historical source: chunk_01_project_overview.md

# Trident Project - Complete Handover Documentation

## Project Overview

Trident is a local voice pipeline running on Windows with the following architecture:
- **ASR**: Parakeet TDT 0.6B v3 (via parakeet.cpp, Vulkan backend) — runs on port 17931
- **LLM**: Gemma 4 E2B (via llama.cpp, Vulkan backend) — runs on port 17932
- **TTS**: Chatterbox (via chatterbox.cpp, Vulkan backend) — runs on port 17933
- **VAD**: Silero VAD (ONNX Runtime, CPU)
- **Turn Detection**: Smart Turn v3.2 (ONNX Runtime, CPU)
- **UI**: Gradio 6.26.0 on port 7860
- **Hardware**: Intel Iris Xe (i5-1145G7, 4 cores / 8 threads, shared UMA memory)
- **Virtual Audio**: VB-CABLE (CABLE Input for playback, CABLE Output for capture)

## Key Design Philosophy

1. **Single Source of Truth**: `data/live-settings.json` is the authoritative configuration
2. **Resident Servers**: Models stay warm in persistent processes (parakeet, gemma, chatterbox)
3. **Identity-Based Restart**: Residents restart only when their identity (model + args) changes
4. **No Defensive Coding**: Fail hard at real defects, keep essential cleanup boundaries
5. **Code Consolidation**: Reuse existing paths, delete duplicates instead of adding abstractions
5. **Real-World Validation**: Every change tested on bare metal, no mocks

---

### Historical source: chunk_02_vb_cable_integration.md

# VB-CABLE Integration (Complete)

## The Problem
The conversation pipeline's ONLY audio ingress was the Gradio browser microphone → getUserMedia → Windows default recording device. Chromium binds the default device at capture start (crbug 40199570/40275281) and silently keeps it. To inject audio programmatically, VB-CABLE must become the default recording device BEFORE the UI page opens.

## The Solution: Three-Piece Architecture

### 1. Cable Routing (`tools/cable.ps1` + `cable.py`)
- **PowerShell + C# COM bridge** (EreTIk/frgnca lineage) — ONLY working approach
- `cable.py` wraps it: `_ps()` subprocess calls, `_active_captures()` parses registry for exact endpoint IDs
- `use()`: switches default capture to CABLE Output, reads back ID to verify, returns previous ID
- `restore(previous_id)`: restores original default
- **Why not comtypes?** comtypes 1.4.16 + Python 3.11.9 ctypes REQUIRE defaults for omittable COM out params; gists pass NULL → 'Invalid pointer'. Raw ctypes vtable calls AV'd (0xC0000005).

### 2. Cable Playback (`cable.play_wav`)
- Injects WAV into CABLE Input (render pin)
- **Critical**: CABLE Input rejects non-native rates (24 kHz → PortAudioError -9997)
- **Fix**: Query device `default_samplerate`, resample via `np.interp` before `sd.play`
- Same technique as UI mic path (`ui._pcm16k`)

### 3. Cable Capture Bridge (`cable.Microphone`)
- Opens CABLE Output natively: 44.1 kHz, 16 channels
- Callback: mean-mix to mono → interpolate to ASR_RATE (16 kHz) → feed `Conversation.feed_audio`
- **Architecture Decision**: Second ingress transport converging on same `feed_audio` as browser mic
- Gradio sessions are per-client isolated → browser automation abandoned

## Verified Behaviors
- Switch → ID read-back → restore verified bare metal
- Tone playback into CABLE Input: OK
- Agent cycles over cable: 6-10s conversational turns

---

### Historical source: chunk_03_agent_driver.md

# Agent Driver (`agent.py`) — Self-Evaluation Loop

## Purpose
Drives full speech cycles through VB-CABLE for automated testing and evaluation.

## Interface
```bash
python main.py agent --say "Prompt 1" --say "Prompt 2" --expect "regex1" --expect "regex2"
```
- `--say`: Text to synthesize and inject (one per turn)
- `--expect`: Regex matched against **the LLM answer** (not transcript). `-` skips check.

## Flow
1. Parse live-settings, determine `ingestion_mode` (continuous vs ptt)
2. `cable_use()` only in continuous mode
3. `warm_resident()` — ensures all three residents ready
4. Start `Conversation` engine
5. Continuous: start `cable.Microphone(engine.feed_audio)`
6. For each `--say`:
   - Synthesize prompt WAV via resident nano path
   - Continuous: `play_wav()` into CABLE Input
   - PTT: `engine.submit_audio(wav_pcm(prompt, ASR_RATE))` — mimics browser stop-recording
   - Wait for completion with **idle-based timeout + settle window**

## Turn Completion Logic (Hardened)
```python
deadline = time.monotonic() + 120s  # idle budget
last_status = None
settled = None
while True:
    complete = engine.turn > turn_before and f"TTS {engine.turn} · complete" in engine.status
    if complete and settled is None: settled = time.monotonic()
    if not complete: settled = None
    if complete and time.monotonic() - settled >= 1.5: break  # stability window
    if engine.status != last_status:
        last_status = engine.status
        deadline = time.monotonic() + 120s  # reset on ANY progress
    if time.monotonic() > deadline: raise RuntimeError("stalled")
    time.sleep(0.1)
```

## Why This Design?
- **Absolute timeouts killed healthy turns**: Smart Turn splits multi-sentence prompts into separate turns; long answers stream 20+ TTS units (each ~10-30s) while LLM still generates
- **Idle budget + settle**: Any status change resets 120s; 1.5s quiet after completion confirms done
- **Expects match answers**: Earlier bug matched against transcript — masked a passing memory test

## PTT Mode
- No cable routing, no Microphone
- Prompt synthesized → converted to 16kHz mono float32 via `cable.wav_pcm()` → `engine.submit_audio()`
- Same wait logic, same expect semantics

## Output
JSON summary with per-turn: say, heard, answer, expect, match, turn_s, conversation_run_dir

---

### Historical source: chunk_04_conversation_engine.md

# Conversation Engine (`conversation.py`) — Core Pipeline

## Architecture
Four worker threads communicating via `queue.SimpleQueue`:
1. **ASR Thread** (`_asr_loop`): VAD → Smart Turn → transcribe → dispatch
2. **LLM Thread** (`_llm_loop`): Stream Gemma → segment into speech units → enqueue to TTS
3. **TTS Thread** (`_tts_loop`): Pull units → stream synthesize via resident → emit audio
4. **Main Thread**: UI status, configuration, cleanup

## Audio Ingress
Two paths converge on `feed_audio(pcm_f32_bytes)`:
- **Browser mic**: Gradio `handsfree_mic.stream()` → `_pcm16k()` → `feed_audio()`
- **Cable mic**: `cable.Microphone` callback → mean-mix 16ch→mono → interp 44.1k→16k → `feed_audio()`

## VAD + Smart Turn (Continuous Mode)
- Silero VAD (ONNX, CPU): 512-sample frames, threshold 0.5, candidate silence 200ms
- On VAD "end": Smart Turn v3.2 (ONNX, CPU) evaluates last 8s of audio
- Smart Turn outputs (complete=True/False, probability)
- If complete: VAD reset → `_transcribe_turn("SMART")` → dispatch to LLM
- **Per-turn speech tracking**: SileroEndpoint now records `speech` flag on any "start" event
- Engine stop: if `speech` flag set → transcribe tail; else discard silently

## LLM Streaming & Segmentation
- Gemma streams tokens → `gemma_chat_stream()` yields deltas
- `SpeechSegmenter` accumulates; emits unit when:
  - ≥ `first_chars` (nano=80, turbo=120, v3=180) AND punctuation (.?!)
  - Or ≥ `chunk_chars` (nano=280, turbo=280, v3=300) → split at space
  - Flush at stream end
- Units enqueued to TTS with turn number and settings snapshot

## TTS Streaming
- Per-unit: `stream_synthesize()` → yields raw PCM chunks → `output_queue` → UI plays
- Buffered mode: full WAV written → `output_queue` gets file path
- **Cached resident URLs**: `gemma_base` and `tts_base` resolved once in `start()`
- `configure()` invalidates `tts_base` on `tts_family/voice/language` change → next turn re-resolves (restarts resident)

## Telemetry (per turn in trident.log)
```
component=conversation event=dispatch turn=N reason=SMART|MANUAL|PTT text="..."
component=llm event=complete turn=N ttfa_ms=... total_ms=... chars=...
component=llm event=answer turn=N text="..."
component=tts event=complete outcome=ok unit=K audio_s=... rtf=... x_realtime=...
component=vad event=smart_turn complete=0/1 p=0.XXX elapsed_ms=YY.ZZZ
```

## Engine Stop Silent Tail Fix
- Before: `_transcribe_turn("STOP")` always ran → phantom empty transcription
- After: SileroEndpoint tracks `speech` (set on "start" event, reset in `reset()`)
- Stop path: `if self.vad.speech: self._transcribe_turn("STOP") else: self._discard_turn()`
- Preserves real mid-utterance stops; kills silence-only final pass

---

### Historical source: chunk_05_resident_management.md

# Resident Management (`resident.py`) — VRAM Safety & Identity

## Single-Instance Guarantee
Each component (parakeet, gemma, chatterbox) runs as ONE detached process on a fixed port:
- parakeet: 17931, gemma: 17932, chatterbox: 17933
- Identity = SHA256(server_exe + model_file + argv_extras)
- `_ensure()` checks port → if open AND identity matches → reuse URL
- If identity changed → `_terminate()` old → `_wait_port_closed()` → spawn new
- **No double-load possible**: kill-before-spawn, single port per component

## Startup Sequence
1. `_identity()` computed from exe+model signatures + config flags
2. If port open with same identity → return cached URL (instant)
3. If port open with DIFFERENT identity → terminate old → wait for port close
4. If port open with NO recorded PID → error (foreign process)
5. Spawn detached (`CREATE_NEW_PROCESS_GROUP`), write state JSON
6. Probe readiness (port open / HTTP 200) with timeout
7. On success: note `resident event=ready`; on failure: cleanup state

## Chatterbox Identity Details
Extra identity inputs: family, language, reference file signature, codec file signature
- Changing ANY of these → new identity → restart (intentional)
- `configure()` in conversation sets `tts_base=None` on voice change → next turn re-resolves → restart
- Verified: mid-session voice change (trump→obama) restarts chatterbox, synthesizes with new voice

## Stop & Cleanup
- `stop_all()` terminates all three
- `_terminate()` uses `taskkill /PID /T /F` (Windows)
- `_wait_port_closed()` polls port for 10s
- State files under `RUNTIMES/.resident/{name}.json`

## VRAM on Iris Xe (UMA)
- No dedicated VRAM — shares system RAM
- Gemma (3.3GB q4_0) + Chatterbox (~300MB) + Parakeet (~600MB) = ~4.2GB
- Single-instance per component prevents duplicate allocations
- Contention: both use Vulkan on same iGPU → serialization at driver level
- Measured: nano RTF 0.7 idle → 1.0-2.2 under gemma load (iGPU saturation)

---

### Historical source: chunk_06_tts_families.md

# TTS Families & Performance Characterization

## Three Families (all English-only except v3)

| Family | Model | Params | CFG | Streaming | first_chars | chunk_chars | cfm_steps |
|--------|-------|--------|-----|-----------|-------------|-------------|-----------|
| **nano** | Chatterbox-Nano | 110M | No (meanflow 2-step) | Yes | **80** | 280 | 2 |
| **turbo** | Chatterbox-Turbo | 350M | No (meanflow 2-step) | Yes | 120 | 280 | 2 |
| **v3** | Multilingual V3 | 500M | Yes (CFG, 10-step CFM) | Yes | 180 | 300 | 5 |

## Iris Xe Measured Performance (bare metal)

### nano (default)
- **Idle**: RTF 0.6-0.8, x_realtime 1.3-1.6, TTFA ~1.6-2.0s
- **Under LLM contention**: RTF 0.9-2.2, x_realtime 0.45-1.1, TTFA degrades to 7-14s
- **Contention cause**: Gemma streaming (Vulkan) + TTS synthesis (Vulkan) on same iGPU

### turbo
- **Idle**: RTF ~0.85-1.06, x_realtime ~1.0-1.17
- **Slightly slower** than nano, same contention pattern

### v3 (multilingual, CFG)
- **Idle**: RTF 1.6-2.4, x_realtime 0.4-0.6 — **below realtime**
- **Under contention**: RTF 2-3+, synthesis cannot keep up
- **Not recommended** for real-time voice on this hardware

## Key Tuning Decisions

### first_chars: 180 → 80 (nano)
- **Why**: 180 chars forced multi-sentence accumulation before first audio
- **Result**: 3-sentence answers 34.2s → 28.5s; first unit starts ~1s earlier
- **Later units**: pipeline at 1.3-1.5× realtime overlapping playback
- **Single-sentence replies**: unaffected (flush at stream end)

### CPU vs Vulkan for nano
- **Tested**: `gpu_layers=0, threads=8` (all 8 logical cores)
- **Result**: RTF 1.6-3.3 (x_realtime 0.3-0.63) — **lost decisively**
- **Why**: Vendor claim "3× realtime on 8 cores" assumes 8 performance cores; this i5-1145G7 has 4 cores / 8 HT threads sharing UMA bandwidth
- **Vulkan wins** both idle AND contention cases

### Smart Turn Threads
- ONNX CPUExecutionProvider, `intra_op_num_threads=1`
- Measured: 61-72ms per decision — negligible
- No threading change warranted

## Model Files (Iris Xe specific)
- Iris Xe block in config.py rewrites codec quant to `q4_0` and filename to `-irisxe-q4_0-rawf32-v1.gguf`
- All three families use q4_0 T3; codec per-family

---

### Historical source: chunk_07_ui_controls.md

# Gradio UI (`ui.py`) — Controls Audit Results

## Tab Structure
1. **Conversation**: Start/Stop engine, hands-free vs PTT mic, transcript, answer, live audio
2. **TTS**: Manual text→speech (streaming/buffered), voice overrides
3. **CLI**: ASR, Brain, TTS, Full Pipeline — mirrors `main.py` commands
4. **Runtime**: Resident status/warm/stop, Install/Repair
5. **Logs**: FileExplorer + live log tail

## All 23 Endpoints — Verified Working
| Endpoint | Status | Notes |
|----------|--------|-------|
| `start_conversation` | ✅ | Creates per-session Conversation engine |
| `conversation_pump` | ✅ | Streams transcript/answer/audio to UI |
| `feed_conversation` | ✅ | Hands-free mic → feed_audio |
| `ptt_submit` | ✅ | Upload WAV → submit_audio → dispatch |
| `manual_submit` | ✅ | Text → dispatch → answer |
| `save_config` | ✅ | Persists live-settings; calls `engine.configure()` mid-session |
| `stop_conversation` | ✅ | Closes engine, restores UI |
| `speak` (TTS tab) | ✅ | Streaming + buffered, voice overrides |
| `cli_asr` | ✅ | Upload WAV → transcript |
| `cli_brain` | ✅ | Text → Gemma answer |
| `cli_tts` | ✅ | Streaming + buffered WAV render |
| `cli_run` | ✅ | Full ASR→Brain→TTS pipeline |
| `cli_resident_status` | ✅ | Shows pid, url, family, language |
| `cli_resident_warm` | ✅ | Pre-warms all three with current overrides |
| `cli_resident_stop` | ✅ | Terminates all residents |
| `cli_install` | ✅ | **Exercised last** — full rebuild + prune validated |
| `read_log` / `read_log_2` | ✅ | Log browsing + live tail |

## Critical Flows Verified

### Mid-Session Voice Change
1. `start_conversation` (nano/trump)
2. `save_config` with `tts_voice=obama` → `engine.configure()` → `tts_base=None`
3. `manual_submit` → dispatch → LLM → TTS thread sees `tts_base=None` → `tts_endpoint()` → `ensure_chatterbox()` with new reference → **resident restart** (identity changed) → synthesis with obama voice
3. Logged: `resident event=restart reason=identity_changed` → `resident event=ready` → `tts event=complete`

### PTT Mode
- `ingestion_mode=ptt` → `handsfree_mic` hidden, `ptt_mic` shown
- Record → Stop Recording → `ptt_submit(audio)` → `engine.submit_audio(pcm)` → dispatch reason=PTT

### Resident Warm
- Reuses running residents if identity matches; restarts if overrides differ
- Used before agent runs and CLI commands for consistent cold/warm comparisons

## Gradio Client Lessons (for headless testing)
- File inputs require `handle_file(path)` — raw string fails validation
- `view_api()` essential for exact parameter counts (cli_run = 26 params!)
- Session isolation: each `Client()` gets own session_hash → separate Conversation engine
- Pump generator not directly consumable; verify via log file instead

---

### Historical source: chunk_08_installer_repair.md

# Installer Repair Control (`installer.py`) — Full Bootstrap Validation

## What It Does
`python main.py install --family all` (or UI `cli_install`) performs complete system setup:

1. **Prerequisites**: Python 3.11+, git, cmake, MSVC Build Tools, Vulkan SDK
2. **Runtime Binaries**: Downloads pinned releases
   - parakeet.cpp v0.5.0 Vulkan → `tools/runtime/parakeet/parakeet-server.exe`
   - llama.cpp b10453 Vulkan → `tools/runtime/gemma/llama-server.exe`
3. **Reference Voices**: Downloads trump/obama/kamala WAVs to `data/`
4. **TTS Native Build** (chatterbox.cpp + ggml):
   - Clones repos at pinned revisions
   - Applies Vulkan FP16 patch (disables FP16 on pre-Turing NVIDIA only)
   - CMake configure + build → `tts-cpp.lib`, `mtl_tokenizer`
   - Build `trident-tts-server.exe` → copy to `tools/runtime/tts/` + DLLs
5. **Models**: Downloads/validates all GGUF/ONNX models to `models/`
6. **Clean Install Artifacts**: **PRUNES** build caches by design:
   - `third_party/` (chatterbox.cpp, ggml sources)
   - `tts/build/`
   - `tools/huggingface/`, `tools/downloads/`
   - `tools/git/`, `tools/cmake-*/`, `tools/VulkanSDK/`

## Critical Finding: Repair = Full Rebuild
When exercised (final validation):
- Native marker was stale → `reason=source_changed`
- **Full rebuild executed**: clone → patch → configure (15.7s) → build chatterbox (234s) → build server (3.5s)
- Resident chatterbox stopped (pid 9872) → new binary deployed → verified
- **All models re-validated**: parakeet, gemma, smart-turn, chatterbox v3/turbo/nano
- Pruned build caches → `third_party` now **missing**

## Post-Repair State
- **Runtime works identically**: agent cycle passes (53s for long answer, contention pattern unchanged)
- **Cannot rebuild again** without re-downloading toolchains
- `tools/runtime/tts/trident-tts-server.exe` + DLLs remain (deployed artifacts)
- `models/` intact, `data/` intact, `.venv` intact

## User Action Required
**From fresh clone**: The project CANNOT rebuild native components until the toolchains are re-acquired. This is BY DESIGN — the installer is a "repair" that validates everything then cleans up.

## Bootstrap Instructions for Fresh Clone
```powershell
# 1. System prerequisites (one-time)
# - Windows 10/11 x64
# - Python 3.11+ installed and on PATH
# - ffmpeg via winget: `winget install Gyan.FFmpeg`

# 2. Clone & venv
git clone https://github.com/wgabrys88/Trident.git
cd Trident
python -m venv .venv
.venv\Scripts\pip install --upgrade pip
.venv\Scripts\pip install -r requirements-ui.txt

# 3. Full install (downloads toolchains, builds, downloads models)
.venv\Scripts\python.exe -X utf8 main.py install --family all

# 4. Verify
.venv\Scripts\python.exe -X utf8 main.py agent --say "Test." --expect "."
```

---

### Historical source: chunk_09_live_settings.md

# Live Settings — Single Source of Truth (`config.py`)

## File: `data/live-settings.json`
Authoritative runtime configuration. Written by UI `save_config`, read by all entry points.

```json
{
  "ingestion_mode": "continuous",    // "continuous" (hands-free) or "ptt" (push-to-talk)
  "system_prompt": "...",            // Injected into LLM system message
  "tts_family": "nano",              // "nano" | "turbo" | "v3"
  "tts_join": "crossfade",           // "crossfade" | "chunks"
  "tts_language": "en",              // Language code (v3: 23 langs; nano/turbo: en only)
  "tts_mode": "real",                // "real" (streaming) | "buffered"
  "tts_voice": "trump",              // "trump" | "obama" | "kamala" | custom ref
  "vad_silence_ms": 200,             // Silero candidate silence duration
  "vad_threshold": 0.5               // Silero speech probability threshold
}
```

## Key Behaviors

### Settings Flow
1. UI `save_config` → validates → writes JSON atomically → `engine.configure(settings)`
2. Agent/Cli → `load_live_settings(data_dir)` → builds argparse.Namespace
3. Conversation `start()` → reads settings ONCE at startup
4. `configure()` mid-session: compares old vs new
   - VAD changed → pushes `("vad-config", (threshold, silence_ms))` to ASR queue
   - Voice changed (`tts_family/voice/language`) → `tts_base = None` → next TTS turn re-resolves

### Fixed Corruption Bug
**Before**: `load_live_settings()` returned module-level `LIVE_SETTINGS` dict; `save_live_settings()` did `LIVE_SETTINGS.clear(); LIVE_SETTINGS.update(settings)` — callers that loaded, modified, and saved back wiped all keys.

**After**:
- `load_live_settings()` returns `dict(LIVE_SETTINGS)` (copy)
- `save_live_settings()` re-syncs global from the **written payload** (`json.loads(payload)`)

## TTS Fields (Overrides)
14 fields from `TTS_FIELDS` applied via `effective_family()`:
- **Runtime**: n_gpu_layers, context, threads, fastconv
- **Sampling**: seed, max_tokens, top_k, cfm_steps, first_chunk_chars, chunk_chars, top_p, min_p, temperature, repeat_penalty
- **Voice**: cfg_weight, exaggeration (v3 only; ignored for nano/turbo)

## Hardware Profile Auto-Detection
`config.py` detects GPU at import:
- `pascal` (GTX 10-series) → `GGML_VK_DISABLE_F16=1`
- `irisxe` (Intel Iris Xe) → codec quant forced to `q4_0`, filename rewritten to `-irisxe-q4_0-rawf32-v1.gguf`
- Unknown GPU → RuntimeError (explicit unsupported)

---

### Historical source: chunk_10_logging_data.md

# Logging & Data Organization

## Run Logs: `data/runs/<STAMP>-<COMMAND>/`
Each invocation creates a timestamped run directory:
```
20260826-155004-632771-agent/
├── trident.log          # Structured telemetry (THE source of truth)
├── transcript.txt       # Full conversation transcript
├── answer.txt           # Last answer
├── meta.txt             # Key-value metadata
├── prompt-00.wav        # Synthesized prompt for turn 0
├── prompt-01.wav        # Synthesized prompt for turn 1
└── tts-turn-XXXX-XXX.wav # Per-unit TTS outputs
```

## Log Format (trident.log)
Concise, local, component-owned. Key event types:

### Pipeline Lifecycle
```
component=pipeline event=start command=agent hardware=irisxe
component=pipeline event=finish outcome=ok
```

### Cable Operations
```
component=cable event=default_set endpoint={...} roles=console,multimedia
component=cable event=capture_routed previous="..." current="CABLE Output (...)"
component=cable event=mic_start device=19 rate=44100 channels=16
component=cable event=inject device=15 src_rate=24000 out_rate=48000 duration_s=2.120
component=cable event=mic_stop
```

### Resident Events
```
component=resident event=start name=chatterbox policy=vulkan_f16=default
component=resident event=ready name=chatterbox pid=1234
component=resident event=restart name=chatterbox reason=identity_changed
component=resident event=stop name=chatterbox pid=1234
```

### ASR (Parakeet)
```
component=asr event=done duration_s=3.080 chunks=1 request_ms=348.853 rtf=0.1133 x_realtime=8.83
```

### LLM (Gemma)
```
component=llm event=complete turn=1 ttfa_ms=1238.817 total_ms=1780.293 chars=39
component=llm event=answer turn=1 text="Hello, Agent Smith..."
```

### TTS (Chatterbox)
```
component=tts event=reference_ready duration_s=21.931
component=tts event=complete outcome=ok unit=1 audio_s=2.560 chunks=1 total_ms=2254.6 rtf=0.8807 x_realtime=1.14
```

### Turn Detection
```
component=vad event=smart_turn complete=1 p=0.985 elapsed_ms=65.569
component=conversation event=dispatch turn=1 reason=SMART text="Hello world"
```

## Why This Matters
- **Single file = full diagnosis**: No UI scraping needed
- **Timestamps + component tags** = cross-thread timeline reconstruction
- **Quantitative metrics** (RTF, x_realtime, TTFA) = performance tracking
- **Per-turn tagging** = correlates ASR→LLM→TTS latency

## .gitignore Allowlist Pattern
```
*                          # ignore everything
!/agent.py                 # explicitly track these
!/cable.py
!/config.py
!/conversation.py
!/installer.py
!/local_api.py
!/log.py
!/main.py
!/media.py
!/resident.py
!/ui.py
!/ui_streaming.py
!/vad.py
!/requirements-ui.txt
!/tools/
/tools/*                   # but ignore tools contents
!/tools/cable.ps1          # except this
```
**Rule**: New source files MUST be added to allowlist or git silently skips them.

---

### Historical source: chunk_11_long_speech_tests.md

# Long Speech VB-CABLE Test Protocol

## Purpose
Validate the pipeline handles **natural human conversation patterns**:
- Multi-sentence human utterances (split by VAD+SmartTurn)
- Human interrupting during TTS playback (barge-in)
- Context preservation across turns
- TTS interruption without context loss

## Test Scenarios

### Scenario 1: Multi-Sentence Prompt (Continuous Mode)
**Setup**: `ingestion_mode=continuous`, cable routed
**Action**: Single `--say` with 3-4 sentences separated by natural pauses
```bash
python main.py agent \
  --say "Hello, my name is John. I live in New York. I work as a software engineer. What is the capital of France?" \
  --expect "Paris"
```
**Expected**:
- VAD+SmartTurn splits into multiple turns (each sentence complete)
- Each turn dispatched → answered
- Final answer recalls context ("Your name is John...")

### Scenario 2: Human Interrupts During TTS (Continuous Mode)
**Setup**: Continuous mode, cable routed
**Action**:
1. Start agent with a long prompt that generates 30+ second answer
2. **While TTS is playing**, inject a second prompt via cable (simulates human speaking over assistant)
3. Verify: TTS interrupted, new turn captured, context preserved

**Implementation** (manual test):
```python
# In a script or interactive session:
engine = Conversation(...)
engine.start()
mic = Microphone(engine.feed_audio)
mic.start()

# Turn 1: long answer
play_wav(long_prompt_wav)
# Wait ~3s into TTS playback (check engine.status for "TTS 1 · streaming")
# Turn 2: interrupt
play_wav(interrupt_prompt_wav)  # "Wait, what about Germany?"
# Wait for completion
# Verify: answer addresses Germany, context (name) preserved
mic.stop()
engine.close()
```

### Scenario 3: PTT Mode Multi-Utterance
**Setup**: `ingestion_mode=ptt`
**Action**: Sequential `engine.submit_audio()` calls with conversational flow
```python
engine = Conversation(...)
engine.start()
# Turn 1
engine.submit_audio(wav_pcm("My name is Alice.", ASR_RATE))
wait_for_turn(engine, 1)
# Turn 2
engine.submit_audio(wav_pcm("What did I just say my name was?", ASR_RATE))
wait_for_turn(engine, 2)
# Verify answer: "Your name is Alice."
engine.close()
```

### Scenario 4: Rapid Fire Questions (Continuous)
**Setup**: Continuous mode
**Action**: `--say` × 5 with short follow-ups
```bash
python main.py agent \
  --say "My name is Bob." \
  --say "My favorite color is blue." \
  --say "What is my name?" \
  --say "What is my favorite color?" \
  --say "Repeat both." \
  --expect - --expect - --expect "Bob" --expect "blue" --expect "Bob.*blue"
```

## Success Criteria
| Scenario | Must Pass |
|----------|-----------|
| Multi-sentence split | All sentences dispatched, context preserved in final answer |
| Barge-in | TTS interrupted cleanly, new turn captured, previous context intact |
| PTT multi-turn | Sequential submissions work, memory intact |
| Rapid fire | All 5 turns complete, final answer recalls both facts |

## Failure Modes to Watch
- **Context loss**: Answer forgets earlier facts → history not propagating
- **TTS not interrupted**: New audio queued behind playing TTS → pipeline stall
- **Double dispatch**: Same audio triggers two turns → duplicate answers
- **Context bleed**: Answer to turn N includes facts from turn N+1 → race condition
- **Silent stall**: Engine status stops updating → idle timeout fires incorrectly

## Logging Verification
Check `trident.log` for:
```
component=conversation event=dispatch turn=N reason=SMART text="..."
component=llm event=answer turn=N text="..."
component=tts event=complete outcome=ok unit=K ...
component=vad event=smart_turn complete=1 p=... elapsed_ms=...
```
Each turn should have: dispatch → llm complete → answer → tts units → complete

---

### Historical source: chunk_12_user_instructions.md

# User Re-Clone & Install Instructions (MANDATORY — Current State)

## Why This Is Required
The installer repair control was exercised as the final validation step. It:
1. Detected stale native build marker
2. **Fully rebuilt** chatterbox.cpp + ggml from source (clone → patch → build 234s)
3. Redeployed fresh `trident-tts-server.exe` + DLLs to `tools/runtime/tts/`
3. **Pruned ALL build caches**: `third_party/`, `tts/build/`, `tools/git/`, `tools/cmake/`, `tools/VulkanSDK/`, `tools/downloads/`, `tools/huggingface/`

**Result**: The working copy CANNOT rebuild native components again. A fresh clone + install is required.

---

## Step-by-Step Instructions

### Prerequisites (One-Time on This Machine)
```powershell
# 1. Ensure Python 3.11+ is installed and on PATH
python --version
# Must show 3.11.x or higher

# 2. Install ffmpeg via winget (required for audio conversion)
winget install Gyan.FFmpeg
# Verify:
ffmpeg -version
```

### Fresh Clone & Environment
```powershell
# 3. Clone the repository (remote has all tracked sources)
git clone https://github.com/wgabrys88/Trident.git
cd Trident

# 4. Create virtual environment
python -m venv .venv

# 5. Activate venv and upgrade pip
.venv\Scripts\activate
pip install --upgrade pip

# 6. Install Python dependencies (ALL runtime deps in requirements-ui.txt)
pip install -r requirements-ui.txt
# Contents: gradio==6.26.0, numpy>=2,<3, onnxruntime==1.29.0, silero-vad-notorch==6.2.1.1, sounddevice==0.5.6
```

### Full Install (Bootstraps Everything)
```powershell
# 7. Run the installer — this downloads toolchains, builds native code, downloads models
.venv\Scripts\python.exe -X utf8 main.py install --family all
```

**What this does (takes 5-15 minutes first time):**
- Downloads git, cmake, MSVC Build Tools, Vulkan SDK into `tools/`
- Clones chatterbox.cpp + ggml at pinned revisions
- Applies Vulkan FP16 patch (disables FP16 on pre-Turing NVIDIA)
- Builds `tts-cpp.lib` + `trident-ts-server.exe` (native C++)
- Downloads pinned parakeet.cpp + llama.cpp release binaries
- Downloads/validates ALL models (Parakeet, Gemma, Smart Turn, Chatterbox nano/turbo/v3)
- Downloads reference voices (trump, obama, kamala)
- Deploys runtime binaries to `tools/runtime/{parakeet,gemma,tts}/`
- **Prunes build caches** (by design — see above)

### Verify Installation
```powershell
# 8. Quick smoke test (continuous mode over VB-CABLE)
.venv\Scripts\python.exe -X utf8 main.py agent --say "Install verification. Reply with one sentence." --expect "."

# 9. Check resident status
.venv\Scripts\python.exe -X utf8 main.py resident status
# Should show all three: parakeet, gemma, chatterbox — ready with pids

# 10. Launch UI (optional)
.venv\Scripts\python.exe -X utf8 main.py --ui
# Opens http://127.0.0.1:7860
```

---

## Post-Install Validation Checklist
- [ ] `git status` shows clean (only ignored paths changed)
- [ ] `tools/runtime/parakeet/parakeet-server.exe` exists
- [ ] `tools/runtime/gemma/llama-server.exe` exists
- [ ] `tools/runtime/tts/trident-tts-server.exe` + DLLs exist
- [ ] `models/` contains all GGUF/ONNX files
- [ ] `data/live-settings.json` exists with defaults (nano/trump/en/continuous)
- [ ] Agent cycle completes with match=true
- [ ] UI launches and all 23 endpoints respond

---

## Key Files That Must Exist in Fresh Clone (Tracked)
```
agent.py, cable.py, config.py, conversation.py, installer.py
local_api.py, log.py, main.py, media.py, resident.py
ui.py, ui_streaming.py, vad.py
requirements-ui.txt
tools/cable.ps1
tts/CMakeLists.txt, tts/include/*.hpp, tts/src/*.cpp
.gitignore, .gitattributes
```

---

## Troubleshooting

### "VB-CABLE not found"
- Install VB-CABLE driver (VB-Audio Virtual Cable)
- Verify in Windows Sound settings: "CABLE Input" (playback), "CABLE Output" (recording)
- `python main.py cable status` should show both endpoints

### "Model missing" during install
- Re-run: `.venv\Scripts\python.exe -X utf8 main.py install --family all`
- Installer is idempotent — only downloads missing/changed artifacts

### "Resident port in use"
- `.venv\Scripts\python.exe -X utf8 main.py resident stop`
- Then retry

### "CUDA/GPU errors" on Iris Xe
- This is expected — Iris Xe uses Vulkan backend, not CUDA
- Config auto-detects hardware profile and sets correct flags
## 35. Final instruction to a completely fresh AI session

You are inheriting Trident Runner X as a Windows x64 bare-metal local voice system. Read this entire README before editing. Then read the current tracked source and current Git diff/history. Do not trust the historical appendices over current source.

Your first objective is reproducibility on the user's real Windows/Iris Xe machine with one command: `python main.py`. Do not refactor until the current bare-metal run has been observed and the exact first failing layer, if any, has been identified.

The product must behave like a useful natural voice conversation system, not a sequence of isolated demos. Test long multi-sentence human speech, contextual follow-ups, rapid questions, and real barge-in. When the human speaks over assistant TTS, interrupt synthesis/playback only; preserve conversation history and continue processing the new turn. Use both the internal PCM self-test and the external VB-CABLE/browser black-box protocol because they validate different boundaries.

Treat Nano/Iris Xe performance as a measured engineering problem. Preserve two CFM steps. Current source deliberately uses first_chars=180, while Git evidence says 80 previously won one controlled A/B. Benchmark before changing. Keep logs quantitative and stage-specific.

Follow the rules: diagnose first; source truth before history; one owner per mutable state; no speculative defensive coding or fallback transports; consolidate duplicate paths; delete obsolete interfaces rather than carrying compatibility; reduce ownership/LOC without reducing functionality; and validate the final packaged artifact, not only a developer working tree.
