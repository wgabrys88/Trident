# Trident Technical Architecture

> **Rollout supersession — 2026-08-18:** The TTS implementation described below is retained as forensic history for the broken `runner-x` build. The rollout in this archive supersedes its TTS model/runtime contract. See `ROLLOUT-TTS-FIX.md`. The active TTS path now uses the attached MILESTONE-20 native patch set and its two locally converted ResembleAI GGUFs; `patches/chatterbox.patch`, the BricksDisplay three-file runtime, and the `builtin_fallback` conditioning contract are no longer active.


Document type: system specification  
Audience: human operator + next coding agent  
Source revision: `runner-x` @ `0eb4f4d` plus TTS runtime install that unloads before replace  
Schema: `SCHEMA.version = 5`  
Trace schema: `trident.event` v1  
Generated from: first-party source, live `third_party/chatterbox.cpp` after apply, `patches/chatterbox.patch`, pinned revisions, `data/config.json`, `data/models.json`, `tools/runtime/tts/build.json`, `trident.log.jsonl` runs `run-c8d39587d774477fa076544cf945751e` and `run-359ca74de66a4ec796775238c0740faf`, and clone-reliable `3e88ec2` / tag `MILESTONE-21`

## Evidence legend

| Tag | Meaning |
|---|---|
| SOURCE | Statement is the literal contents of a named file at this revision. |
| LOG | Statement is a field in `trident.log.jsonl` read this session. |
| COMMIT | Statement is recorded in a git commit body on this branch. Not re-measured this session. |
| UNTESTED-PRIOR | Behavioral inference from source or prior commits. Not executed this session. Minimal falsifier follows the claim. |
| CLONE | Statement is the applied result of `C:\Users\eb-wjt\Downloads\clone-reliable` @ `3e88ec2` (`chatgpt v3 and previous golden`). That tree is the last known-working TTS. |

`Chatterbox_V3_Diagnostic_Report.md` is a PyTorch-harness analysis. It is not a source of truth for this C++/GGUF/Vulkan stack.

Root `1.patch`, `2.patch`, `3.patch` are not referenced by `apply_chatterbox_patch()`. Live apply path is only `patches/chatterbox.patch`. SOURCE: `main.py:apply_chatterbox_patch`.

clone-reliable is an older checkout of this project. Its `patches/chatterbox.patch` is one cumulative file with **two** bake hunks on `chatterbox_engine.cpp`. The later hunk is the golden: it **removes** the CAMPPlus call and the empty-embedding throw. The earlier hunk that adds `throw ... CAMPPlus embedding failed` is not the working end state. Trident `31ba93d` copied the earlier hunk. This revision matches the later hunk. CLONE + SOURCE.

---

---

## 1. System Overview

### 1.1 Product

Trident is a single-host Windows 11 voice workspace. One Chrome tab talks to one Python controller. The controller owns install/build, process lifecycle, configuration, and request routing. Three child processes perform inference:

| Engine ID | Role | Process | Bind |
|---|---|---|---|
| P-ASR | Speech-to-text | `parakeet-server.exe` | `127.0.0.1:8097` |
| P-BRAIN | Chat completion | `llama-server.exe` | `127.0.0.1:8098` |
| P-TTS | Multilingual TTS + clone | `tts-server.exe` | `127.0.0.1:8095` |

Controller bind: `127.0.0.1:8765`. SOURCE: `main.py:main`. LOG: `controller.started` host `127.0.0.1` port `8765` api_version `5`.

Conversation path: microphone or WAV → Parakeet → active brain → Chatterbox Multilingual V3 → speakers. Speech Lab path: typed text → Chatterbox only. Install path: sequential prerequisite / component / model jobs.

### 1.2 Process set

| Process | Component IDs | Launch owner | Lifetime |
|---|---|---|---|
| Chrome | C-UI, C-AW | `webbrowser.open` 0.4 s after listen | User |
| `python main.py` | C-CTRL, C-LOG | Operator | Until KeyboardInterrupt; then `stop_engine` on every name in `PROCESSES` |
| `parakeet-server.exe` | P-ASR | `load_engine("asr")` | Until `unload_engine` / controller exit |
| `llama-server.exe` | P-BRAIN | `load_engine("brain")` | Until unload / brain swap reload |
| `tts-server.exe` | C-TTS-SRV, C-TTS-WRAP, P-TTS | `load_engine("tts")` | Until unload; also closes all TTS lanes |

### 1.3 Pins

| Kind | ID | Pin | Size / digest |
|---|---|---|---|
| Source | chatterbox.cpp | `https://github.com/gianni-cor/chatterbox.cpp` `ddca05fb69c2910b0d7b5eae420d360ed98c067b` | `SOURCES["chatterbox"]` |
| Source | ggml | `https://github.com/ggml-org/ggml.git` `58c3805840b516b2a88ff867ccf7bb41dba79951` | `SOURCES["ggml"]` |
| Binary | parakeet.cpp | `mudler/parakeet.cpp` tag `v0.5.0` asset `parakeet-v0.5.0-bin-win-vulkan-x64.zip` | `BINARIES["parakeet"]` |
| Binary | llama.cpp | `ggml-org/llama.cpp` tag `b10453` asset `llama-b10453-bin-win-vulkan-x64.zip` | `BINARIES["gemma"]` |
| Model | T3 | `BricksDisplay/Chatterbox-Multilingual-TTS-GGUF` rev `37277eeb9e26da8e3fba65b52727cb30b0bc5ae8` `chatterbox-mtl-t3-q4_0.gguf` | 283389248 / `9a5b5e86…205c30` |
| Model | S3Gen codec | same repo/rev `chatterbox-mtl-codec-f16.gguf` | 335027072 / `dce99659…64954a` |
| Model | S3T | same repo/rev `chatterbox-mtl-s3t.gguf` | 247487280 / `26592ce1…2e69c4` |
| Model | ASR | `mudler/parakeet-cpp-gguf` rev `bf0af9f4…d13e9` `tdt-0.6b-v3-q4_k.gguf` | 675200864 / `993d73fe…d5ee8` |
| Model | Brain default | `google/gemma-4-E2B-it-qat-q4_0-gguf` rev `675cff42…9b93` `gemma-4-E2B_q4_0-it.gguf` | 3349516256 / `fa401b55…c6634` |
| Model | Brain option | `unsloth/Qwen3.5-0.8B-GGUF` `Qwen3.5-0.8B-Q4_K_M.gguf` | 532517120 / `bd258782…f11a4` |
| Model | Brain option | `unsloth/Qwen3.5-4B-GGUF` `Qwen3.5-4B-Q4_K_M.gguf` | 2740937888 / `00fe7986…f11a4` |
| Asset | Default voice | `assets/default-reference.wav` → `data/default-reference.wav` | 1012558 / `de2579b2…171a9` |
| Tool | Vulkan SDK | `1.4.357.0` | `PACKAGES["vulkan"]` sha256 `81f47471…310d` |
| Tool | CMake | `4.4.2` zip | `PACKAGES["cmake"]` |
| Tool | MinGit | `2.54.0` | `PACKAGES["git"]` |
| Tool | MSVC | `vs_BuildTools.exe` workload `Microsoft.VisualStudio.Workload.VCTools` | `PACKAGES["msvc"]` |
| Fetch | cpp-httplib | `yhirose/cpp-httplib` `f00e476f1b2d519343e960f77f57a06c8a24f046` | `server/CMakeLists.txt` |
| Fetch | nlohmann_json | `nlohmann/json` `55f93686c01528224f448c19128836e7df245f72` | `server/CMakeLists.txt` |

`data/models.json` receipts match the five installed model SHA-256 values plus the default-voice SHA-256. SOURCE.

`tools/runtime/tts` after the 2026-08-18 11:05 panel `install_component tts` (LOG `run-359ca74d…`): Chatterbox build 239.8 s code 0, server-configure 3.2 s code 0, server-build 2.3 s code 0, then `job.failed` `[WinError 183] Cannot create a file when that file already exists: tools\runtime\tts`. Cause: `shutil.rmtree(..., ignore_errors=True)` left the directory because `tts-server.exe` pid 7740 still held it; `mkdir` then collided. `build.json` was absent after that failure. The new exe sat at `server/build/Release/tts-server.exe` 11:05:22 (1140224 bytes). The running binary stayed the 10:37:44 CAMPPlus-throw build (1207296 bytes). `tts` component ready means exe exists AND `build.json.build_id == tts_build_id()`. SOURCE: `main.py` verification predicates. LOG: `job.failed` seq 300.

### 1.4 Language sets

| Set | Codes | Use |
|---|---|---|
| `TTS_LANGUAGES` | pt | Speech Lab + native `VoiceConfig.language`. Single-language lock to Iracema. |
| `ASR_LANGUAGES` | bg hr cs da nl en et fi fr de el hu it lv lt mt pl pt ro sk sl es sv ru uk | Catalog only; Parakeet is not passed a language flag by `transcribe()` |
| `CONVERSATION_LANGUAGES` | pt | `conversation.language`, `brain()`, `run_turn()` |

Intersection is `pt`. Multilingual catalogs return after one-language T3 is verified.

### 1.5 Control surface

All browser and operator control except static files and TTS PCM uses `GET|POST /api` with field `op`. GET allows `inspect|schema|state|log|events`. POST accepts every key in `OPS`. WAV bodies use `Content-Type: audio/wav` and query `op` in `{turn, asr, upload_reference}`. SOURCE: `Handler.do_GET`, `Handler.do_POST`, `OPS`.

---

## 2. Architecture Diagram

### 2.1 Process topology

```
[Chrome C-UI / C-AW]
        |  HTTP 127.0.0.1:8765  GET / POST /api  GET /api?op=events (SSE)
        |  WS  127.0.0.1:8095/tts  (browser opens after C-CTRL returns init JSON)
        v
[C-CTRL main.py Handler + dispatch]
   |          |              |
   | HTTP     | HTTP         | spawn + stdout ingest
   | :8097    | :8098        v
   |          |         [P-TTS tts-server.exe]
   |          |              |  EngineWrapper
   |          |              v
   |          |         [chatterbox.cpp Engine + ggml-vulkan]
   v          v
[P-ASR]    [P-BRAIN]
parakeet   llama-server
Vulkan0    --device Vulkan0

[C-LOG log.py] <-- C-CTRL record/ingest
               <-- P-TTS stdout "TRIDENT_EVENT " + unstructured T3/S3Gen lines
               --> trident.log.jsonl  (20 MiB rotate to .1)
               --> SSE event:trace  (set_trace_listener)
```

