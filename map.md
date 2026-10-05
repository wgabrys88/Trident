# map

One process. `trident.py` joins the organs. `install.py` fetches binaries and weights. Every organ reads `config.toml` through `organs.CONFIG`. `reference.wav` is tracked, binary, and present. This map did not read the audio.

Tracked files: `.gitattributes`, `.gitignore`, `LICENSE`, `config.toml`, `install.py`, `map.md`, `organs/__init__.py`, `organs/brain.py`, `organs/ears.py`, `organs/eyes.py`, `organs/hands.py`, `organs/memory.py`, `organs/mouth.py`, `organs/telegram.py`, `reference.wav`, `requirements.txt`, `trident.py`.

## config.toml

`[owner]` `telegram_id` = 5884279027, `name` = Wojciech. Read by `trident` (`name`) and `telegram` (`telegram_id`).

`[paths]` `models` = models, `bin` = bin, `state` = state. Read by `organs`, `install`, `brain`.

`[brain]` `model` = gemma-4-E2B-it-Q4_0.gguf, `mmproj` = mmproj-gemma-4-E2B-it-Q8_0.gguf, `host` = 127.0.0.1, `port` = 8080, `context` = 16384, `slots` = 2, `gpu_layers` = 999, `threads` = 4, `image_tokens` = 1120, `ubatch` = 2048, `temperature` = 0, `top_k` = 64, `top_p` = 0.95, `min_p` = 0.05, `max_tokens` = 4096, `max_tool_steps` = 200. Read by `brain`. `model` and `mmproj` also by `install`.

`[eyes]` `max_side` = 1920. Read by `eyes`.

`[vision]` `model` = Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf, `mmproj` = mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf, `side` = 1920, `context` = 4096, `slots` = 1, `ubatch` = 1024, `image_tokens` = 1024. Read by `install` for the two filenames. Nothing loads this model. There is no survey.

`[cloud]` `model` = grok-4.7-xhigh. Read by `trident` when `consult` runs. That id is Grok 4.7 extra-high, and it is not the fast id. A one-word ask on cursor-agent `2026.10.01-e373342` returned `ok`. The same build rejects `grok-4.7-xhigh[context=500k,fast=false]` and `grok-4.7[context=500k,effort=xhigh,fast=false]` with `Cannot use this model`. The 500k override is not applied. It is a local process in ask mode. It is not a virtual machine.

`[ears]` `vad_threshold` = 0.65, `min_silence_ms` = 700, `pad_ms` = 120, `min_utterance_s` = 1.0, `model` = nemotron-3.5-asr-streaming-0.6b.q8_0.gguf. Read by `ears`. `model` also by `install`.

`[mouth]` `device` = cuda, `reference` = reference.wav. Read by `mouth`.

`[telegram]` `desk_video` = true, `desk_fps` = 12. Read by `telegram`.

`[install]` `llama_tag` = b11371, `llama_asset` = bin-win-cuda-12.4-x64.zip, `cudart_asset` = cudart-llama-bin-win-cuda-12.4-x64.zip, `nemo_url` = https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.1.0/nemo-speech-0.1.0-windows-x86_64-vulkan.zip, `gemma_url` = https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_0.gguf, `mmproj_url` = https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF/resolve/main/mmproj-gemma-4-E2B-it-Q8_0.gguf, `vision_url` = https://huggingface.co/ggml-org/Qwen2.5-VL-3B-Instruct-GGUF/resolve/main/Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf, `vision_mmproj_url` = https://huggingface.co/ggml-org/Qwen2.5-VL-3B-Instruct-GGUF/resolve/main/mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf, `ear_url` = https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/resolve/main/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf, `silero_url` = https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx, `torch_index` = https://download.pytorch.org/whl/cu124. Read by `install`.

## Vision budget

