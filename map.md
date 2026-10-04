# map

One process. `trident.py` joins the organs. `install.py` fetches binaries and weights. Every organ reads `config.toml` through `organs.CONFIG`. `reference.wav` is tracked, binary, and present. This map did not read the audio.

Tracked files: `.gitattributes`, `.gitignore`, `LICENSE`, `config.toml`, `install.py`, `map.md`, `organs/__init__.py`, `organs/brain.py`, `organs/ears.py`, `organs/eyes.py`, `organs/hands.py`, `organs/memory.py`, `organs/mouth.py`, `organs/telegram.py`, `reference.wav`, `requirements.txt`, `trident.py`.

## config.toml

`[owner]` `telegram_id` = 5884279027, `name` = Wojciech. Read by `trident` (`name`) and `telegram` (`telegram_id`).

`[paths]` `models` = models, `bin` = bin, `state` = state. Read by `organs`, `install`, `brain`.

`[brain]` `model` = gemma-4-E2B-it-Q4_0.gguf, `mmproj` = mmproj-gemma-4-E2B-it-Q8_0.gguf, `host` = 127.0.0.1, `port` = 8080, `context` = 16384, `slots` = 2, `gpu_layers` = 999, `threads` = 4, `image_tokens` = 1120, `ubatch` = 2048, `temperature` = 0, `top_k` = 64, `top_p` = 0.95, `min_p` = 0.05, `max_tokens` = 4096, `max_tool_steps` = 200. Read by `brain`. `model` and `mmproj` also by `install`.

`[eyes]` `max_side` = 1920. Read by `eyes`.

`[vision]` `model` = Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf, `mmproj` = mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf, `side` = 1920, `context` = 4096, `slots` = 1, `ubatch` = 1024, `image_tokens` = 1024. Read by `brain` and `trident` (`side`). The two files also by `install`.

`[cloud]` `model` = gpt-5.6-luna-none. Read by `trident`. Luna is the consult model. Grok 4.7 extra-high is not asked on every consult.

`[ears]` `vad_threshold` = 0.65, `min_silence_ms` = 700, `pad_ms` = 120, `min_utterance_s` = 1.0, `model` = nemotron-3.5-asr-streaming-0.6b.q8_0.gguf. Read by `ears`. `model` also by `install`.

`[mouth]` `device` = cuda, `reference` = reference.wav. Read by `mouth`.

`[telegram]` `desk_video` = true, `desk_fps` = 12. Read by `telegram`.

`[install]` `llama_tag` = b11371, `llama_asset` = bin-win-cuda-12.4-x64.zip, `cudart_asset` = cudart-llama-bin-win-cuda-12.4-x64.zip, `nemo_url` = https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.1.0/nemo-speech-0.1.0-windows-x86_64-vulkan.zip, `gemma_url` = https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_0.gguf, `mmproj_url` = https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF/resolve/main/mmproj-gemma-4-E2B-it-Q8_0.gguf, `vision_url` = https://huggingface.co/ggml-org/Qwen2.5-VL-3B-Instruct-GGUF/resolve/main/Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf, `vision_mmproj_url` = https://huggingface.co/ggml-org/Qwen2.5-VL-3B-Instruct-GGUF/resolve/main/mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf, `ear_url` = https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/resolve/main/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf, `silero_url` = https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx, `torch_index` = https://download.pytorch.org/whl/cu124. Read by `install`.

## Vision budget

Gemma 4 image budgets are 70, 140, 280, 560, and 1120. 1120 is the maximum, about 2.6 million pixels. A 1920×1080 screen is 2.07 million pixels, so 1120 holds the whole picture and 560 does not. The vision tokens use non-causal attention, so the image batch has to fit in one micro-batch. `ubatch` is 2048. llama-server's default `--batch-size` is 2048, so the 1120 batch fits. A 1120-token image can encode as slightly more than 1120 tokens, and 1024 does not hold that.

A 20-pixel icon is smaller than one 1120-token cell on a 1920×1080 frame. `interactive_controls` reads that window. `overlay` draws a cyan box on the screen and the name on the list below it.