### 2.2 Conversation turn (`op=turn`)

```
C-AW PcmCapture @ AudioContext 16000
        | Float32 frames
        v
C-UI onCapture / VAD  --> makeWav PCM16 LE mono @ context.sampleRate
        | POST /api?op=turn&language=&trace_id=&client_id=  body audio/wav
        v
C-CTRL run_turn
        | atomic_bytes data/last-input.wav
        | if clone_voice and duration>=10s: validate_wav -> data/reference.wav
        v
P-ASR POST http://127.0.0.1:8097/v1/audio/transcriptions  multipart speech.wav
        | JSON {text,...}
        v
P-BRAIN POST http://127.0.0.1:8098/v1/chat/completions  non-stream
        | choices[0].message.content  (or filtered reasoning_content)
        v
C-CTRL returns {ok,text,trace_id,turn_id,clone,cloned,results,reference}
        v
C-UI speak(..., source="turn")
        | POST /api {op:tts_session,lane:a,...} -> init JSON
        | WS /tts  send init  recv ready
        | POST /api {op:tts_request,lane:a,text} -> synthesize JSON
        | WS send synthesize
        v
P-TTS Engine.synthesize -> float32 24000
        | WS text "audio" + binary PCM16LE + "chunk_done"
        | write data/last-output.wav
        v
C-AW PcmRing @ AudioContext 24000 -> destination
        | C-UI reportTts + browserTrace
```

### 2.3 Speech Lab

Same TTS session/request/WS path. Source field is `speech_lab`. No ASR, no brain. Style from `speech.style`. Language from `speech.language`.

### 2.4 Install / build / load

```
C-UI installAll()  sequential, one job at a time
  prerequisites (skip python): git, cmake, msvc, vulkan
  components: tts (local CMake Vulkan build), parakeet (zip), gemma (zip)
  models: chatterbox-t3, chatterbox-codec, chatterbox-s3t, parakeet, gemma, reference
          + active catalog brain if not already in that list

tts install_component:
  checkout chatterbox @ ddca05f
  apply patches/chatterbox.patch  (placeholders __EM_DASH__ __SECTION_SIGN__ __BLANK_CONTEXT__)
  checkout ggml @ 58c3805
  cmake -DGGML_VULKAN=ON -DGGML_CUDA=OFF -DGGML_NATIVE=OFF
  build tts-cpp mtl_tokenizer
  cmake server -DCHATTERBOX_CPP_ROOT=...
  build tts-server
  stop_engine(tts)   # Windows cannot rmtree a running tts-server.exe
  atomic copy exe+dll+build.json -> tools/runtime/tts  (write .part, rmtree dest, rename)

load_engine:
  stop_engine(name)
  verify models (tts: all three GGUFs + SHA receipts; asr: parakeet; brain: active_brain_path)
  Popen + log_process thread + wait_ready(health URL, 600s)
```

### 2.5 Trace identifier propagation

```
browser client_id  = sessionStorage["trident.client_id"]   (uuid, durable per tab)
browser trace_id   = makeId("trace") per turn / synthesis
C-CTRL turn_id     = new_id("turn") if caller omitted
C-CTRL http_id     = new_id("http") per Handler GET/POST
C-CTRL job_id      = new_id("job") per start_job
C-CTRL config_id   = new_id("tts-config") per tts_session
C-CTRL request_id  = new_id("tts-request") per tts_request
lane               = "a" only

C-UI --JSON--> C-CTRL --PROCESS_TRACES["tts"]--> stdout ingest
C-UI --WS JSON--> P-TTS identifiers() merge onto every native_event and WS reply
P-TTS stdout "TRIDENT_EVENT {schema:trident.native-event,...}" --> log.ingest
C-UI op=trace event must match ^browser\.[a-z0-9_.-]{1,80}$
```

Identifiers accepted by `log.py`: `trace_id turn_id http_id job_id config_id session_id request_id lane client_id`. SOURCE.

---

## 3. Component Catalog

### 3.1 Inventory

| ID | Class | Process | Files | Inbound | Outbound | State owned | Failure as implemented |
|---|---|---|---|---|---|---|---|
| C-CTRL | controller | python | `main.py` | HTTP `/`, `/api` | child stdin none; HTTP to engines; spawn | `CONFIG`, `RECEIPTS`, `BRAIN_STATE`, `RUNTIME`, `PROCESSES` | `ApiError` JSON `{error}`; job `status=error`; engine `status=error` on unexpected exit |
| C-LOG | logger | in-process | `log.py`, `trident.log.jsonl` | `record`/`ingest` | file + optional listener | `RUN_ID`, `SEQUENCE`, `CONTEXT` | listener exceptions swallowed; bad JSONL lines skipped on read |
| C-UI | panel | Chrome | `panel.html`, `panel.css`, `panel.js` | SSE `state/job/trace/ping`; WS text+binary | `POST /api`; WS `/tts` | schema, state, recording, playback, diagnostic, `client_id` | `fail()` 9s banner; EventSource `onerror` → "Reconnecting" |
| C-AW | worklet | Chrome render/capture | `audio-processor.js` | Float32 PCM / `{type:clear}` | `played`/`drained` / capture frames | ring queue | silence on underrun (`out[i]=0`); process always returns true |
| C-TTS-SRV | HTTP/WS | tts-server | `server/src/server.cpp`, `server/include/server.hpp` | WS `/tts`, GET `/health` `/state`, POST `/cancel` | WS frames; stdout `TRIDENT_EVENT`; `data/last-output.wav` | `session`, `session_context` | WS `{type:error}`; HTTP 400/404 |
| C-TTS-WRAP | pimpl | tts-server | `server/src/engine_wrapper.cpp`, `server/include/engine_wrapper.hpp`, `server/src/main.cpp` | `VoiceConfig` | `tts_cpp::chatterbox::Engine` | `sessions` map, per-session mutex | `session limit reached`; `model file missing`; `sessions.at` throw |
| C-PATCH | patch | apply at install | `patches/chatterbox.patch` | `git apply --unidiff-zero` | modified chatterbox.cpp tree | none | `run()` raises if apply exits non-zero |
| C-CFG | durable config | files | `data/config.json`, `data/models.json`, `data/brains.json` | `load_*` / `atomic_json` | disk | field map + receipts + brain selection | missing file → defaults; invalid object → `RuntimeError` |
| C-REF | audio artifacts | files | `data/reference.wav`, `data/default-reference.wav`, `data/last-input.wav`, `data/last-output.wav`, `assets/default-reference.wav` | upload / clone / native capture | native `reference_audio`; diagnostics | `RUNTIME["reference_generation"]` | `409` missing/unverified default; `400` invalid WAV; native throw if path missing |
| P-ASR | binary | parakeet-server | `tools/runtime/parakeet/**/parakeet-server.exe` | `/v1/audio/transcriptions` | JSON transcript | none in Trident | `remote()` wraps HTTPError; `wait_ready` 600s |
| P-BRAIN | binary | llama-server | `tools/runtime/gemma/**/llama-server.exe` | `/v1/chat/completions` | OpenAI-shaped JSON | llama KV | same |
| P-TTS | engine | tts-server + chatterbox | `third_party/chatterbox.cpp` (pinned+patched), GGUFs in `models/` | `EngineOptions` | float32 PCM 24000; stderr metrics | T3 KV, S3Gen, baked voice | T3 stop reasons; `Engine:` runtime_error |

### 3.2 C-CTRL types and function clusters

| Symbol | Kind | Responsibility |
|---|---|---|
| `ApiError` | class | `code:int` + message; mapped to HTTP JSON |
| `Handler` | `BaseHTTPRequestHandler` | static files, GET/POST `/api`, SSE |
| `Server` | `ThreadingHTTPServer` | `daemon_threads = True`, bind `127.0.0.1:8765` |
| `field` / `FIELDS` / `PARAM_GROUPS` / `SCHEMA` / `OPS` | data | single catalog for UI + validation + inspect |
| `validate` / `set_config` / `load_config` | config | type/range/options; persist; migrate `tts.engine.context < 1280` → `1536` |
| `snapshot` / `emit` / `emit_state` / `set_flow` / `set_job` / `start_job` | runtime | locked copy of live state; SSE fan-out |
| `checkout` / `apply_chatterbox_patch` / `install_*` / `download_model` / `fetch` | install | pin-exact bytes |
| `load_engine` / `stop_engine` / `log_process` / `log_native_line` | engines | argv, health wait, stdout parse |
| `transcribe` / `brain` / `run_turn` | pipeline | sequential ASR → Brain; clone gate |
| `tts_session` / `tts_request` / `tts_event` / `tts_cancel` | TTS control | lane `a` only; does not open the WebSocket |
| `dispatch` | router | one `op` → one function |
| `identifier` | validator | `^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$` |

### 3.3 C-LOG types

| Symbol | Responsibility |
|---|---|
| `SCHEMA="trident.event"` `VERSION=1` | durable record type |
| `IDENTIFIERS` | nine correlation keys copied from context + kwargs |
| `record` | append one ASCII JSONL line; rotate at 20 MiB |
| `ingest` | wrap native `TRIDENT_EVENT` payload |
| `scope` | `contextvars` identifier stack |
| `read` | filter last N (1..5000) by identifier/source/component/level/event/`since_seq` |
| `clear` | truncate file to empty |
| `SECRET_KEYS` | values replaced with `[redacted]` |

Listener exceptions are swallowed. SOURCE: `log.py:record`.

### 3.4 C-UI / C-AW types

