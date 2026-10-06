# Trident v2

An independent Windows implementation. All source, configuration, prompts, and tool definitions live directly in this directory. `artifacts/` contains installed dependencies, native runtimes, and model files. `runs/` contains recorded evidence and restart checkpoints.

Use 64-bit Python 3.11, an NVIDIA GPU with drivers supporting CUDA 12.4 and Vulkan, and sufficient VRAM for the selected models. From the repository root:

```powershell
py -3.11 v2/install.py
v2/artifacts/python/Scripts/python.exe v2/trident.py
```

The installer downloads the pinned llama.cpp CUDA release, NeMo-Speech.cpp CUDA release, CrispASR Vulkan release, Gemma and its vision projector, Nemotron ASR, Chatterbox and its codec. It installs no other inference models. No application, tests, or installer were run while writing this version.

`config.toml` preserves the existing Cursor command and `gpt-5.6-luna-none` model selector. That exact installed Cursor version must exist and already be logged in with access to that model. The application does not switch providers, models, or accounts. Update the command explicitly if the installed path differs.

Telegram Desktop's configured `tdata` must contain exactly one authorized Trident user account distinct from Wojciech (`5884279027`). Wojciech's account must be resolvable in its existing dialogs. Telegram bots cannot make this private-call connection. Missing files, login, sessions, arguments, model responses, or transport operations end the process with one error. The only readiness wait is for the two native HTTP workers to finish loading; a worker exit or readiness deadline ends startup.

Boot loads resources and connects Telegram. It does not dial, send a greeting, create a goal, or ask Luna to invent one. A private text message or incoming owner call supplies input. Luna returns a JSON array of chosen tool calls, executed by Python in order. The catalog maps names to Python functions and is loaded from disk each step. Luna may explicitly author Python to batch independent operations. There is no chess policy, Paint script, retry controller, or automatically chosen next action.

An incoming call rings until Luna invokes `answer`. `dial` places an outgoing call; `speak` transmits Chatterbox audio only on an established line, using the baked voice in the selected Nano model. Chat is independent. Received 16 kHz PCM is segmented by energy and silence without a VAD model, then passed to Nemotron's streaming recognizer in 160 ms chunks. This version does not implement continuous partial transcripts or Telegram video. Speech segmentation thresholds are explicit configuration. Call connection, sent chat IDs, ASR audio, synthesized audio, screenshots, model requests, responses, and tool starts/results are recorded.

`screen` sends the actual full-resolution desktop screenshot to Gemma; Luna receives its text and coordinate report. `images` supports further inspection, including crops made through Python. Desktop tools use native Windows pixel coordinates, including negative coordinates on monitors left of the main display. Real chess and Paint work must be selected by Luna and verified from the real screen. An executed click does not establish that a chess move or drawing succeeded.

When Luna detects a needed change, `heal` suspends the owner's goal. `rewrite` writes complete flat source files and deletes unnecessary files, including mutable tool definitions and prompts. `activate` explicitly saves state and restarts Python to load rewritten architecture; the call must be closed first. `resume` returns to the owner's goal after Luna supplies repair evidence. Tool catalog changes take effect at the next step; already imported core modules require activation. The prompt directs Luna to abandon an approach after two observed failures; code neither retries nor chooses a replacement approach.

`finish` closes the recorded task and queues its lesson. Only with no active goal and no call does Gemma study that trace and Luna produce updated actor/student prompts and tool descriptions. Owner input interrupts idle learning; the pending lesson remains for the next idle period. Every applied lesson, including the trace-based teaching, is retained in `trace.jsonl`. `wait` pauses unfinished work without starting learning. `finish(shutdown=true)` requests exit after the recorded task's lesson; otherwise the empty queue waits.

Learning here changes prompts and descriptions, not model weights. It can improve Gemma's guidance but cannot guarantee Gemma will match Luna. Successful chess, drawing, inbound/outbound calling, end-to-end audio, learning, architecture activation, and shutdown have not been demonstrated by this rewrite. Its current evidence is code and static review only.

Native interface references: [NTgCalls](https://github.com/pytgcalls/ntgcalls/tree/v3.0.0), [NeMo CLI](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/v0.1.0/docs/cli.md), [CrispASR TTS](https://github.com/CrispStrobe/CrispASR/blob/v0.8.41/docs/tts.md), [Gemma GGUF](https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF). Downloaded dependencies and models retain their own licenses. The project's MIT notice is in `LICENSE`.
