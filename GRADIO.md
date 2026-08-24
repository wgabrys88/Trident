# Trident full-duplex Gradio engine

Trident keeps its native runtimes as the model owners. The Gradio layer controls them; it does not implement replacement ASR, LLM, or TTS inference paths.

## Runtime architecture

The Conversation tab starts three independent inference stages and one UI pump:

```text
browser microphone
      |
      v
Gradio Audio.stream -- 160 ms transport --> ASR queue --> Parakeet EOU C API process
                                                   |            |
                                                   |            +--> newly-finalized text + EOU/EOB
                                                   +--> Silero VAD ONNX
                                                                  |
                       EOU | VAD silence | char threshold | PTT/manual cut
                                                                  |
                                                                  v
                                                            LLM queue
                                                                  |
                                                                  v
                                                  resident llama-server / Gemma
                                                  streaming SSE response to UI
                                                                  |
                                                        completed spoken reply
                                                                  v
                                                            TTS queue
                                                                  |
                                                                  v
                                              resident chatterbox.cpp TCP server
                                                   |                       |
                                             native PCM stream        buffered WAV
                                                   |                       |
                                             one chunk ahead          one WAV ahead
                                                   +-----------+-----------+
                                                               v
                                                       Gradio Audio output
```

ASR, Gemma and Chatterbox are separate resident/native processes. Conversation orchestration uses one dedicated Python thread per inference stage. Gradio microphone callbacks only convert the browser audio to 16 kHz mono float32 and enqueue it. No Gradio-wide model concurrency group serializes the stages.

The ASR thread is the only owner of the Parakeet stream and Silero VAD state. This keeps feed, EOU, VAD cut, PTT cut, manual cut and shutdown ordering deterministic without a Python lock around the C API.

The LLM thread owns one streamed chat-completion request at a time. The TTS thread owns synthesis requests independently. STT continues ingesting while Gemma is generating and while Chatterbox is speaking an earlier response. Completed transcript turns queue behind an in-flight Gemma turn rather than blocking microphone capture.

## Conversation input modes

### Continuous

Start engine enables browser microphone recording immediately. Audio is transported to Trident every `LIVE_AUDIO["asr_feed_seconds"]` seconds. The default is 0.16 s.

### Push to talk

`PTT ON` opens the server-side microphone gate before enabling browser recording. `PTT SEND` stops browser recording and queues a tagged Parakeet stream cut. The ASR worker processes all already-enqueued microphone chunks before that cut, finalizes the utterance, restarts the Parakeet stream without unloading the model, then dispatches the remaining transcript.

### Manual

`Submit now` with text bypasses STT and sends that text directly into the same LLM queue used by speech. Submitting an empty manual field queues a tagged ASR cut and dispatches the current speech tail.

## Turn dispatch triggers

The transcript accumulator is dispatched when any enabled automatic trigger fires:

- **Parakeet native EOU**: `parakeet_realtime_eou_120m-v1` emits a native EOU event. EOB is displayed but is not treated as a conversational turn.
- **Silero VAD silence**: the ONNX VAD iterator emits an end event after the configured silence duration.
- **Character threshold**: the accumulated, not-yet-dispatched transcript reaches the configured character count.

PTT SEND and manual flush are explicit dispatch triggers independent of those automatic triggers.

EOU and VAD can be enabled or disabled independently from the UI. Character threshold remains the secondary bounded-latency trigger.

A dispatch snapshots the current conversation settings. Later UI edits therefore affect later turns without changing the prompt, voice, family, language, join mode, or TTS delivery mode of a turn that is already queued.

## Parakeet models

Trident deliberately keeps two Parakeet model roles:

| Role | Model | Path |
| --- | --- | --- |
| normal `main.py asr` / `run` CLI | Nemotron 3.5 ASR Streaming 0.6B | existing resident HTTP server |
| live Conversation tab | Parakeet Realtime EOU 120M v1 | direct `parakeet.dll` C API |

The live path uses the dedicated EOU model because current `parakeet.cpp` documents `<EOU>/<EOB>` streaming for `parakeet_realtime_eou_120m-v1`. The regular Nemotron model remains unchanged for the existing multilingual CLI path.

The live C API requires ABI v5 or newer. It consumes 16 kHz mono float32 PCM and returns newly-finalized text. EOU and EOB are read from the streaming JSON result rather than parsed from transcript text.