| Symbol | Responsibility |
|---|---|
| `api` / `command` | `fetch` JSON or WAV; throw on `!response.ok` |
| `browserTrace` | `op=trace`; swallows network failure (`.catch(() => null)`) |
| `installAll` / `installEntry` / `waitForJob` | sequential jobs |
| `startAll` / `stopAll` / `ensureEngine` | ASR then Brain then TTS load; reverse unload after `closeTts` |
| `openTts` / `speak` / `closeTts` / `cancelSpeech` | one socket, one lane, PCM decode |
| `startRecording` / `stopRecording` / `onCapture` / `submitUtterance` | 16 kHz capture + optional RMS VAD |
| `runTurn` | WAV POST turn then `speak` with `style=natural` |
| `render*` / `paintLogs` / `openEvents` | DOM from SSE + snapshot |
| `PcmCapture` | post every input frame to main thread |
| `PcmRing` | queue Float32; report every 2400 samples; `drained` when emptied |

### 3.5 C++ types

| Symbol | File | Responsibility |
|---|---|---|
| `VoiceConfig` | `engine_wrapper.hpp` | reference path, language, sampling, stream chunking |
| `EngineWrapper` | pimpl | session map, limit, one `Engine` per session |
| `EngineWrapper::Impl::Session` | `engine_wrapper.cpp` | `unique_ptr<Engine>` + `mutex synthesis` |
| `TTSServer` | `server.hpp/.cpp` | httplib listen, health/state/cancel, WS `/tts` |
| `tts_cpp::chatterbox::Engine` | `engine.h` | load T3+S3Gen, bake voice, `synthesize`, `cancel` |
| `tts_cpp::chatterbox::EngineOptions` | `engine.h` | GGUF paths, `n_ctx`, sampling, stream tokens |
| `tts_cpp::chatterbox::v3::*` | `v3_defaults.h` | library defaults including `n_ctx=512` |

`Engine::synthesize` on one instance is documented as not concurrent-safe. Wrapper serializes with `session->synthesis`. SOURCE: `engine.h` comment + `engine_wrapper.cpp:synthesize`.

### 3.6 P-TTS internal stages (patch-owned / pinned)

| Stage | Module | Input | Output |
|---|---|---|---|
| Reference WAV load | `chatterbox_engine.cpp` | path, ≥5 s (engine validate) | PCM, resampled 16 kHz, LUFS −27 |
| T3 speaker embedding | `voice_encoder.*` | 16 kHz wav + weights from codec GGUF | 256-d `speaker_emb` |
| Speech tokens | `s3tokenizer.*` via `find_s3t_gguf` | sibling `*s3t*.gguf` | S3Gen prompt tokens + T3 cond tokens |
| Prompt features | `compute_prompt_feat_native` | reference + codec GGUF | mel `(T,80)` |
| S3Gen speaker 192-d | CAMPPlus or builtin | BricksDisplay codec has no `campplus/*` tensors | bake leaves `s3gen_embedding` empty; S3Gen copies `s3gen/builtin/embedding`. |
| Text tokenize | `mtl_tokenizer` | text + `opts.language` | `tok.encode(text, language)` only. No `punc_norm`, no extra start/stop text tokens on the MTL Engine path. |
| Text split | `max_sentence_chars` | long string | N segments; log `auto-split:` |
| T3 decode | `t3_mtl.cpp` | cond + text + speech BOS | speech tokens; `speech_position = n_past` (clone-reliable / pin). Not `generated.size()`. |
| GPU step | `use_optimized_mtl_backend` = `!ggml_backend_is_cpu` | Vulkan and Metal | B=2 stacked QKV + `ggml_flash_attn_ext`. Sequential-softmax was tried in `dd13afe` and crashed. |
| S3Gen + HiFT | codec GGUF | speech tokens + prompt feat/token + 192-d speaker | float32 24 kHz chunks. Empty speaker → builtin Iracema embedding. |

### 3.7 Class / ownership graph

```
Server
  Handler (per request thread)
    dispatch
      run_turn | tts_session | load_engine | start_job | ...
        PROCESSES[name] : Popen
        RUNTIME.lanes["a"]
        C-LOG.record

TTSServer
  httplib::Server
  unique_ptr<EngineWrapper>
    map<id, Session>
      unique_ptr<tts_cpp::chatterbox::Engine>
        T3 ggml + S3Gen preload thread + baked tensors
```

---

## 4. Data Flow Analysis

### 4.1 Capture bytes

| Step | Format | Rate | Owner |
|---|---|---|---|
| `getUserMedia` | MediaStreamTrack | device | C-UI `startRecording` constraints: `channelCount:1`, echo/noise/AGC all `false` |
| `AudioContext({sampleRate:16000})` | AudioContext | requested 16000; actual is `context.sampleRate` | C-UI |
| `PcmCapture.process` | Float32Array copy | graph quantum | C-AW |
| VAD off | every frame appended | — | `onCapture` |
| VAD on | RMS ≥ `asr.vad.threshold` starts speech; silence ≥ `asr.vad.silence_ms` after `asr.vad.min_speech_ms` submits; pre-roll trimmed to 0.3 s | — | `onCapture` / `trimPreRoll` |
| `makeWav` | RIFF PCM16 LE mono | `context.sampleRate` | C-UI |
| `run_turn` | same bytes | stored `data/last-input.wav` | C-CTRL `atomic_bytes` |

File picker: `file.arrayBuffer()` posted as-is. Diagnostic decoder requires PCM16 WAV. SOURCE: `panel.js:decodeWav`.

### 4.2 ASR

`transcribe` builds multipart boundary `trident-{uuid}` with parts `file=speech.wav` (`audio/wav`) and `response_format=json`. POST `http://127.0.0.1:8097/v1/audio/transcriptions`. Response JSON stored in `RUNTIME["results"]["asr"]`. Empty `text` → `ApiError(422, "speech was not recognized")` from `run_turn`. No language query is sent. SOURCE.

### 4.3 Brain

System prompt (SOURCE, exact):

```
Reply in {language_name} ({language}). Give one or two short, natural spoken sentences. Do not analyze, list options, add a preamble, or mention transcription.
```

User content: `Respond naturally to this speech transcript:\n\n{transcript}` from `run_turn`, or caller `prompt` from `op=brain`.

POST `http://127.0.0.1:8098/v1/chat/completions` body:

```
model = active_brain_id()
messages = [system, user]
temperature, top_p, top_k, min_p, repeat_penalty, seed, max_tokens  from CONFIG brain.sample.*
stream = false
+ BRAIN_FAMILIES[family]   # gemma4/qwen35: reasoning_format=none, chat_template_kwargs.enable_thinking=false
                           # generic: {}
```

`brain_reply_text`: prefer `choices[0].message.content`; else last non-heading line of `reasoning_content` that does not start with skip tokens `(thinking, analyze, analysis, option, theme, constraint, input, role, task, draft, determine)`. Empty content + empty filter → `""`; `run_turn` then uses transcript as speak text.

### 4.4 TTS control vs media

C-CTRL never opens the WebSocket. It returns the init/synthesize JSON. C-UI opens `ws://127.0.0.1:8095/tts`.

Init fields (SOURCE `tts_session` + `TTSServer::connect`):

`type, reference_audio, language, config_id, trace_id, turn_id, lane, source, client_id, seed, max_tokens, top_k, top_p, min_p, temperature, repeat_penalty, cfg_weight, exaggeration, cfm_steps, stream_first_chunk_tokens, stream_chunk_tokens, max_sentence_chars`

Native requires every listed numeric field except `max_sentence_chars` (default 180). `request_id` required on `synthesize`. SOURCE: `server.cpp`.

Media: each chunk is one JSON `audio` frame then one binary frame of PCM16LE mono 24000. C-UI `binaryType=arraybuffer`; `getInt16(i*2,true)/32768` → `PcmRing`.

Native also writes `parent(reference)/last-output.wav` (`data/last-output.wav` when reference lives in `data/`). SOURCE: `server.cpp` `capture_audio`.

### 4.5 Reference / clone

| Path | Writer | Reader | Rule |
|---|---|---|---|
| `assets/default-reference.wav` | repo | `download_model("reference")` copy | SHA+size must match pin |
| `data/default-reference.wav` | download_model | `reference_path` if no custom | receipt SHA |
| `data/reference.wav` | `validate_wav` (upload or clone) | `reference_path` if file exists | mono PCM16 uncompressed, ≥5 s |
| `data/last-input.wav` | every `run_turn` | diagnostics / clone source | no format re-encode |

Clone: `conversation.clone_voice` and `wav_metrics.seconds >= 10` → `validate_wav(audio)` replaces `data/reference.wav` and increments `reference_generation`. Shorter recordings keep the previous reference and emit `turn.clone.skipped`. SOURCE.

After clone, C-UI `closeTts()` so the next session rereads the new file. SOURCE: `panel.js:runTurn`.

Conditioning contract: C-CTRL `tts.session.configured.conditioning_contract` is an advertisement. Native `Engine: voice conditioning ...` is the measured bake.

| Tensor | Origin when reference present | Advertised at `31ba93d` | What actually happens |
|---|---|---|---|
| T3 speaker 256-d | VoiceEncoder on reference @16 kHz | `reference_voice_encoder` | succeeds. LOG + SOURCE |
| T3 cond speech tokens | S3TokenizerV2 on sibling `*s3t*.gguf` | `reference_s3tokenizer` | 150 cond tokens. LOG |
| S3Gen prompt tokens | same S3TokenizerV2 | `reference_s3tokenizer` | 250 prompt tokens. LOG |
| S3Gen prompt feat | `compute_prompt_feat_native` @24 kHz after resample+trim | `reference_audio` | `(500,80)` when bake reaches it. LOG prior turn |
| S3Gen speaker 192-d | empty → GGUF `s3gen/builtin/embedding` | `builtin_fallback` | bake does not call CAMPPlus and does not throw if empty. S3Gen per-tensor builtin fill. |

SOURCE: `main.py:tts_session` (advertises `builtin_fallback`), `chatterbox_engine.cpp:bake_voice_conditioning` (no `compute_embedding_native` block, no empty-embedding throw), `chatterbox_tts.cpp` (per-tensor builtin fill). CLONE later bake hunk. Prior LOG seq 1027–1032 of `run-c8d39587…` is the `31ba93d` throw, not this source.