One model is on the GPU at a time. Gemma, Qwen, the mouth, and the ear each load, run, and exit before the next one loads. The reload is the cheap part. Two of them resident at once spill this GTX 1060 into RAM and the looks crawl. `look` and `crop` use the Gemma server. `survey` stops it, loads Qwen, then stops Qwen and loads Gemma again. Qwen reads the raw screenshot, longest side `eyes.max_side`, encoded at `vision.image_tokens` 1024. The marks are drawn after that look, below the screen.

Qwen2.5-VL answers in absolute pixels, x then y, of the picture llama.cpp actually encodes. `--image-min-tokens` and `--image-max-tokens` are both 1024. On a 1920×1080 frame the float32 smart-resize used by this build (b11371) is 1176×672, 1008 tokens, with the picture letterboxed inside that canvas. Python converts those pixels through that canvas onto Gemma's 1000 grid, y then x. A picture whose encoded size would pass 1024 tokens is scaled down first, so the batch stays inside `vision.ubatch` 1024. The 1024 cap is unchanged: the same frame still uses about 4.7GB dedicated and returns in a few seconds. `stop` drops the device back to the desktop.

## organs/__init__.py

Talks to `config.toml` and the `state/` directory. Talked to by `brain`, `ears`, `eyes`, `hands`, `memory`, `mouth`, `telegram`, `trident`.

`ROOT` is the repo root. `CONFIG` is `config.toml` parsed at import.

`path_of(section, key) -> Path`. `brain`, `ears`, and `vision` resolve under `paths.models`. Any other section resolves under the repo root. Returns that directory plus `CONFIG[section][key]`.

`state_dir() -> Path`. Creates `paths.state` and returns it.

`log(name) -> logging.Logger`. First call with no root handlers sets INFO, format `%(asctime)s %(name)-8s %(message)s`, time `%H:%M:%S`, stderr plus `state/trident.log` (utf-8). Later calls return the named logger.

## organs/brain.py

Talks to `organs` (`CONFIG`, `ROOT`, `log`, `path_of`, `state_dir`), `bin/llama/llama-server.exe`, and HTTP `http://{host}:{port}`. Talked to by `trident` (`Brain`, `Tool`) and `python -m organs.brain`. Logger name `brain`. Import calls `log("brain")`, which creates `state/` and opens the log.

Tokens: `BOS` `<bos>`, `TURN_OPEN` `<|turn>`, `TURN_CLOSE` `<turn|>`, `THINK` `<|think|>`, `QUOTE` `<|"|>`, `TOOL_RESPONSE_OPEN` `<|tool_response>`, `TOOL_RESPONSE_CLOSE` `<tool_response|>`. The image marker is whatever the running server reports. `Brain.media()` reads `media_marker` from `GET {url}/props` on first use and stores it on `Brain.marker`. `stop()` clears it.

`Tool(name, description, params, run, optional=(), final=False)`. `params` maps a name to `description`, `type` (`STRING`, `INTEGER`, `NUMBER`, or `BOOLEAN`), and optional `enum`. `optional` names may be omitted. `final` ends the turn with empty spoken text. `run` is `Callable[..., object]`.

`Step(thought, tool, args)`. `Reply(text)`.

`quoted`, `literal`, `declare`, `tool_response`, `turn`, `plain`, and `parse` build and read Gemma's tool text. `declare` sorts keys, quotes descriptions, and emits the `<|tool>declaration:...` block with a space before `},required` and a space before the final `}`. A dict tool result is emitted with sorted keys. `parse` reads one `<|tool_call>call:NAME{...}<tool_call|>`. Quoted values stay strings. Bare `true`/`false` become bools. Bare integers and decimals become `int` and `float`.

`Brain.__init__()`. `url` from `brain.host` and `brain.port`. `proc` is `None`. `marker` is `None` until `media()`.

`Brain.alive() -> bool`. GET `{url}/health`, timeout 2. True when status is 200. `URLError` or `OSError` returns False.