Gemma 4 image budgets are 70, 140, 280, 560, and 1120. 1120 is the maximum, about 2.6 million pixels. A 1920×1080 screen is 2.07 million pixels, so 1120 holds the whole picture and 560 does not. The vision tokens use non-causal attention, so the image batch has to fit in one micro-batch. `ubatch` is 2048. llama-server's default `--batch-size` is 2048, so the 1120 batch fits. A 1120-token image can encode as slightly more than 1120 tokens, and 1024 does not hold that.

One model is on the GPU at a time. Gemma, the mouth, and the ear each load, run, and exit before the next one loads. Qwen is downloaded and is not started. A look is Gemma reading the picture `picture()` just made. The other mind is the Cursor agent `consult` starts. It does not use this GPU.

`imprint` draws on the picture a model is about to see, and `video_rgb` calls that same function for the call. The drawing is the pointer and the foreground controls. There is no list under the screen. A 20-pixel icon is still smaller than one 1120-token cell on a 1920×1080 frame.

## organs/__init__.py

Talks to `config.toml` and the `state/` directory. Talked to by `brain`, `ears`, `eyes`, `hands`, `memory`, `mouth`, `telegram`, `trident`.

`ROOT` is the repo root. `CONFIG` is `config.toml` parsed at import.

`path_of(section, key) -> Path`. `brain`, `ears`, and `vision` resolve under `paths.models`. Any other section resolves under the repo root. Returns that directory plus `CONFIG[section][key]`.

`state_dir() -> Path`. Creates `paths.state` and returns it. `memory.json` and `inbox.txt` stay here.

`run_dir() -> Path`. One folder per process, `state/run_YYYY-MM-DD_HHMM`, for example `run_2026-10-04_2030`. The folder is created on first use. If that name already exists, the next free `run_YYYY-MM-DD_HHMM-2` is used, so a later process never writes into an older folder. The path is stored in `TRIDENT_RUN`. A child process, including the mouth, inherits that variable and uses the same folder. Call audio, the consult workspace, `controls.ps1`, and `llama-server.log` go here. There is no `trident.log`, no `last_prompt.txt`, and no `last_image.png`. The chat is the record of what a model was sent and what it returned.

`log(name) -> logging.Logger`. First call with no root handlers sets INFO, format `%(asctime)s %(name)-8s %(message)s`, time `%H:%M:%S`, stderr only.

## organs/brain.py

Talks to `organs` (`CONFIG`, `ROOT`, `log`, `path_of`, `run_dir`), `bin/llama/llama-server.exe`, and HTTP `http://{host}:{port}`. Talked to by `trident` (`Brain`, `Tool`, `UserTurn`) and `python -m organs.brain`. Logger name `brain`. Import calls `log("brain")`.

Tokens: `BOS` `<bos>`, `TURN_OPEN` `<|turn>`, `TURN_CLOSE` `<turn|>`, `THINK` `<|think|>`, `QUOTE` `<|"|>`, `TOOL_RESPONSE_OPEN` `<|tool_response>`, `TOOL_RESPONSE_CLOSE` `<tool_response|>`. The image marker is whatever the running server reports. `Brain.media()` reads `media_marker` from `GET {url}/props` on first use and stores it on `Brain.marker`. `stop()` clears it.

`Tool(name, description, params, run, optional=())`. `params` maps a name to `description`, `type` (`STRING`, `INTEGER`, `NUMBER`, or `BOOLEAN`), and optional `enum`. `optional` names may be omitted. `run` is `Callable[..., object]`.

`UserTurn(text)`. A tool that returns this ends the completion loop. `Reply.follow` is that text. `trident` starts a new request with it, so she reads it as the user.

`Reply(text, follow="")`.

`quoted`, `literal`, `declare`, `tool_response`, `turn`, `plain`, and `parse` build and read Gemma's tool text. `declare` sorts keys, quotes descriptions, and emits the `<|tool>declaration:...` block with a space before `},required` and a space before the final `}`. A dict tool result is emitted with sorted keys. `parse` reads one `<|tool_call>call:NAME{...}<tool_call|>`. Quoted values stay strings. Bare `true`/`false` become bools. Bare integers and decimals become `int` and `float`.