`wav_load` already downmixes stereo→mono. Mono/stereo is not a clone defect. SOURCE: `voice_features.cpp`.

S3T GGUF is not a tts-server argv. `find_s3t_gguf(s3gen_path)` scans the codec's parent directory for `*.gguf` whose filename contains `s3t`. Trident places all three files in `models/`. SOURCE.

### 4.6 Event stream

| Producer | Wire | Consumer |
|---|---|---|
| `log.record` | JSONL + listener | file; SSE `event:trace` |
| P-TTS `native_event` | stdout `TRIDENT_EVENT {json}` schema `trident.native-event` | `log_native_line` → `ingest` |
| P-TTS / P-ASR / P-BRAIN unstructured | stdout lines | regex extract T3/S3Gen/bench or `ENGINE_LOG_TOKENS` |
| C-UI | `op=trace` | `browser_trace` |
| C-CTRL jobs/state | in-process `emit` | SSE `job` / `state` / 15 s `ping` |

LOG this session (`run-c8d39587…`, 1042 events): controller started; panel rebuilt TTS (`install_component`, build 217 s + server 2.4 s); ASR/Brain/TTS all `engine.ready`; one conversation turn (see §4.5 and Appendix B).

### 4.7 Static HTTP

| Path | File | Type |
|---|---|---|
| `/` `/panel.html` | `panel.html` | `text/html; charset=utf-8` |
| `/panel.css` | `panel.css` | `text/css; charset=utf-8` |
| `/panel.js` | `panel.js` | `text/javascript; charset=utf-8` |
| `/audio-processor.js` | `audio-processor.js` | `text/javascript; charset=utf-8` |

No cache (`Cache-Control: no-store`). Any other path → 404. SOURCE: `Handler.do_GET`.

---

## 5. State Management Strategy

### 5.1 Durable

| Store | Writer | Reader | Atomicity |
|---|---|---|---|
| `data/config.json` | `set_config`, `load_config` migrate | `CONFIG` process dict | `atomic_json` (`.part` + `os.replace`) |
| `data/models.json` | `download_model` | `model_status` receipts | `atomic_json` |
| `data/brains.json` | `save_brains` | `load_brains` | `atomic_json`; created on first custom/apply |
| WAV files | `atomic_bytes` / `validate_wav` / native ofstream | engines, diagnostics | `.part` replace for Python writers |
| `tools/runtime/tts/build.json` | `install_component("tts")` | `component_status("tts")` | `atomic_json` |
| `trident.log.jsonl` | `log.record` | `log.read`, panel | append + rotate to `.jsonl.1` at 20 MiB |
| `sessionStorage["trident.client_id"]` | first panel load | all traces | browser |

`data/brains.json` is absent on this tree. Default in memory: `active=gemma`, empty custom. SOURCE: `default_brains`; file missing this session.

### 5.2 Process (`RUNTIME`)

```
jobs[kind:name] = {status, stage, progress, message, error, job_id}
engines[tts|asr|brain] = {status, error, pid, applied, message}
lanes.a = {status, session, request, config_id, trace_id, turn_id, source, language, style, reference, samples, chunks, error}
results = {asr, brain, turn}
flow = {stage, transcript, answer, error, language, started, trace_id, turn_id}
trace = {run_id, latest, latest_turn}
reference_generation : int
```

Statuses:

| Object | Values written in source |
|---|---|
| engine | `stopped`, `stopping`, `loading`, `running`, `error` |
| job | `running`, `done`, `error` |
| lane | `closed`, `connecting`, `ready`, `queued`, `streaming`, `cancelled`, `error` |
| flow.stage | `idle`, `listening` (also set locally in C-UI), `transcribing`, `thinking`, `ready_to_speak`, `error`; C-UI also uses `speaking`, `complete` locally |

`LOCK` (`threading.RLock`) guards `RUNTIME`, `PROCESSES`, `PROCESS_TRACES`, `CONFIG` updates, `RECEIPTS`, `BRAIN_STATE`, `SUBSCRIBERS`. SOURCE.

Single TTS lane key `"a"`. Unknown lane → `ApiError(400)`.

### 5.3 Browser

| Variable | Role |
|---|---|
| `schema` | from `GET /api` inspect |
| `state` | last snapshot / SSE state |
| `recording` | stream + 16 kHz graph + VAD accumulators + `busy` |
| `playback*` / `playbackNode` | 24 kHz graph + counters |
| `ttsSocket` / `ttsSession` / `ttsConfigId` / `ttsLanguage` / `ttsStyle` / `ttsReferenceGeneration` | session reuse predicate |
| `diagnostic` | last input/output PCM for canvas |
| `clientStage` | UI stage overlay |
| `installingAll` | mutex for sequential install |

Session reuse: same socket OPEN, same session id, same language, same style, same `reference_generation`. Else `closeTts` + new `tts_session`. SOURCE: `openTts`. Changing any `tts.sample.*`, `tts.stream.*`, or `tts.style.*` also `closeTts`. SOURCE: `save`.

### 5.4 Native

| Object | Lifetime |
|---|---|
| `EngineWrapper` models paths + limits | process |
| `Session` / `Engine` | WS `init` → `close`/disconnect/`destroy_session` |
| baked voice tensors | `Engine` construction |
| `cancel_flag` | `cancel()` any thread; checked in T3 loop |
| `std::async` synthesis | one in-flight per socket; `finish()` waits before next synthesize/init |

`max-sessions` from `--max-sessions` (CONFIG `tts.engine.sessions`, default 1). Extra `init` → `session limit reached`.

### 5.5 Config fields and apply timing

| Path | Type | Default | Min | Max / options | Apply |
|---|---|---|---|---|---|
| `conversation.language` | string | `en` | — | `CONVERSATION_LANGUAGES` | next turn / brain call |
| `conversation.clone_voice` | bool | `false` (live file currently `true`) | — | — | next turn |
| `conversation.vad` | bool | `false` | — | — | next captured frame |
| `speech.language` | string | `pt` | — | `TTS_LANGUAGES` (`pt`) | next Speech Lab session |
| `speech.style` | string | `natural` | — | `natural\|expressive` | next Speech Lab session |
| `speech.text` | string | `This is a multilingual voice synthesis test.` | — | multiline | next Speak text |
| `tts.engine.gpu_layers` | int | 99 | 0 | 999 | TTS process restart |
| `tts.engine.context` | int | 1536 | 1280 | 8192 | TTS process restart; stored values `<1280` rewritten to 1536 |
| `tts.engine.sessions` | int | 1 | 1 | 8 | TTS process restart |
| `tts.engine.threads` | int | 4 | 1 | 64 | TTS process restart |
| `tts.sample.seed` | int | 42 | 0 | 2147483647 | next WS init |
| `tts.sample.max_tokens` | int | 1000 | 16 | 4096 | next WS init |
| `tts.sample.top_k` | int | 0 | 0 | 200 | next WS init |
| `tts.sample.top_p` | float | 1.0 | 0.0 | 1.0 | next WS init |
| `tts.sample.min_p` | float | 0.05 | 0.0 | 1.0 | next WS init |
| `tts.sample.temperature` | float | 0.8 | 0.01 | 5.0 | next WS init |
| `tts.sample.repeat_penalty` | float | 1.2 | 0.5 | 2.0 | next WS init |
| `tts.sample.cfm_steps` | int | 5 | 1 | 50 | next WS init |
| `tts.stream.first_chunk` | int | 75 | 8 | 1000 | next WS init |
| `tts.stream.chunk` | int | 150 | 8 | 1000 | next WS init |
| `tts.stream.max_sentence_chars` | int | 180 | 16 | 2000 | next WS init |
| `tts.style.natural.cfg_weight` | float | 0.5 | 0.0 | 2.0 | next WS init (conversation uses natural) |
| `tts.style.natural.exaggeration` | float | 0.5 | 0.0 | 2.0 | next WS init |
| `tts.style.expressive.cfg_weight` | float | 0.3 | 0.0 | 2.0 | Speech Lab expressive |
| `tts.style.expressive.exaggeration` | float | 0.7 | 0.0 | 2.0 | Speech Lab expressive |
| `tts.style.cross-language.cfg_weight` | float | 0.0 | 0.0 | 2.0 | Speech Lab less-accent |
| `tts.style.cross-language.exaggeration` | float | 0.5 | 0.0 | 2.0 | Speech Lab less-accent |
| `asr.threads` | int | 4 | 1 | 64 | ASR process restart |
| `asr.vad.threshold` | float | 0.02 | 0.001 | 0.5 | next frame |
| `asr.vad.silence_ms` | int | 700 | 200 | 3000 | next frame |
| `asr.vad.min_speech_ms` | int | 400 | 100 | 5000 | next frame |
| `brain.engine.context` | int | 2048 | 256 | 32768 | Brain process restart |
| `brain.engine.parallel` | int | 1 | 1 | 1 | hard 1 in `load_engine` argv |
| `brain.engine.fit_target` | int | 3072 | 2048 | 4096 | Brain process restart |
| `brain.sample.temperature` | float | 0.2 | 0.0 | 2.0 | next completion |
| `brain.sample.top_p` | float | 0.9 | 0.0 | 1.0 | next completion |
| `brain.sample.top_k` | int | 40 | 0 | 200 | next completion |
| `brain.sample.min_p` | float | 0.0 | 0.0 | 1.0 | next completion |
| `brain.sample.repeat_penalty` | float | 1.05 | 0.5 | 2.0 | next completion |
| `brain.sample.seed` | int | 42 | 0 | 2147483647 | next completion |
| `brain.sample.max_tokens` | int | 160 | 8 | 2048 | next completion |

Live `data/config.json` matches these keys; `conversation.clone_voice` is `true`. SOURCE.

`brain.engine.parallel` UI max is 1. `load_engine` always passes `--parallel 1` regardless of stored value.

### 5.6 Verification predicates