The model stays loaded in the Parakeet worker process. A VAD/PTT/manual cut finalizes only the current stream and immediately starts another stream on the same model context.

## Silero VAD

The UI environment installs `silero-vad-notorch` and ONNX Runtime. VAD is CPU-side endpoint detection, not a competing GPU speech model.

`config.py` defines the ASR sample rate and VAD frame size. The default frame is 512 samples at 16 kHz. The UI exposes speech threshold and minimum silence duration. Applying a changed VAD configuration queues a VAD reinitialization on the ASR owner thread.

## Gemma

Gemma remains owned by the existing resident `llama-server` process. Conversation uses its OpenAI-compatible streamed chat-completion endpoint. SSE deltas update `Gemma streaming response` as they arrive.

The UI system prompt is persisted in `config.py`. It supports the existing `{tts_language}`, `{tts_language_name}`, `{language}` and `{language_name}` substitutions. It can be replaced with a parrot-style instruction or any other spoken-dialogue policy without changing inference code.

Conversation history is bounded by `LIVE_AUDIO["llm_history_turns"]` and is maintained only by the LLM worker.

Trident does not add a second token-stream protocol between Gemma and Chatterbox. The existing Chatterbox resident request takes a complete text payload, so the completed Gemma spoken reply is queued to TTS. This preserves the native server protocol and avoids repeatedly restarting synthesis on tiny LLM token fragments. Pipeline overlap occurs across stages and queued turns: STT remains live during LLM/TTS work, and Gemma can process a later turn while Chatterbox is speaking a completed earlier turn.

## Chatterbox delivery

### Native stream

Conversation calls the existing `stream_synthesize()` path. Chatterbox performs its native streaming algorithm. Gradio only aggregates returned PCM for browser playback.

The transport accumulator uses `LIVE_AUDIO["tts_gradio_min_seconds"]`, default 1.25 seconds, and holds one aggregate chunk ahead before emitting audio. The one-chunk lookahead reduces audible discontinuities caused by browser/network scheduling without changing Chatterbox synthesis behavior.

### Buffered WAV

Buffered mode uses the existing family `TTS_CHUNK` splitter and existing resident non-streaming synthesis. `LIVE_AUDIO["tts_fake_group_chunks"]` defaults to five, so one browser delivery request contains about five native text chunks rather than restarting synthesis for every small text fragment.

Current defaults are:

| Family | first native chunk | later native chunk | first five-chunk macro request | later five-chunk macro request |
| --- | ---: | ---: | ---: | ---: |
| v3 | 180 chars | 300 chars | up to 1,380 chars | about 1,500 chars |
| turbo | 120 chars | 280 chars | up to 1,240 chars | about 1,400 chars |
| nano | 180 chars | 280 chars | up to 1,300 chars | about 1,400 chars |

Each macro request is sent directly to the already-resident Chatterbox server with `streaming=False`, which writes its WAV into the Trident run directory. Gradio receives that WAV path directly. The first WAV is held until the second WAV has completed; after that, the previous WAV is emitted while the next is already available. The final WAV is emitted after generation completes.

`HighlightedText` shows already-emitted text, the buffered-ahead region and pending text. Native TTS streaming has no source-text offset in the existing TCP protocol, so Trident marks the response active/completed instead of inventing approximate text/audio synchronization.

## `config.py` is the control source

Persistent live settings are serialized in `LIVE_SETTINGS_JSON` inside `config.py`. `Apply config` and `Start engine` write the current UI values back to that line atomically. The in-memory `LIVE_SETTINGS` dict is updated at the same time.

Static model/runtime/buffer definitions also remain in `config.py`:

- Windows hardware profile detection: Pascal or Intel Iris Xe
- model repositories, pinned revisions and filenames
- resident ports and startup limits
- Gemma context/generation/runtime values
- TTS family runtime, sample, voice, text-chunk and native stream parameters
- ASR sample rate
- Gradio microphone transport interval
- Silero frame size
- TTS fake-stream five-chunk grouping
- browser TTS lookahead duration
- conversation-history window

The UI writes only user-facing `LIVE_SETTINGS`; hardware/model recipes are code configuration, not mutable runtime controls.

## CLI parity

The Conversation tab is the full-duplex orchestration layer. The CLI parity and Runtime tabs keep the original command surface available without implementing alternate command semantics.