`Brain.__init__()`. `url` from `brain.host` and `brain.port`. `proc` is `None`. `marker` is `None` until `media()`. `sink` is `None` until `trident` sets it to `mirror`.

`Brain.emit(text, images)`. Calls `sink` when it is set and there is text or an image. `complete` calls it with the prompt and the images before the POST, and with the response text after.

`Brain.alive() -> bool`. GET `{url}/health`, timeout 2. True when status is 200. `URLError` or `OSError` returns False.

`Brain.start()`. Returns immediately when `alive()`. Otherwise runs `bin/llama/llama-server.exe` with `path_of("brain", ...)`, `brain.context`, `brain.slots`, `brain.ubatch`, and `brain.image_tokens`. `--image-min-tokens` and `--image-max-tokens` are both that count. Shared flags: `--host`, `--port`, `--n-gpu-layers`, `--threads`, `--flash-attn off`, `--cache-type-k f16`, `--cache-type-v f16`, `--no-webui`, `--log-file` the run folder's `llama-server.log`. Working directory is the exe directory. Stdio is discarded. Window is hidden. Missing exe raises `FileNotFoundError`. Polls 0.5 s for up to 300 s. Process exit raises `RuntimeError`. Timeout raises `TimeoutError`. On this binary, `--ctx-size 16384 --parallel 2` comes up as `n_slots = 2`, `n_ctx_slot = 8192`.

`Brain.stop()`. If `proc` is still running, `terminate()` and `wait(10)`, then `kill` if it is still up. A llama-server that was already listening is ended too, so the next model can load. Waits until the port is down, at most 20 s. Sets `proc` and `marker` to `None`.

`Brain.complete(prompt, images=(), schema=None, stop=()) -> str`. Emits the prompt and images, then POSTs `{url}/completion`, timeout 600. Body: `prompt` (a string, or `{prompt_string, multimodal_data}` of base64 images when `images` is non-empty), `n_predict` `brain.max_tokens`, `cache_prompt` false when `images` is non-empty, otherwise true, `stop`, and the same sampling fields. `schema` sets `json_schema`. Emits the response text. Returns `content`. Logs elapsed seconds plus `timings.prompt_n` and `timings.predicted_n` under the word `gemma`.

`Brain.ask_json(question, schema, png=None) -> object`. Optional image, given schema, thinking off. An image prefixes the question with `media()`. Returns `json.loads` of the completion. `look` uses this. `y` and `x` in that object are on the 1000-grid of that image. `screen_px` converts them.

`Brain.think(system, tools, history, user) -> Reply`. System turn is `THINK`, `system`, and every `declare(tool)`. History is user/model turn pairs. `trident` passes an empty history. Then the user turn and an open model turn. Up to `max_tool_steps` completions, stopped on the tool-response open token and `TURN_CLOSE`. A completion with no tool returns `Reply(plain(out))`. An unknown tool name yields `unknown tool {name}` and the loop continues. A tool that raises ends the turn. A `UserTurn` returns `Reply("", text)`. Otherwise the completion and `tool_response` are appended and the loop continues. Exhausting the steps returns `Reply("I am still working on it.")`.

`main()`. `Brain.start()`, then `ask_json` on the command-line question, or `What is the capital of France and what are its GPS coordinates?` when the command line is empty. Schema requires `capital` (string), `latitude` (number), `longitude` (number). Prints that JSON and `think("You are Gemma. Answer in one short sentence.", {}, [], question).text`. Does not call `stop()`.

## organs/ears.py

Talks to `organs` (`CONFIG`, `ROOT`, `path_of`), `models/silero_vad.onnx` through onnxruntime, and `bin/nemo-speech/bin/nemo-speech.exe`. Talked to by `trident` (`transcribe`, `write_wav`) and `telegram` (`Segmenter`). `python -m organs.ears FILE.wav` prints `transcribe` of that path.

`RATE` = 16000. `WINDOW` = 512.