| Object | ready |
|---|---|
| model | file size == pin size AND `RECEIPTS[name]==sha256` |
| tts component | `tts-server.exe` exists AND `build.json.build_id == tts_build_id()` else `unverified` |
| parakeet/gemma component | exe exists under `tools/runtime/{name}` (exactly one match by name) |
| reference | mono PCM16 uncompressed, duration ≥ 5 s |
| custom brain | file exists, size and sha256 match `BRAIN_STATE.custom` |
| conversation start (UI) | components+models ready for asr/brain/tts including `reference` |

`tts_build_id` = SHA-256 of chatterbox rev + ggml rev + bytes of `patches/chatterbox.patch`, `server/CMakeLists.txt`, every `server/include/*.hpp`, every `server/src/*.cpp`. SOURCE.

---

## 6. Model Interaction Patterns

### 6.1 ASR — Parakeet TDT 0.6B v3 Q4_K

| Item | Implemented value |
|---|---|
| Binary | `component_artifact("parakeet")` → `parakeet-server.exe` |
| Argv | `--model {tdt-0.6b-v3-q4_k.gguf} --host 127.0.0.1 --port 8097 --threads {asr.threads}` |
| Env | `PARAKEET_DEVICE=Vulkan0` |
| Health | `GET http://127.0.0.1:8097/health` |
| Infer | multipart WAV, `response_format=json` |
| Language | not sent |
| VAD | not in this process; RMS in C-UI |

UNTESTED-PRIOR: Parakeet JSON field set beyond `text`. Falsifier: one `op=asr` and print keys.

### 6.2 Brain — llama.cpp Vulkan b10453

| Item | Implemented value |
|---|---|
| Binary | always `component_artifact("gemma")` i.e. llama-server from the gemma runtime dir, even for Qwen/custom GGUF |
| Argv | `-m {active_brain_path} --host 127.0.0.1 --port 8098 --device Vulkan0 --n-gpu-layers all --ctx-size {brain.engine.context} --parallel 1 --no-mmproj --load-mode auto --flash-attn on --repack --fit on --fit-target {brain.engine.fit_target} --fit-ctx 2048` |
| Families | `gemma4`, `qwen35`, `generic` as in §4.3 |
| Catalog | `gemma`, `qwen35-0.8b`, `qwen35-4b`, `custom` → `models/custom-brain.gguf` |
| Prompt | spoken-sentence system + transcript user |
| Sampling | `BRAIN_GENERATION` defaults; live from CONFIG |
| Output use | first spoken sentence(s) only; max_tokens default 160 |

Custom URL must be `http(s)://...gguf` or `owner/repo/file.gguf` (resolved to Hugging Face `resolve/main`). Downloaded file first 4 bytes must be `GGUF`. SOURCE: `resolve_brain_url`, `install_custom_brain`.

### 6.3 TTS — Chatterbox Multilingual V3

| Item | Implemented value |
|---|---|
| Binary | `tools/runtime/tts/tts-server.exe` + ggml `*.dll` |
| Argv | `--port 8095 --model {t3} --s3gen-gguf {codec} --n-gpu-layers {gpu_layers} --context {context} --max-sessions {sessions} --threads {threads}` |
| Header default `v3::n_ctx` | 512 |
| Trident launch context | 1536 (min allowed 1280) |
| Effective hparam | `min(max_text_tokens+max_speech_tokens+4, requested_ctx)` in `gguf_split_mtl.cpp` |
| GPU | `GGML_USE_VULKAN`; `n_gpu_layers>0` moves the model to first GPU backend |
| S3T | directory scan next to codec |
| Sampling | `VOICE_DEFAULTS` + style overlay cfg/exaggeration |
| Stream | first 75 tokens, then 150; CFM steps 5; 24 kHz |
| Split | `max_sentence_chars=180` |
| Languages | 23 codes in T3 MTL table (same as `TTS_LANGUAGES`) |

#### 6.3.1 GPU generation path (live)

`use_optimized_mtl_backend(backend)` = `!ggml_backend_is_cpu(backend)`. SOURCE: live `t3_mtl.cpp:48`, `gguf_split_mtl.cpp:34`. Vulkan therefore takes B=2 stacked QKV and `ggml_flash_attn_ext`.

`dd13afe` inverted that on Vulkan (sequential CFG + `ggml_soft_max_ext`). Measured crash: `GGML_ASSERT(ggml_can_mul_mat)` because eager `ggml_mul_mat(Vfull, kq)` had `V.ne[0]=64` vs `kq.ne[0]=L`. COMMIT `dd13afe` + prior session. `31ba93d` restored flash_attn. Do not re-introduce the softmax path.

LOG this load: `TTS Vulkan ws://127.0.0.1:8095/tts`. No `Vulkan correctness path` line (that string is gone).

#### 6.3.2 Speech position

Live Engine MTL loop: `const int speech_position = n_past`. SOURCE: `chatterbox_engine.cpp:425`. Bounds still `1 <= speech_position < max_speech_tokens` inside `eval_step_mtl`.

`12ddb94` / `2d2807d` / `dd13afe` set `speech_position = generated.size()` (first token at 1). That is not the clone-reliable generate path and is not live.

Stop log:

`T3 stop reason={reason} prompt={n} n_past={n} speech_position={n} generated={n} [final_token=]`

Reasons parsed by C-CTRL: `cancellation`, `step_error`, plus whatever string the engine prints for EOS / repetition / context / max. `context_limit|max_tokens|repetition_guard|step_error` → warn. SOURCE: `T3_STOP_RE`, `log_native_line`.

Speech-lab turn before CAMPPlus throw (trace `f85bcfb0`, COMMIT `31ba93d` body): prompt=84, first token=7838, stop=`repetition_guard`, generated=89, never EOS. Token 7838 is outside S3 codebook 6561 (T3 speech vocab 8194, EOS 6562). That is a T3 distribution failure, not a missing 192-d speaker.

#### 6.3.3 Prompt / text boundaries

Live MTL Engine path: `text_tokens = tok.encode(text, opts.language)`. SOURCE: `chatterbox_engine.cpp` `run_t3`. `mtl_tokenizer::encode` prepends `[lang]`. No `punc_norm`. CLI/Python also pad `start_text_token`+`stop_text_token`; live Engine pad of those two tokens was measured this session to move T3 first token from in-codebook 5075 to off-codebook 7840–7844 and is not used.

### 6.4 Hyperparameter strategy as implemented

| Constraint | Setting | Source |
|---|---|---|
| Small E2B / 0.8B–4B brains on one iGPU with TTS+ASR | `max_tokens=160`, temp `0.2`, `parallel=1`, `fit_target=3072`, `fit-ctx=2048` | `BRAIN_*`, `load_engine` |
| Spoken reply not analysis | system prompt + `enable_thinking=false` | `brain()`, `BRAIN_FAMILIES` |
| T3 1000 predict vs old 512 ctx | migrate ctx to 1536, min 1280 | `load_config`, `967f95c` |
| Intel Vulkan + flash_attn | B=2 + `ggml_flash_attn_ext` (clone-reliable). Sequential softmax crashed. | `31ba93d`; do not restore `dd13afe` |
| T3 speech ids | Sampler keeps `[0, EOS)` plus EOS only. S3Gen cannot decode start_speech or 6563+ | `sample_next_token_mtl` |
| Single spoken language | Portuguese only; Iracema reference | `TTS_LANGUAGES`, `VOICE_STYLES` |
| First audio latency vs RTF | `first_chunk=75`, `chunk=150` | `VOICE_DEFAULTS` |

---

## 7. Constraint-Driven Design Decisions