`Brain.start(section="brain")`. Returns immediately when `alive()`. Otherwise runs `bin/llama/llama-server.exe`. `section="brain"` uses `paths` from `path_of("brain", ...)`, `brain.context`, `brain.slots`, `brain.ubatch`, and `brain.image_tokens`. Any other section uses `path_of(section, ...)`, and `vision.context`, `vision.slots`, `vision.ubatch`, and `vision.image_tokens`. Both sections set `--image-min-tokens` and `--image-max-tokens` to that count. Shared flags: `--host`, `--port`, `--n-gpu-layers`, `--threads`, `--flash-attn off`, `--cache-type-k f16`, `--cache-type-v f16`, `--no-webui`, `--log-file state/llama-server.log`. Working directory is the exe directory. Stdio is discarded. Window is hidden. Missing exe raises `FileNotFoundError`. Polls 0.5 s for up to 300 s. Process exit raises `RuntimeError`. Timeout raises `TimeoutError`. On this binary, `--ctx-size 16384 --parallel 2` comes up as `n_slots = 2`, `n_ctx_slot = 8192`.

`Brain.stop()`. If `proc` is still running, `terminate()` and `wait(10)`, then `kill` if it is still up. A llama-server that was already listening is ended too, so the next model can load. Waits until the port is down, at most 20 s. Sets `proc` and `marker` to `None`.

`Brain.survey(png, question) -> list`. `stop()`, then `start("vision")`. The slot is erased before each look, when that route exists. Qwen2.5-VL is asked for `bbox_2d` as x1, y1, x2, y2 in pixels and `label` for what is drawn. Python maps those pixels through the llama.cpp canvas onto the 1000 grid, y then x. The first look is the whole picture. Each of up to 32 boxes is padded by 80 on that grid, cropped without the Gemma upscale, and looked at again. The closer box is mapped back with `embed`. A closer box with no area keeps the first box. Absent means an empty list. Sampling uses `temperature`, `top_k`, `top_p`, and `min_p`. The vision reply may run to 1536 tokens. Timeout 600. A schema rejection is asked once more without the schema. Logs `see` per look and `survey` with the names. `finally` calls `stop()` and `start()` so Gemma is the server again.

`Brain.complete(prompt, images=(), schema=None, stop=()) -> str`. POST `{url}/completion`, timeout 600. Body: `prompt` (a string, or `{prompt_string, multimodal_data}` of base64 images when `images` is non-empty), `n_predict` `brain.max_tokens`, `cache_prompt` false when `images` is non-empty, otherwise true, `stop`, and the same sampling fields. `schema` sets `json_schema`. Writes the prompt to `state/last_prompt.txt`. When `images` is non-empty, writes the first of those bytes to `state/last_image.png` before the POST. Returns `content`. Logs elapsed seconds plus `timings.prompt_n` and `timings.predicted_n` under the word `gemma`.

`Brain.ask_json(question, schema, png=None) -> object`. Optional image, given schema, thinking off. An image prefixes the question with `media()`. Returns `json.loads` of the completion. A point in that object is on the 1000-grid of that image. Pixel conversion is outside this file.

`Brain.think(system, tools, history, user, on_step=None) -> Reply`. No image. System turn is `THINK`, `system`, and every `declare(tool)`. History is user/model turn pairs. Then the user turn and an open model turn. Up to `max_tool_steps` completions, stopped on the tool-response open token and `TURN_CLOSE`. A completion with no tool calls `on_step` with an empty tool name. If that returns text, the text is appended as the next user turn and the loop continues. Otherwise it returns `Reply(plain(out))`. An unknown tool name yields `unknown tool {name}` and the loop continues. Any exception from `run` yields `bad arguments: {exc}` and the loop continues. Each tool call builds a `Step` and, when `on_step` is set, calls it before `run`. A `final` tool returns `Reply("")` after it runs. Otherwise the completion and `tool_response` are appended and the loop continues. Exhausting the steps returns `Reply("I am still working on it.")`.