`Segmenter` loads Silero with `CPUExecutionProvider`. `pad`, `min_silence`, and `min_len` come from `pad_ms`, `min_silence_ms`, and `min_utterance_s`. `push` returns a finished utterance at 16 kHz, or `None`. Silence below `vad_threshold - 0.15` ends speech after `min_silence` samples. A clip shorter than `min_len` is dropped.

`write_wav(path, samples, rate=16000) -> Path`. Mono 16-bit wav, samples clipped to [-1, 1].

`transcribe(wav) -> (text, language)`. Runs `nemo-speech.exe transcribe` with `--device vulkan --format json --verbatim --quiet --endpointing=true --stop-history-eou-ms 1200`. Non-zero exit raises `RuntimeError`. Text is NFC, `<lang>` tags removed. Polish letters `ąćęłńóśźż` force `language` to `pl`.

## organs/eyes.py

Talks to `organs` (`CONFIG`, and `run_dir` in `__main__`), `user32`, `hands.interactive_controls`, and PIL `ImageGrab`, `ImageDraw`, and `ImageFont`. Talked to by `trident` and `telegram`. At import, `SetProcessDpiAwarenessContext(-4)`. `GetCursorPos` has argument types set. There is no module docstring.

`VIDEO` is `(960, 540)`.

`screen_size() -> (width, height)`. `GetSystemMetrics(0)`, `GetSystemMetrics(1)`.

`refresh()`. Stores `interactive_controls()` on `boxes`. An empty title is the foreground window. `picture` calls this. The video does not.

`imprint(image) -> Image`. Copies the image. For each stored control, a cyan box and the name, scaled from screen pixels into this image. Then the pointer, from `GetCursorPos` even when Windows is not showing a cursor. The point is scaled into this image. Red lines of width 2 cross at that point, a white arrow with a black outline has its tip there, and yellow text at 32px on a black plate reads `y` then `x` on the 1000 grid of this image. `picture` and `video_rgb` both call this and no other drawer.

`picture() -> bytes`. `refresh()`, then `ImageGrab.grab()` of the primary monitor. Longest side at most `eyes.max_side` (LANCZOS). `imprint`, then RGB PNG, `compress_level` 1.

`video_rgb() -> Image`. The same grab, resized to `VIDEO` with BILINEAR, then `imprint`. `telegram.desk_i420` sends that frame.

`screen_px(y, x) -> (x, y)`. Clamps the 1000 grid and maps it through `screen_size()`. This is the pixel `click` uses.

`around(png, x, y) -> bytes`. `x` and `y` are screen pixels. Crops a 480-pixel window of that picture around the matching image pixel. RGB PNG, `compress_level` 1.

`python -m organs.eyes` writes `screen.png` in the run folder and prints that path and `screen_size()`.

## organs/hands.py

Talks to `user32.SendInput`, `powershell.exe`, and `run_dir` when listing controls. Talked to by `trident` and `eyes.refresh`. At import, `SetProcessDpiAwarenessContext(-4)`.

`aim(x, y) -> (x, y)`. `SetCursorPos`, then an absolute move, then `GetCursorPos`. `strike(x, y, how)` presses. `click(x, y, how="left")` aims, then strikes, and returns the cursor position from before the press. `right` uses the right button. `double` adds a second left down/up. Any other `how` uses the left button.

`drag(x0, y0, x1, y1)`. Left down, ten moves 0.02 s apart, left up.

`press(keys)`. Space-separated chords. `+` and `-` split a chord. An unknown name raises `KeyError`.

`type_text(text)`. A character the foreground layout can type is that virtual key, with shift, ctrl, or alt when the layout asks for them. Anything else is a Unicode key, one UTF-16 unit at a time.

`interactive_controls(title="") -> list`. Named interactive controls on one window: `(name, x, y, width, height)` in screen pixels. An empty title is the foreground window. A title such as `Untitled - Paint` reads that window when it is not in front. A repeated name keeps the smaller rectangle. The PowerShell is written to `controls.ps1` in the run folder, run, and deleted.