| ID | Decision | Constraint | Evidence | Consequence |
|---|---|---|---|---|
| D1 | Single `/api` + `op` | One Chrome client, one catalog, no extra routes | `6233f74`; `SCHEMA.control` | GET subset; POST all ops; WAV via query `op` |
| D2 | Chrome-only AudioWorklet | User stack: latest Chrome; no fallbacks in source | `panel.js` `AudioWorkletNode`; no `ScriptProcessor` | Other browsers have no path |
| D3 | Sequential `installAll` | MSVC/CMake/Vulkan/source builds share disk and iGPU | `e532305`; `installAll` for-loop | One `jobs[key]` running; 409 if same key retried |
| D4 | TTS built locally; ASR/Brain are release zips | Chatterbox needs Trident patch; llama/parakeet are consumed as Vulkan binaries | `install_component` | Only TTS `build_id` tracks patch+server sources |
| D5 | Depth-1 pin checkout + hard reset + clean | Reproducible tree before patch | `checkout` | Untracked third_party edits are destroyed on next install |
| D6 | Cumulative ASCII patch + placeholders | Patch must stay 7-bit for `encoding=ascii` read then utf-8 apply | `apply_chatterbox_patch` | `__EM_DASH__` `__SECTION_SIGN__` `__BLANK_CONTEXT__` |
| D7 | `tts.engine.context` min 1280 default 1536 | Session may request 1000 speech tokens after 100–250 prompt; 512 exhausted without EOS | `967f95c`; `load_config` comment+migrate | Header `v3::n_ctx=512` is not the live Trident value |
| D8 | Vulkan uses the optimized MTL backend | clone-reliable + pin: `use_optimized_mtl_backend = !cpu`. Sequential softmax (`dd13afe`) crashed `ggml_can_mul_mat` on this Iris Xe | live `t3_mtl.cpp`; LOG crash in prior session | B=2 + `ggml_flash_attn_ext` on Vulkan. Softmax path is forbidden. |
| D9 | Patch brace consumes outer close | Zero-context hunk previously inserted extra `}`; MSVC C2143 | `236a80d` | Historical compile fix. Softmax hunk itself is no longer live. |
| D10 | `speech_position = n_past` | clone-reliable / pin RoPE alignment. Independent `generated.size()` was a later Trident experiment | live `chatterbox_engine.cpp:425`; CLONE | First eval uses `n_past == prompt_len`, not 1. |
| D11 | Trace IDs on every hop | Flat logs could not attribute duplicate audio | `e9695a5` | `trident.event` + native prefix + panel filter |
| D12 | Clone copies last input; language unlocked | Clip language ≠ spoken language | `cd40457`; UI copy in `renderReference` | Identity-only reference |
| D13 | Swappable brains + family | One llama-server, multiple GGUF chat templates | `cd40457`; `BRAIN_FAMILIES` | Same argv; extra JSON kwargs |
| D14 | BricksDisplay codec has no CAMPPlus. That is accepted. | `campplus_load` fails on `chatterbox-mtl-codec-f16.gguf`. clone-reliable comment: "the 192-d s3gen embedding is always the built-in one even when cloning." | SOURCE `main.cpp:147`; `chatterbox_tts.cpp:2073`; CLONE; LOG seq 1027 | S3Gen speaker is builtin Iracema. T3 speaker + prompt tokens/feat stay from the reference. Do not re-export the GGUF. |
| D23 | Missing CAMPPlus is not fatal | clone-reliable later bake hunk deletes the CAMPPlus call and the empty-embedding throw. `31ba93d` copied the earlier throw hunk. This revision deletes that throw. | CLONE later hunk; SOURCE `bake_voice_conditioning` | `s3gen_embedding` stays empty; S3Gen copies builtin. Advertised contract `builtin_fallback`. |
| D15 | Brain `parallel=1`, `fit on` | Shared Vulkan device with TTS+ASR | `load_engine` | One slot; llama shrinks layers to `fit_target` MiB |
| D16 | C-CTRL does not proxy PCM | Large 24 kHz stream | C-UI WS direct to :8095 | Controller crash does not copy audio; browser must reach 8095 |
| D17 | Lane `a` only | `tts.engine.sessions` default 1; one tab | `RUNTIME["lanes"]` | No second concurrent voice |
| D18 | Conversation style forced `natural` | Clone/conversation must not pick Speech Lab style | `panel.js:runTurn` `speak(..., "natural", "turn")` | `cross-language` unused on turns |
| D19 | Python not installable from panel | Controller already running under Python 3.11 | `install_prerequisite("python")` raises; UI disable | Host prerequisite |
| D20 | SHA-256 + size receipts | Hugging Face/GitHub bytes must match pins | `fetch`, `model_status` | `unverified` if size matches but receipt missing |
| D21 | `atomic_json` / `atomic_bytes` | Windows replace of live config | `os.replace` | Readers see old or new file, not partial |
| D22 | S3T sidecar by filename | EngineOptions has no s3t path | `find_s3t_gguf` | All three GGUFs must share a directory; Trident still requires S3T receipt before load |
| D24 | C-CTRL never opens the TTS WebSocket | PCM is 24 kHz; browser already has the socket | `tts_session` returns init JSON; `panel.js:openTts` connects `ws://127.0.0.1:8095/tts` | Do not invent a Python/PowerShell WS client. |
| D25 | Do not start `main.py` from the agent Job Object | Agent-launched controller then `load_engine` exited `3221225794` (`STATUS_DLL_INIT_FAILED`) with empty `last_message`. Same exe `--help` works from the runtime dir. Duplicate listeners (pids 3932 and 9384) followed an in-agent restart. | prior session; this session Start-Process detach succeeded and all three engines loaded | Operator or detached `Start-Process` only. |

### 7.1 Decision: do not put CAMPPlus in the GGUF

Confidence: 100. This is not a product preference. It is what the working tree already does.

| Option | Reject / accept | Why |
|---|---|---|
| Re-run `scripts/convert-s3gen-to-gguf.py` and replace `chatterbox-mtl-codec-f16.gguf` | **Reject** | Converter needs official PyTorch `speaker_encoder` weights. That is a new model pin, new SHA, new receipt. Pins stay `BricksDisplay` rev `37277eeb`. User rule: do not reinstall models. clone-reliable never did this and TTS worked. |
| Treat missing CAMPPlus as fatal (`31ba93d` throw) | **Reject** | Measured: bake dies, `last-output.wav` not rewritten, panel spinner until 30 s timeout. clone-reliable's **later** hunk removes this throw. The throw is an intermediate mis-copy. |
| Leave `s3gen_embedding` empty; S3Gen fills `s3gen/builtin/embedding` | **Accept** | Live `chatterbox_tts.cpp` already does per-tensor builtin fill and documents the BricksDisplay gap. clone-reliable bake stops before CAMPPlus so that fill runs. T3 language is VoiceEncoder + S3Tokenizer + `[lang]` + T3 tokens, not the 192-d S3Gen speaker. |

What CAMPPlus is and is not:

- CAMPPlus is the 192-d **S3Gen speaker identity** (timbre / clone color). SOURCE: `convert-s3gen-to-gguf.py` comment at the `campplus/` write; `campplus.embedding_size=192`.
- Language of the utterance is **T3** (`tok.encode` language tag + generated speech tokens). S3 codebook is 6561; T3 speech vocab is 8194; EOS is 6562.
- Mixing a custom English `prompt_feat`/`prompt_token` with builtin Portuguese Iracema speaker can color the vocoder. It cannot be the whole "language is Chinese / Na forma" story when T3 also emitted token 7838 and stopped on `repetition_guard`.
- Default reference `data/default-reference.wav` **is** the official Iracema demo (Parakeet: Portuguese Iracema excerpt). Builtin speaker matches that file. Custom `data/reference.wav` does not.

Implemented in this source:

1. `bake_voice_conditioning` has no `compute_embedding_native` block and no `s3gen_embedding.empty()` throw.
2. VoiceEncoder, S3Tokenizer, and prompt_feat remain required.
3. `tts_session` `conditioning_contract.s3gen_speaker_embedding` is `builtin_fallback`.
4. Native summary line still uses `s3gen_embedding.empty() ? "builtin_fallback" : "reference_campplus"`.
5. T3 flash_attn, `speech_position = n_past`, and `tok.encode(text)` are unchanged.

---

## 8. API Contract Specifications

### 8.1 HTTP — C-CTRL

Base: `http://127.0.0.1:8765`

| Method | Path | Body | Success | Error |
|---|---|---|---|---|
| GET | `/` `/panel.html` `/panel.css` `/panel.js` `/audio-processor.js` | — | 200 bytes | 404 unknown |
| GET | `/api?op=inspect` (default) | — | inspect object | ApiError |
| GET | `/api?op=schema\|state\|log` | query filters for log | object | ApiError |
| GET | `/api?op=events` | — | SSE stream | disconnect |
| POST | `/api` | JSON `{op,...}` | JSON + 200 or 202 | `{error}` 400/404/409/422/500 |
| POST | `/api?op=turn\|asr\|upload_reference&...` | `audio/wav` raw | JSON 200 | `{error}` |

Identifier fields must match `IDENTIFIER_RE` or 400.

`client_gone`: `BrokenPipeError`, `ConnectionResetError`, `ConnectionAbortedError`, or `winerror in (10053, 10054)` → silent return.

SSE events: `state`, `job`, `trace`, `ping` (`data:{}` every 15 s empty-queue). Initial event is current `snapshot()`.

### 8.2 `OPS`

| op | Fields | Body | Status | Result |
|---|---|---|---|---|
| `inspect` | — | — | 200 | `{ok, version:5, control:"/api", ops, schema, state}` |
| `schema` | — | — | 200 | `SCHEMA` |
| `state` | — | — | 200 | `snapshot()` |
| `log` | `limit, since_seq, trace_id, turn_id, config_id, session_id, request_id, source, component, level, event` | — | 200 | `{ok, run_id, lines}` |
| `clear_log` | — | — | 200 | `{ok, lines:[]}` + SSE log empty |
| `note` | `component, msg\|message, data` | — | 200 | `{ok, lines}` |
| `trace` | `event, level, ids..., data` | — | 200 | `{ok, event: entry}` |
| `set` | `values` object | — | 200 | `{ok, state}` |
| `install_prerequisite` | `name` in SCHEMA.prerequisites | — | 202 | `{ok, accepted, op, name, job_id}` |
| `install_component` | `name` in `{tts, parakeet, gemma}` | — | 202 | same |
| `download_model` | `name` in `MODELS` | — | 202 | same |
| `set_brain` | `name, url?, family?` | — | 200 or 202 | snapshot or job |
| `load_engine` | `name` in `{tts,asr,brain}` | — | 202 | job |
| `unload_engine` | `name` | — | 202 | job (`stop_engine`) |
| `upload_reference` | — | WAV | 200 | `{ok, reference}` |
| `asr` | ids | WAV | 200 | `{ok, result}` |
| `brain` | `prompt, language` + ids | — | 200 | `{ok, result}` |
| `tts_session` | `lane, language, style` + ids | — | 200 | `{url, message:init, language, style, config_id, ids}` |
| `tts_request` | `lane, text` + ids | — | 200 | `{message:synthesize, ids}` |
| `tts_event` | `lane, event` + ids + samples/chunks | — | 200 | `{ok}` |
| `tts_cancel` | `session_id` | — | 200 | native `{cancelled, session_id}` |
| `turn` | `language` + ids | WAV | 200 | `{ok, clone, cloned, language, text, trace_id, turn_id, client_id, results, reference}` or raise |

`tts_event.event` ∈ `ready, synthesize_started, audio_received, chunk_done, playback_started, playback_complete, cancelled, error, closed`.

Job collision: `409` `{key} is already running`.

### 8.3 `SCHEMA` v5 map

```
version, control, fields, languages{conversation,speech,asr},
voice_styles, param_groups, ops, prerequisites, components, models,
brains, brain_families, engines, defaults{tts_runtime,voice,asr,brain_runtime,brain_generation},
trace{schema,version,run_id,identifiers},
tts{url,text,audio,messages,events}
```

`SCHEMA.tts.messages` = `init, synthesize, cancel, close`  
`SCHEMA.tts.events` = `ready, synthesize_started, audio, chunk_done, cancelled, error`  
`SCHEMA.tts.audio` = `binary PCM16LE mono 24000 Hz`

### 8.4 TTS native HTTP/WS

Bind: `127.0.0.1:{port}` port from argv (8095).

| Route | Contract |
|---|---|
| GET `/health` | `{"status":"ok"}` |
| GET `/state` | `{"sessions": N}` |
| POST `/cancel` | JSON `{session_id}` → `{cancelled:true,session_id}` or 404 |
| WS `/tts` | text JSON in; text JSON + binary out |

WS client → server:

```
{"type":"init", ...VoiceConfig fields..., identifiers}
{"type":"synthesize", "text":"...", "request_id":"...", identifiers}
{"type":"cancel"}
{"type":"close"}
```

WS server → client:

```
{"type":"ready", language, sample_rate:24000, format:"pcm_s16le", identifiers including session_id}
{"type":"synthesize_started", identifiers}
{"type":"audio", chunk_index, samples, total_samples, sample_rate:24000, last, identifiers}
<binary PCM16LE>
{"type":"chunk_done", samples, chunks, seconds, sample_rate, rms_dbfs, peak_dbfs, clip_pct, capture_audio, identifiers}
{"type":"cancelled"|"error", message, samples, chunks, identifiers}
```

`main` argv required: `--port --model --s3gen-gguf --n-gpu-layers --context --max-sessions --threads`. Missing/invalid → exit 2. SOURCE: `server/src/main.cpp`.

### 8.5 Engine HTTP (consumed, not owned)

| Engine | Method | URL | Request | Response used |
|---|---|---|---|---|
| ASR | POST | `http://127.0.0.1:8097/v1/audio/transcriptions` | multipart file + response_format | `text` |
| ASR | GET | `http://127.0.0.1:8097/health` | — | any 200 |
| Brain | POST | `http://127.0.0.1:8098/v1/chat/completions` | JSON chat | `choices[0].message.content` / `reasoning_content`, `usage`, `timings` |
| Brain | GET | `http://127.0.0.1:8098/health` | — | any 200 |

### 8.6 `trident.event` record

Written fields: `schema, version, event_id, run_id, seq, ts, time, level, source, component, event, message, pid, thread, {identifiers}, data`.

Native ingest prefix: `TRIDENT_EVENT ` + JSON `{schema:"trident.native-event", version:1, source:"tts-native", component:"tts", level, event, native_ms, message, data, identifiers}`.

String truncate 8192; dict/list cap 256; depth 6; bytes become `{bytes:N}`; secrets redacted.

### 8.7 Flow stages emitted by C-CTRL

`set_flow` writes `pipeline.pipeline.stage` with `{from,to,...}`. Turn uses `transcribing` → `thinking` → `ready_to_speak` or `error`. C-UI overlays `listening` / `speaking` / `complete` without requiring those strings from the controller.

---

## 9. Critical Dependencies

### 9.1 Graph

```
Python 3.11 (host, not panel-installable)
    main.py
        PACKAGES: MinGit, CMake 4.4.2, MSVC VCTools, Vulkan SDK 1.4.357.0
            checkout chatterbox@ddca05f
            apply patches/chatterbox.patch
            checkout ggml@58c3805
            CMake GGML_VULKAN=ON
                tts-cpp.lib, mtl_tokenizer.lib, ggml*.lib
            CMake server + FetchContent httplib, nlohmann_json
                tts-server.exe + ggml*.dll
        GitHub zip llama-b10453-bin-win-vulkan-x64
        GitHub zip parakeet-v0.5.0-bin-win-vulkan-x64
        Hugging Face GGUFs (SHA pinned)
        assets/default-reference.wav
            engines:
                ggml-vulkan.dll (TTS)
                ggml-vulkan.dll (llama-server)
                PARAKEET_DEVICE=Vulkan0
                --device Vulkan0
        Chrome
            AudioWorklet, EventSource, WebSocket, getUserMedia
```

### 9.2 Link surface of `tts-server`

`server/CMakeLists.txt` links: `tts-cpp.lib`, `mtl_tokenizer.lib`, `ggml.lib`, `ggml-cpu.lib`, `ggml-vulkan.lib`, `ggml-base.lib`, `httplib`, `nlohmann_json`, `Vulkan_LIBRARIES`, `ws2_32`. Defines `GGML_USE_VULKAN`. Requires `include/tts-cpp/chatterbox/engine.h`. WIN32: `/MP /bigobj`, `_CRT_SECURE_NO_WARNINGS NOMINMAX WIN32_LEAN_AND_MEAN`. Post-build copies ggml DLLs next to the exe.

### 9.3 Prerequisite discovery

| Name | Ready iff |
|---|---|
| python | `sys.executable` exists (always if controller runs) |
| git | `tools/git/cmd/git.exe` or `PATH` |
| cmake | `tools/cmake-4.4.2-windows-x86_64/bin/cmake.exe` or `PATH` |
| msvc | highest `ProgramFiles(x86)/Microsoft Visual Studio/*/BuildTools/VC/Tools/MSVC/*/bin/Hostx64/x64/cl.exe` |
| vulkan | first of `VULKAN_SDK`, `tools/VulkanSDK/1.4.357.0`, `C:/VulkanSDK/*` that has `Include/vulkan/vulkan.h` and `Lib/vulkan-1.lib` |

`build_env` prepends Vulkan `Bin` + git/cmake dirs to `PATH` and sets `VULKAN_SDK`.

### 9.4 Shared device

ASR, Brain, and TTS all target Vulkan. No Trident mutex across the three processes. UNTESTED-PRIOR: concurrent load OOM behavior. Falsifier: `load_engine` all three and read engine statuses + llama/TTS stdout.

### 9.5 Files the next agent must treat as first-party

`ARCHITECTURE.md`, `main.py`, `log.py`, `panel.html`, `panel.css`, `panel.js`, `audio-processor.js`, `server/CMakeLists.txt`, `server/include/engine_wrapper.hpp`, `server/include/server.hpp`, `server/src/main.cpp`, `server/src/server.cpp`, `server/src/engine_wrapper.cpp`, `patches/chatterbox.patch`, `.gitattributes`, `.gitignore`, `data/config.json`, `data/models.json`, `assets/default-reference.wav`.

`.gitattributes` sets `text eol=lf` on first-party text so Windows `core.autocrlf=true` cannot turn a one-line edit into a whole-file CRLF rewrite. SOURCE.

Third-party trees are disposable: next `install_component tts` resets them to pins and reapplies the tracked patch.

External working reference (not first-party, do not merge blindly): `C:\Users\eb-wjt\Downloads\clone-reliable` @ `3e88ec2`. Use its **later** `chatterbox_engine.cpp` bake hunk and its generate path. Ignore its earlier CAMPPlus-throw hunk.

---

## 10. System Limitations and Workarounds

| ID | Limitation | Evidence | Workaround in tree | Absent |
|---|---|---|---|---|
| L1 | Codec GGUF has no CAMPPlus; S3Gen 192-d speaker is builtin | SOURCE `chatterbox_tts.cpp:2073`; CLONE later bake hunk; this bake leaves embedding empty | T3 256-d + prompt tokens/feat from reference; S3Gen speaker = GGUF builtin | Do not add CAMPPlus to this GGUF. |
| L2 | Header `v3::n_ctx=512` vs Trident 1536 | SOURCE both | Python migrate + `--context` | Header default not updated to 1536 |
| L3 | `max_tokens` default 1000 still requires ctx ≥ prompt+generate | COMMIT `967f95c` | ctx 1536 / min 1280 | No automatic clamp of `max_tokens` to remaining ctx |
| L4 | Vulkan uses B=2 flash_attn, not sequential CFG | live `use_optimized_mtl_backend = !cpu` | Softmax path deleted after crash | No validated Vulkan softmax |
| L5 | TTS `sessions` default 1, one lane `a` | SOURCE | raise `tts.engine.sessions` and restart (UI still one lane) | Second lane key not implemented |
| L6 | Brain `parallel` locked at 1 | SOURCE argv + field max | none | Multi-slot brain |
| L7 | Conversation cannot select Speech Lab style | `runTurn` hardcodes `natural` | user can use Speech Lab | No conversation style field |
| L8 | Browser VAD is RMS, not a model | `panel.js:rms` | threshold/silence/min_speech fields | No Silero/Parakeet VAD |
| L9 | Capture `AudioContext(16000)` actual rate may differ | C-UI logs `actual_rate` | WAV uses `context.sampleRate` | No resampler in C-UI |
| L10 | Chrome only | no other engine in JS | none | Firefox/Edge untested by design |
| L11 | Install jobs sequential from panel | `installAll` | individual chips also one-at-a-time via 409 | Parallel downloads not offered |
| L12 | Python cannot be installed by Trident | `install_prerequisite` | documented host prereq | — |
| L13 | S3T discovered by substring `s3t` in the codec directory | `find_s3t_gguf` | Trident keeps the three GGUFs together | No explicit `--s3t` flag |
| L14 | `ENGINE_MODELS["brain"]=("gemma",)` unused on load | `load_engine` special-cases brain | `active_brain_path()` | Catalog pin of unused brains not required to start |
| L15 | llama-server binary always from `tools/runtime/gemma` | `component_artifact("gemma")` | works for Qwen/custom GGUF | No per-brain llama build |
| L16 | T3 still off-distribution on this Iris Xe after generate-path restore | Speech-lab LOG (trace `f85bcfb0`): first=7838, `repetition_guard` @89, Parakeet on output `"Na forma sieć the paktoput."`, centroid 456 Hz, 3.640 s, SHA `30412783…`. Prior flash_attn turn `aa20c24b`: first=7839, 433 tokens, `"I'm shh I'm sh uh hash problems."`, centroid 4003 Hz. | flash_attn + `encode(text)` + `speech_position=n_past` + ctx 1536 | CAMPPlus will not be the next T3 lever. After bake is unblocked, re-measure T3 stop / first token / Parakeet. |
| L17 | `log.record` listener errors discarded | `except Exception: pass` | file still written | No listener-fault event |
| L18 | `browserTrace` network errors discarded | `.catch(() => null)` | server-side native/controller events remain | Dropped browser spans |
| L19 | Build log suppression | non-matching stdout counted `suppressed` | `BUILD_LOG_TOKENS` + first unique MSVC `C####` | Full MSVC log not in JSONL |
| L20 | `1.patch` `2.patch` `3.patch` unused | no code reference | live patch is `patches/chatterbox.patch` | Risk of human applying the wrong file |
| L21 | Diagnostic report file is PyTorch-era | content | ignore for this stack | — |
| L22 | `wait_ready` 600 s then raise | SOURCE | job error + engine leftover if process still starting | No cancel of a hung load except unload |
| L23 | `stop_engine` terminate 10 s then kill | SOURCE | lanes reset if name==tts | In-flight WS clients see close |
| L24 | Reference must be ≥5 s; clone replace requires ≥10 s | `reference_state`, `run_turn` | skip clone, keep previous | No automatic pad |
| L25 | Shared Vulkan device, no admission control | three processes | brain `--fit` | No explicit VRAM budget split |
| L26 | `openTts` waits 30 s for WS `ready` then reports timeout even if native already sent `{type:error}` | `panel.js` `timeout_ms: 30000`; LOG seq 1031 error then seq 1037 timeout | native error is traced as `browser.tts.error` / `browser.tts.synthesis_failed` | Spinner can outlive the real failure by ~24 s |
| L27 | Agent Job Object + `load_engine` → `3221225794` | prior session | Detached `Start-Process` this session loaded all three engines | Do not relaunch `main.py` from the agent job |
| L28 | Advertised bake contract can lie | was `reference_campplus` while codec has no CAMPPlus | `tts_session` now advertises `builtin_fallback`; native summary remains authoritative | — |
| L29 | clone-reliable `chatterbox.patch` contains two bake hunks | earlier hunk adds CAMPPlus throw; later hunk deletes it | later hunk is golden | Never copy the earlier hunk alone |
| L30 | Windows cannot replace `tools/runtime/tts` while `tts-server.exe` is loaded | LOG `run-359ca74d…` seq 300 `WinError 183` after a successful 239 s Chatterbox + 2 s server build | `install_component` now `stop_engine("tts")` then atomic `.part` replace | clone-reliable never copied: `component_artifact("tts")` is `server/build/Release/tts-server.exe`. Trident keeps the runtime copy (D4). |