`main()`. `Brain.start()`, then `ask_json` on the command-line question, or `What is the capital of France and what are its GPS coordinates?` when the command line is empty. Schema requires `capital` (string), `latitude` (number), `longitude` (number). Prints that JSON and `think("You are Gemma. Answer in one short sentence.", {}, [], question).text`. Does not call `stop()`.

## organs/ears.py

Talks to `organs` (`CONFIG`, `ROOT`, `path_of`), `models/silero_vad.onnx` through onnxruntime, and `bin/nemo-speech/bin/nemo-speech.exe`. Talked to by `trident` (`transcribe`, `write_wav`) and `telegram` (`Segmenter`). `python -m organs.ears FILE.wav` prints `transcribe` of that path.

`RATE` = 16000. `WINDOW` = 512.

`Segmenter` loads Silero with `CPUExecutionProvider`. `pad`, `min_silence`, and `min_len` come from `pad_ms`, `min_silence_ms`, and `min_utterance_s`. `push` returns a finished utterance at 16 kHz, or `None`. Silence below `vad_threshold - 0.15` ends speech after `min_silence` samples. A clip shorter than `min_len` is dropped.

`write_wav(path, samples, rate=16000) -> Path`. Mono 16-bit wav, samples clipped to [-1, 1].

`transcribe(wav) -> (text, language)`. Runs `nemo-speech.exe transcribe` with `--device vulkan --format json --verbatim --quiet --endpointing=true --stop-history-eou-ms 1200`. Non-zero exit raises `RuntimeError`. Text is NFC, `<lang>` tags removed. Polish letters `ąćęłńóśźż` force `language` to `pl`.

## organs/eyes.py

Talks to `organs` (`CONFIG`, and `state_dir` in `__main__`), `user32`, and PIL `ImageGrab`, `ImageDraw`, and `ImageFont`. Talked to by `trident.tools`. At import, `SetProcessDpiAwarenessContext(-4)`. There is no module docstring.

`screen_size() -> (width, height)`. `GetSystemMetrics(0)`, `GetSystemMetrics(1)`.

`screenshot() -> bytes`. `ImageGrab.grab()` with no arguments. Pillow's `all_screens` default is false, so this is the primary monitor. Longest side at most `eyes.max_side` (LANCZOS). RGB PNG, `compress_level` 1.

`crop(png, box, upscale=True) -> bytes`. `box` is `[y0, x0, y1, x1]` on the 1000-grid. When `upscale` is true, a crop whose longest side is under 512 is scaled by 4 with `Image.NEAREST`. RGB PNG, `compress_level` 1.

`overlay(png, box=None, notes=None) -> bytes`. Crops when `box` is set, with the same geometry as `crop`. Cyan boxes for named controls and for `notes` stay on the screen. The list, each line `y` then `x` then the name on that screen's 1000 grid, is drawn below a cyan line, outside the screen. Then the pointer, on the screen only. `GetCursorPos` is the point even when Windows is not showing a cursor. Slim red lines of width 1 cross at that point, a white arrow with a black outline has its tip there, and yellow text at 32px on a black plate reads `y` then `x`. That pointer text is not a list line. Gemma 4 names a box `[y, x, y, x]` on a 1000×1000 grid. A crop that does not contain the point is returned without that mark. A control outside the crop is not drawn. The bytes passed in are not changed.

`embed(box, inner) -> list`. `inner` is a box on the crop's 1000-grid. Returns that box on the full 1000-grid.

`center_px(box_2d) -> (x, y)` and `point_px(box, y, x) -> (x, y)` convert the 1000-grid through `screen_size()`.

`python -m organs.eyes` writes `state/screen.png` and prints that path and `screen_size()`.

## organs/hands.py

Talks to `user32.SendInput`, `powershell.exe`, and `state_dir` when listing controls. Talked to by `trident.tools` and `eyes.overlay`. At import, `SetProcessDpiAwarenessContext(-4)`.

`aim(x, y) -> (x, y)`. `SetCursorPos`, then an absolute move, then `GetCursorPos`. `strike(x, y, how)` presses. `click(x, y, how="left")` aims, then strikes, and returns the cursor position from before the press. `right` uses the right button. `double` adds a second left down/up. Any other `how` uses the left button.