`run(command, timeout=25) -> str`. Hidden `powershell.exe`, the `start` alias removed. A command that is only a name, when that name is a zero-byte file, is `Start-Process -FilePath` and that name. Timeout returns `timed out`. A command whose stripped lowercase text starts with `start-process` waits 1.5 s. Empty output returns `exit {code}` when the code is non-zero, otherwise `ok`.

`python -m organs.hands` accepts `press`, `type`, `click`, and `run`. No `drag` verb.

## organs/memory.py

`state/memory.json` holds `facts` and `task`. `remember` appends a new stripped fact. `set_task` stores the latest chat, call, or typed request at the start of that turn. A consult reply does not replace the task. `block` is `Task:` then one `Remembered:` line per fact. That block is put on every Gemma request. A file that still has `quiet` true gains the fact `he asked not to be bothered`. The chat is the record of what was said. The file does not keep turns.

## organs/mouth.py

`python -m organs.mouth` reads the text on stdin, loads `ChatterboxTurboTTS` on `mouth.device`, calls `prepare_conditionals` on `path_of("mouth", "reference")`, which is `reference.wav`, writes pcm48 on stdout, and exits. That exit is what gives the GPU back. `speakable` keeps letters, digits, punctuation, and the tags `laugh`, `chuckle`, `sigh`, `gasp`, `cough`, `clear throat`, `sniff`, `groan`. `pieces` yields 24 kHz float32 audio: the first yield is about ten seconds, and a second yield is the rest when words remain. `pcm48` resamples to 48000 Hz signed 16-bit mono. Nothing is played on the PC speakers. The trident process does not import torch.

## organs/telegram.py

`Line` is the Telegram voice line. `OWNER` is `owner.telegram_id`. Capture is 48000 Hz. Playback is 16000 Hz into `Segmenter`. `desk_video` sends `video_rgb()` as the camera at `desk_fps`. That frame is `VIDEO`, 960×540, and it has already been through `imprint`. `start` quits Telegram Desktop, opens the first `tdata` account whose `UserId` is not `OWNER`, and reaches `idle`. `stop` hangs up when the line is up, disconnects, and starts Desktop again. `send_text`, `send_photo`, and `send_file` return immediately when `client` is `None`. `send_text` sends the whole string, in pieces of 4000 characters when it is longer than one Telegram message. `speak` requires the line to be `up` and paces 10 ms microphone frames. PC mic and speakers are not opened.

`python -m organs.telegram` connects and answers a call without sending speech.

## trident.py

`OWNER` is `owner.name`. `INBOX` is `state/inbox.txt`. `SYSTEM` tells Gemma who she is. She is the mind at his computer. She hears and speaks on the call. The computer's microphone and speakers are not hers. She decides from the meaning of what he says and what a look returns. She is stateless, and the memory block is how a preference reaches her. She calls tools. Python takes the picture, draws the pointer, and turns a place into a click. When the call is up he can see the screen. `consult` spawns a Cursor agent, and she says aloud why. Its reply is his next request. She is not told it came from the agent.

`request_text` puts `block()`, then `Call: up` or `Call: down`, then the text, on every Gemma request.

`mirror(text, images)` sends that text and those PNG bytes to him. `Brain.sink` is `mirror`, so each Gemma prompt, each picture on that prompt, and each Gemma response go out as they are. `consult` uses the same function for the agent prompt, the picture, and the agent reply. A plain reply is spoken only when the call is up. The words are already in the chat as the raw response. A turn that raises sends `str(exc)` and does not retry.

`ask_cursor(folder, prompt) -> str`. The latest `%LOCALAPPDATA%\cursor-agent\versions\<date>-<hash>` directory that contains `node.exe`. A name that does not start with a digit, including `dist-package`, is skipped. The command is that directory's `node.exe` and `index.js`, print mode, `--mode ask`, `--trust`, `--model` `cloud.model`, text output, workspace `consult` in the run folder. Ask mode is read-only. There is no `--force`, no sandbox change, and no worktree. It is a local process, not a virtual machine. Timeout 1800 s. Non-zero exit or an empty reply raises `RuntimeError`. The stdout is the reply, unchanged apart from the emptiness check.