### 10.1 Error handling map

| Layer | Mechanism |
|---|---|
| HTTP | `ApiError(code, msg)` → `{error}`; unexpected → 500; `KeyError/TypeError/ValueError` on POST → 400 `invalid request` |
| Jobs | thread catches Exception → `jobs[key].status=error` |
| Engines | unexpected process exit → `engines[name].status=error`; expected stop after pop from `PROCESSES` is not error |
| Native WS | exception → `{type:error}` or `{type:cancelled}` if message contains `cancelled` |
| Native HTTP cancel | 404 session missing; 400 parse |
| Fetch/install | SHA/size mismatch deletes `.part` and raises |
| ZIP extract | path-escape rejected; exactly one expected exe |

### 10.2 Performance mechanisms present in source

| Mechanism | Location |
|---|---|
| Stream first chunk 75 tokens | `VOICE_DEFAULTS`, `VoiceConfig.first_chunk` |
| Later chunks 150 tokens | same |
| S3Gen preload thread during T3 load | `Engine::Impl` |
| llama `--flash-attn on --repack --fit` | `load_engine` |
| TTS Vulkan B=2 + flash_attn | live `t3_mtl.cpp` |
| AudioWorklet not main-thread render | `audio-processor.js` |
| Log rotate 20 MiB | `log.py` |
| Build stdout filter | `run()` |
| `PcmRing` report every 2400 samples (~100 ms @ 24 kHz) | worklet |

UNTESTED-PRIOR: RTF, VRAM, or token/s on this host. Falsifier: one loaded turn and the `tts.native.benchmark` / llama `timings` events already emitted by the stack.

---

## Appendix A — File ↔ ID index

| File | IDs |
|---|---|
| `main.py` | C-CTRL |
| `log.py` | C-LOG |
| `trident.log.jsonl` | C-LOG |
| `panel.html` `panel.css` `panel.js` | C-UI |
| `audio-processor.js` | C-AW |
| `server/src/server.cpp` `server/include/server.hpp` | C-TTS-SRV |
| `server/src/engine_wrapper.cpp` `server/include/engine_wrapper.hpp` `server/src/main.cpp` | C-TTS-WRAP |
| `server/CMakeLists.txt` | C-TTS-WRAP, C-TTS-SRV |
| `patches/chatterbox.patch` | C-PATCH |
| `data/config.json` `data/models.json` `data/brains.json` | C-CFG |
| `data/*.wav` `assets/default-reference.wav` | C-REF |
| `third_party/chatterbox.cpp/**` | P-TTS (pinned+patched) |
| `tools/runtime/parakeet/**` | P-ASR |
| `tools/runtime/gemma/**` | P-BRAIN |
| `tools/runtime/tts/**` | P-TTS binary |

## Appendix B — Handover for next agent

HEAD of this document commit is the next session start. Source at write: `0eb4f4d` plus the TTS install unload/atomic-copy fix.

### Decision already taken

Do **not** put CAMPPlus weights in `chatterbox-mtl-codec-f16.gguf`. Do **not** keep the fatal CAMPPlus throw. Match clone-reliable `3e88ec2` / tag `MILESTONE-21` (`chatgpt v3 golden`) later bake hunk: leave `s3gen_embedding` empty; S3Gen copies `s3gen/builtin/embedding`. See §7.1. Do **not** merge clone-reliable's engine_wrapper OLA crossfade or its in-place `server/build/Release` load path; Trident keeps `tools/runtime/tts` + native_event server.cpp.

### Measured this session (LOG `run-359ca74de66a4ec796775238c0740faf` + prior `run-c8d39587d774477fa076544cf945751e`)

Conversation turn `trace-6031f6bf` / `turn-8567386dfc5041f2b7aaa3984550d70c` at 08:38:39 UTC:

- User 18.072 s mono PCM16 16 kHz, 578348 bytes, SHA `022411f98b84f06b0d35edbbb7fbb45af1bbe9e5a59d73611c81fa68c48b25f2`, RMS −35.78, peak −19.11, clip 0. Cloned to `data/reference.wav` generation 1.
- Parakeet 829 ms: `Hello, I want you to recite every number from zero to ten using Polish language. Write every number in separated by the comma.`
- Gemma 2281 ms: `Zero, jeden, dwa, trzy, cztery, pięć, sześć, siedem, osiem, dziewięć, dziesięć.`
- TTS session init started. S3Tokenizer 250/150 ok. Then `voice: s3gen GGUF has no CAMPPlus weights` and `Engine: CAMPPlus embedding failed`. `received_samples=0`. `data/last-output.wav` still the 10:13:46 file (174764 bytes, SHA `30412783…`, prior speech-lab `"Na forma sieć the paktoput."`).
- Browser then `Voice connection timed out` at 30 s. Panel looked like synthesis was running.

Config: `conversation.clone_voice=true`, language `en`, style `natural`, seed 42, gpu_layers 99, context 1536.

Engines that loaded: ASR pid 7908 `:8097`, brain pid 12500 `:8098` (Gemma 4 E2B), TTS pid 7740 `:8095`. Controller python 12248. Started via detached `Start-Process`, not the agent job.

Panel also ran `install_component tts` this run. New `build.json` `f245cf4e…`. Same required-CAMPPlus source.

Later panel rebuild (LOG `run-359ca74d…`, controller pid 12788): Chatterbox 239.8 s + server 2.3 s both code 0. Applied live bake already matches `0eb4f4d` (`s3gen_embedding=builtin_fallback`, no CAMPPlus throw). Install then failed `WinError 183` on `tools\runtime\tts` because pid 7740 still held the 10:37:44 exe. New bits: `server/build/Release/tts-server.exe` 11:05:22, 1140224 bytes. Speak not re-measured.

### Earlier speech-lab (COMMIT `31ba93d` body, pre-throw)

Text `This is a multilingual voice synthesis test.` Conditioning `s3gen_embedding=builtin_fallback`. T3 first=7838, `repetition_guard` 89 tokens. Output 3.640 s 24 kHz mono. That WAV is still on disk because bake never completed again.

### Next implementation (source only, then one Speak)

1. Edit bake in `third_party/chatterbox.cpp/src/chatterbox_engine.cpp` and regenerate `patches/chatterbox.patch` from pin `ddca05f` the same way as `31ba93d` (placeholders `__EM_DASH__` / `__SECTION_SIGN__`). Delete CAMPPlus call + empty-embedding throw. Keep VoiceEncoder / S3Tok / prompt_feat required.
2. `main.py` `tts_session` contract: `s3gen_speaker_embedding` = `builtin_fallback`.
3. Do not change flash_attn, `speech_position = n_past`, `tok.encode(text, language)`, or models.
4. Operator or detached process: if `:8765` is already up with engines, unload/reload **only TTS** through panel REST so the new exe is picked up after install. If you rebuild, `install_component tts` (now unloads TTS before copy) then `load_engine tts`. Do not start a second `main.py`.
5. User Speaks the same sentence (Speech Lab or conversation). Do not invent a WS client.
6. Success of **this** change: session init reaches `s3gen_embedding=builtin_fallback`, PCM arrives, `last-output.wav` mtime updates. Failure: still CAMPPlus throw.
7. After a new WAV exists, Parakeet `POST /api?op=asr` on it. That is the T3 intelligibility measurement, not a CAMPPlus measurement. Expect possible `repetition_guard` / off-codebook first token. That is L16, next T3 hypothesis.

### Do not do

- Do not invent a Python/PowerShell WebSocket client.
- Do not reinstall Parakeet, Gemma, or model GGUFs. Do not run `convert-s3gen-to-gguf.py` against the pinned codec.
- Do not re-introduce Vulkan softmax / sequential CFG.
- Do not pad start_text/stop_text or wrap `punc_norm` on the MTL Engine path. Measured: those two pads moved first token into 7840–7844 unless the sampler also drops ids above EOS.
- Do not start `main.py` from the agent Job Object.
- Do not treat `Chatterbox_V3_Diagnostic_Report.md` or root `1.patch`/`2.patch`/`3.patch` as the live contract.
- Do not treat builtin S3Gen speaker as a language-tag bug. It is identity-only (D14).

Pins unchanged: chatterbox `ddca05f`, ggml `58c3805`, branch `runner-x`. clone-reliable remains at `C:\Users\eb-wjt\Downloads\clone-reliable` @ `3e88ec2`.