| CLI surface | Gradio counterpart |
| --- | --- |
| `install --family` | Runtime / Install |
| `asr INPUT [-o]` | CLI parity / ASR |
| `brain [INPUT|-t] [-o] --language --system-prompt/--system-prompt-file` | CLI parity / Brain |
| `tts [INPUT|-t] [-o] --family` plus all TTS overrides and streaming/join | CLI parity / TTS |
| `run INPUT [-o] --family --tts-language` plus prompts and all TTS overrides | CLI parity / Run |
| `resident status|warm|stop` plus TTS/family settings | Runtime / Resident |
| global `--models-dir`, `--data-dir` | inherited from the process that launches Gradio and forwarded to CLI subprocesses |

CLI parity operations invoke `main.py` through `subprocess`. Live Conversation uses only the existing native resident/C-API endpoints because routing microphone PCM or streamed model output through a file-oriented CLI subprocess would add latency and a duplicate transport protocol.

## Failure model

The main CLI no longer wraps command dispatch in a generic exception-to-message handler. Invalid state or native inference failures raise immediately.

The three conversation worker wrappers catch only at the asynchronous thread boundary. The exception is placed on the UI output queue and terminates that worker; it is not converted into a fallback behavior or silently ignored. Without this boundary, a Python thread could die while the Gradio pump continued waiting forever.

There is no fallback model, automatic model downgrade, silent retry loop, alternate cloud path or hidden protocol.

## Installation

Install or repair normally:

```powershell
python main.py install --family all --ui
```

The installer downloads both Parakeet roles, Gemma, selected Chatterbox models, native runtimes and reference voices. The UI virtual environment installs the pinned `requirements-ui.txt` packages.

Launch:

```powershell
python main.py --ui
```

The Conversation Start button stops the ordinary Nemotron HTTP ASR resident if it is running, then loads the EOU model in the direct C-API worker while leaving Gemma and Chatterbox resident. This avoids keeping two Parakeet models resident during the live conversation path.

## Verification

Model-independent syntax check:

```powershell
python -m py_compile asr_live.py config.py conversation.py installer.py local_api.py main.py media.py resident.py ui.py ui_streaming.py vad.py tests/test_asr_live.py tests/test_ui_streaming.py
```

Model-independent unit tests:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

On a supported Windows Pascal or Iris Xe machine after installation:

1. `python main.py resident warm --family v3 --tts-language en -r trump`
2. `python main.py resident status`
3. `python main.py --ui`
4. Start Conversation in continuous mode and confirm newly-finalized ASR text updates while speaking.
5. Enable only EOU and confirm native EOU dispatches a turn.
6. Enable only Silero VAD and confirm configured silence dispatches a turn.
7. Disable both and confirm the character threshold still dispatches.
8. Switch to PTT, use PTT ON / PTT SEND and confirm the final ASR tail is dispatched after recording stops.
9. Submit typed text and confirm it enters the same Gemma/TTS pipeline.
10. Edit the system prompt, Apply config, and confirm later turns use the changed behavior.
11. Test native TTS and buffered WAV TTS. In buffered mode confirm playback begins only after a second WAV is ready.
12. Keep speaking while Gemma/TTS are active and confirm ASR continues updating rather than waiting for the prior response.
13. Exercise every CLI parity command and resident action.

## Current upstream constraints

Research date: 2026-08-24.

- `parakeet.cpp` documents the EOU streaming C API for `parakeet_realtime_eou_120m-v1`; the normal Nemotron streaming model is retained separately for Trident's multilingual CLI ASR.
- `parakeet.cpp` ABI v5 separates EOU and EOB events and documents streamed text as newly finalized text.
- Gradio streaming inputs are intentionally time-limited queue jobs; the configured 86,400-second window is effectively day-long, and Gradio re-queues a media stream when its time limit expires.
- There is an open upstream `parakeet.cpp` issue, #63 dated 2026-08-16, reporting memory growth in `parakeet_capi_stream_feed` and stating that stream free/re-begin does not reclaim it. Trident does not conceal that upstream behavior with periodic model reloads because doing so would violate persistent-model semantics and would not address the reported leak.
- This source tree can be syntax/unit/UI-construction tested without the models. GPU residency, real microphone timing, Parakeet native inference and Chatterbox playback must be validated on the supported Windows GPU hardware because those native runtimes cannot execute in a Linux-only packaging environment.