`drag(x0, y0, x1, y1)`. Left down, ten moves 0.02 s apart, left up.

`press(keys)`. Space-separated chords. `+` and `-` split a chord. An unknown name raises `KeyError`.

`type_text(text)`. A character the foreground layout can type is that virtual key, with shift, ctrl, or alt when the layout asks for them. Anything else is a Unicode key, one UTF-16 unit at a time.

`interactive_controls(title="") -> list`. Named interactive controls on one window: `(name, x, y, width, height)` in screen pixels. An empty title is the foreground window. A title such as `Untitled - Paint` reads that window when it is not in front. A browser, a dialog, or another program is the same call with that window's title. A repeated name keeps the smaller rectangle. The PowerShell is written to `state/controls.ps1`, run, and deleted.

`run(command, timeout=25) -> str`. Hidden `powershell.exe`, the `start` alias removed. A command that is only a name, when that name is a zero-byte file, is `Start-Process -FilePath` and that name. Timeout returns `timed out`. A command whose stripped lowercase text starts with `start-process` waits 1.5 s. Empty output returns `exit {code}` when the code is non-zero, otherwise `ok`.

`python -m organs.hands` accepts `press`, `type`, `click`, and `run`. No `drag` verb.

## organs/memory.py

`state/memory.json` holds `facts`, `task`, and `turns`. `remember` appends a new stripped fact. `add_turn` appends both sides. `set_task` stores the latest chat, call, or typed request. A consult reply does not replace the task. `facts_block` is empty when there are no facts. Every Gemma request appends that block. A wish not to be bothered is a fact, and she still decides. A file that still has `quiet` true gains that fact. `history` returns one pair when `task` is set: that task and her latest reply. An empty task leaves the slot empty. The file is not scanned to invent a task. The slot is 8192 tokens. The rest of the day stays in the file.

## organs/mouth.py

`python -m organs.mouth` reads the text on stdin, loads `ChatterboxTurboTTS` on `mouth.device`, calls `prepare_conditionals` on `path_of("mouth", "reference")`, which is `reference.wav`, writes pcm48 on stdout, and exits. That exit is what gives the GPU back. `speakable` keeps letters, digits, punctuation, and the tags `laugh`, `chuckle`, `sigh`, `gasp`, `cough`, `clear throat`, `sniff`, `groan`. `pieces` yields 24 kHz float32 audio: the first yield is about ten seconds, and a second yield is the rest when words remain. `pcm48` resamples to 48000 Hz signed 16-bit mono. Nothing is played on the PC speakers. The trident process does not import torch.

## organs/telegram.py

`Line` is the Telegram voice line. `OWNER` is `owner.telegram_id`. Capture is 48000 Hz. Playback is 16000 Hz into `Segmenter`. `desk_video` sends the primary monitor as a 960×540 camera at `desk_fps`. `start` quits Telegram Desktop, opens the first `tdata` account whose `UserId` is not `OWNER`, and reaches `idle`. `stop` hangs up when the line is up, disconnects, and starts Desktop again. `send_text`, `send_photo`, and `send_file` return immediately when `client` is `None`. `send_text` sends the whole string, in pieces of 4000 characters when the thought is longer than one Telegram message. `speak` requires the line to be `up` and paces 10 ms microphone frames. PC mic and speakers are not opened.

`python -m organs.telegram` connects and answers a call without sending speech.

## trident.py

`OWNER` is `owner.name`. `INBOX` is `state/inbox.txt`. `SOURCE` maps `chat` and `consult` to a request, `call` to his voice, and `typed` to a line on the computer. A consult answer is queued as `consult`. The label she reads is still a request. The situation says the line is `up` or `down`.

`SYSTEM` is three sentences. She reads the request in full, then every tool description in full, then calls the one tool that moves one step closer to the goal. Tool descriptions are parallel and short. `max_tokens` 4096 is the room for that thought. `facts_block` is appended to every request.

`PASS` is the look schema: `answer` and `confident` are required. `y` and `x` are optional. A confident look that names one mark uses that mark's center. Otherwise `y` and `x` are the place.