`Trident.start` calls `brain.start()`, `line.start()`, then the worker and inbox threads. `stop` sets `stopping`, then `line.stop()` and `brain.stop()`. A call writes `call.wav` in the run folder, stops Gemma before `transcribe`, and starts Gemma again after, because the ear uses the GPU and then exits. The wav is not sent. `speak` stops Gemma, runs `python -m organs.mouth`, plays the pcm, and starts Gemma again.

`inbox_loop` reads `state/inbox.txt`, deletes it, and queues each non-empty line as `typed`. It does not send the file. A line that starts with `/` is `call`, `hang`, or `stop`. Chat, call, and typed requests become `task` before the turn.

`turn` clears the aim and the points, stores the task, and calls `think`. A `follow` is the next user text and the loop continues, up to `max_tool_steps`. The task stays the human request. A plain reply ends the turn. The step cap says `I am still working on it.` Nothing starts another turn while she is idle.

Tools she can call:

- `look` requires `what`. `picture()`, then `ask_json` on those bytes. The schema is `seen`, and `y` and `x` when she sees it. A seen result with both numbers stores `screen_px` as the aim and appends it to the points. The tool result is `that place is ready` or `it is not on the screen`. She is not given the pixel.
- `click` presses the aim. `how` is `left`, `right`, or `double`, and the default is left. No aim returns `look first` and does not press. `aim` moves the cursor. If `GetCursorPos` is more than 2 pixels off, the button does not go down and the result is that cursor. Otherwise `strike`.
- `drag` strokes from the previous point to the last. Fewer than two points does not stroke.
- `type_text`, `press`, and `run` act, including while she is alone.
- `remember` appends one fact.
- `call_owner` dials when the line is down. A failed dial returns `he did not answer` and the exception. Python does not consult for her. When he is on the line, `opening` is spoken and the result says he can see the screen. She can keep using tools.
- `hang_up` ends the call. The process stays up. She can keep using tools.
- `consult` is how she spawns the agent. `why` is spoken when the line is up, before the agent runs. `attach` is `screen`, `part`, or `no`. `part` with no aim returns `look first` and does not spawn. `screen` writes `picture()` to `consult/screen.png`. `part` writes `around` that picture at the aim. A stale `screen.png` is removed first. The prompt tells the agent who she is, her tools, the memory block, his request, and her question, and it says the reply will be read as his next request. That prompt, the picture, and the reply go through `mirror`. The return is a `UserTurn`.

`main()`. `say TEXT` appends that line to `state/inbox.txt`. `call`, `hang`, and `stop` append `/call`, `/hang`, or `/stop`. No command builds `Trident`, starts it, and waits. `stop()` always runs afterward.

## install.py

Creates `.venv` when needed, installs torch and torchaudio from `install.torch_index`, then `requirements.txt`. Fetches llama.cpp tag `b11371` when `bin/llama/llama-server.exe` is missing. That tag is the one with the Windows CUDA 12.4 zip. The GitHub latest release does not ship it. Fetches NeMo Speech when its exe is missing. Downloads Gemma, its mmproj, Qwen2.5-VL-3B, its mmproj, the ear model, and `models/silero_vad.onnx`. Qwen is not loaded by a running Trident.

## requirements.txt

`numpy`, `Pillow`, `onnxruntime==1.20.1`, `huggingface_hub`, `chatterbox-tts`, `telethon==1.45.0`, `ntgcalls==3.0.0`, `opentele-ng==1.4.0`.

## reference.wav

Tracked binary (`.gitattributes`). Present at the repo root. `mouth.reference` points here.

## .gitignore

Ignores `.venv/`, `bin/`, `models/`, `state/`, `__pycache__/`.

## .gitattributes

`* text=auto eol=lf`. `reference.wav binary`.

## LICENSE

MIT. Copyright (c) 2026 Gianfranco Cordella. The file also names ggml (MIT, 2023-2026, The ggml authors) and Chatterbox / Chatterbox Turbo (MIT, 2025, Resemble AI).