`consult_prompt` asks GPT for one JSON object and no other text, key `request`. The advisor is on this machine and may use the internet. `request` names the next action, with no pixel and no mention of a model. `ask_cursor` runs the latest `%LOCALAPPDATA%\cursor-agent\versions\<date>-<hash>` directory that contains `node.exe`. A name that does not start with a digit, including `dist-package`, is skipped. The command is that directory's `node.exe` and `index.js`, print mode, `--force`, `--sandbox disabled`, `--trust`, `--model` `cloud.model`, text output, workspace `state/consult`. There is no `--mode ask`. It is a local process, not a VM. The stdout is parsed with `json.loads` and the `request` string is returned. Non-zero exit, invalid JSON, or an empty `request` raises `RuntimeError`.

`Trident.start` calls `brain.start()`, `line.start()`, then the worker and inbox threads. `stop` sets `stopping`, then `line.stop()` and `brain.stop()`. A call stops Gemma before `transcribe` and starts Gemma again after, because the ear uses the GPU and then exits. `speak` stops Gemma, runs `python -m organs.mouth`, plays the pcm, and starts Gemma again.

`inbox_loop` sends `state/inbox.txt` to Telegram as a file, then queues each non-empty line as `typed`. A line that starts with `/` is `call`, `hang`, or `stop`. Nothing queues the task again when the line is down. She continues only by calling another tool in the turn she was given. Chat, call, and typed requests become `task` after the turn. A consult does not.

`on_step` runs before the tool, and again when she tries to stop. It sends the thought, the tool name, and the arguments as JSON, uncut, with `line.send_text`. If the last click or drag was refused, stopping is not the end of the turn: she is told the click did not happen. If she acted and has not looked since, stopping is not the end either: she is told to look again. A spoken reply that is allowed to end the turn is said on an open call, and sent as text when the line is down.

Tools:

- `look` screenshots, overlays the pointer and the list below the screen, sends that picture, then `ask_json` on the same bytes. A confident result whose answer names one stored mark sets the aim to that mark's center. Otherwise a confident result with `y` and `x` sets the aim to that point. Any other result returns no pixel and clears the aim. Each pixel is kept for the rest of the turn.
- `survey` screenshots the raw picture, asks `brain.survey`, stores those marks, draws them on that picture, sends it, and returns the names. A click or a drag that lands clears the marks. A new turn clears them too.
- `click` aims and writes `state/aim.png`. A click with no aim, or whose pixel is not the aim, does not press. If the cursor is more than 2 pixels off the point, the picture is sent and the button does not go down. Otherwise the press happens, then the picture is sent. She cannot end the turn on a refusal, and after a press she cannot end it until she looks again.
- `drag` starts at the latest pixel. Any other start does not stroke. An end she was not given does not stroke. `type_text`, `press`, and `run` act, including while she is alone.
- `remember` appends one fact.
- `call_owner` dials. If he does not answer, the text says so. When he answers, the opening is spoken and he can see the screen.
- `hang_up` is `final`. The process stays up.
- `consult` is `final`. `why`, `question`, and optional `image`. `image` true attaches the whole screen. That picture is sent to him and written to `state/consult/screen.png`. The reply, or the failure, is sent as text and queued as `consult`. The advisor runs on this machine with the internet, not in ask mode.

`main()`. `say TEXT` appends that line to `state/inbox.txt`. `call`, `hang`, and `stop` append `/call`, `/hang`, or `/stop`. No command builds `Trident`, starts it, and waits. `stop()` always runs afterward.

## install.py

Creates `.venv` when needed, installs torch and torchaudio from `install.torch_index`, then `requirements.txt`. Fetches llama.cpp tag `b11371` when `bin/llama/llama-server.exe` is missing. That tag is the one with the Windows CUDA 12.4 zip. The GitHub latest release does not ship it. Fetches NeMo Speech when its exe is missing. Downloads Gemma, its mmproj, Qwen2.5-VL-3B, its mmproj, the ear model, and `models/silero_vad.onnx`.

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
