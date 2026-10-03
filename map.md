# map

One process. `trident.py` joins the organs. `install.py` fetches binaries and weights. Every organ reads `config.toml` through `organs.CONFIG`. `reference.wav` is tracked, binary, and present.

Tracked files: `.gitattributes`, `.gitignore`, `LICENSE`, `config.toml`, `install.py`, `organs/__init__.py`, `organs/brain.py`, `organs/ears.py`, `organs/eyes.py`, `organs/hands.py`, `organs/memory.py`, `organs/mouth.py`, `organs/telegram.py`, `reference.wav`, `requirements.txt`, `trident.py`.

## config.toml

`[owner]` `telegram_id` = 5884279027, `name` = Wojciech. Read by `trident` (`name`) and `telegram` (`telegram_id`).

`[paths]` `models` = models, `bin` = bin, `state` = state. Read by `organs`, `install`, `brain`.

`[brain]` `model` = gemma-4-E2B-it-Q4_0.gguf, `mmproj` = mmproj-gemma-4-E2B-it-Q8_0.gguf, `host` = 127.0.0.1, `port` = 8080, `context` = 16384, `slots` = 2, `gpu_layers` = 999, `threads` = 4, `image_tokens` = 560, `temperature` = 0, `top_k` = 64, `top_p` = 0.95, `min_p` = 0.05, `max_tokens` = 1024, `max_tool_steps` = 20, `idle_after` = 30. Read by `brain` and, for `idle_after`, `trident`. `model` and `mmproj` also by `install`.

`[eyes]` `max_side` = 1920, `cloud_side` = 480. Read by `eyes`. `cloud_side` is the longest side of a picture for a cloud look.

`[cloud]` `model` = gpt-5.6-luna-none. Read by `trident`.

`[ears]` `vad_threshold` = 0.65, `min_silence_ms` = 700, `pad_ms` = 120, `max_utterance_s` = 30, `min_utterance_s` = 1.0, `model` = nemotron-3.5-asr-streaming-0.6b.q8_0.gguf. Read by `ears`. `model` also by `install`.

`[mouth]` `device` = cpu, `reference` = reference.wav. Read by `mouth`.

`[telegram]` `desk_video` = true, `desk_fps` = 12. Read by `telegram`.

`[install]` `llama_tag` = b11371, `llama_asset` = bin-win-cuda-12.4-x64.zip, `cudart_asset` = cudart-llama-bin-win-cuda-12.4-x64.zip, `nemo_url` = https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.1.0/nemo-speech-0.1.0-windows-x86_64-cpu.zip, `gemma_url` = https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_0.gguf, `mmproj_url` = https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF/resolve/main/mmproj-gemma-4-E2B-it-Q8_0.gguf, `ear_url` = https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/resolve/main/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf, `silero_url` = https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx, `torch_index` = https://download.pytorch.org/whl/cpu. Read by `install`.

## organs/__init__.py

Talks to `config.toml` and the `state/` directory. Talked to by `brain`, `ears`, `eyes`, `memory`, `mouth`, `telegram`, `trident`. `hands` does not import it.

`ROOT` is the repo root. `CONFIG` is `config.toml` parsed at import.

`path_of(section: str, key: str) -> Path`. `brain` and `ears` resolve under `paths.models`. `mouth` and any other section resolve under the repo root. Returns that directory plus `CONFIG[section][key]`.

`state_dir() -> Path`. Creates `paths.state` and returns it.

`log(name: str) -> logging.Logger`. First call with no root handlers sets INFO, format `%(asctime)s %(name)-8s %(message)s`, time `%H:%M:%S`, stderr plus `state/trident.log` (utf-8). Later calls return the named logger.

## organs/brain.py

Talks to `organs` (`CONFIG`, `ROOT`, `log`, `path_of`, `state_dir`), `bin/llama/llama-server.exe`, and HTTP `http://{host}:{port}`. Talked to by `trident` (`Brain`, `Tool`) and `python -m organs.brain`. Logger name `brain`. Import calls `log("brain")`, which creates `state/` and opens the log.

Tokens: `BOS` `<bos>`, `TURN_OPEN` `<|turn>`, `TURN_CLOSE` `<turn|>`, `THINK` `<|think|>`, `QUOTE` `<|"|>`, `TOOL_RESPONSE_OPEN` `<|tool_response>`, `TOOL_RESPONSE_CLOSE` `<tool_response|>`. The image marker is whatever the running server reports. `Brain.media()` reads `media_marker` from `GET {url}/props` on first use and stores it on `Brain.marker`.

`Tool(name, description, params, run, optional=(), final=False)`. `params` maps a name to `description`, `type` (`STRING`, `INTEGER`, `NUMBER`, or `BOOLEAN`), and optional `enum`. `optional` names may be omitted. `final` ends the turn with empty spoken text. `run` is `Callable[..., object]`.

`Step(thought: str, tool: str, args: dict, result: object)`.

`Reply(text: str, steps: list[Step])`. `steps` defaults to `[]`.

`quoted(text: object) -> str`. Wraps `str(text)` in `QUOTE`. A `QUOTE` inside the text becomes `'`.

`literal(value: object) -> str`. `bool` to `true`/`false`, `int` and `float` to decimal text, `dict` to `{k:v}` with sorted keys, `list` and `tuple` to `[v]`, anything else to `quoted`.

`declare(tool: Tool) -> str`. Keys sorted. `required` is every param name not in `optional`. Each property is `name:{description:quoted,enum:[quoted,...],type:quoted}`, and `enum` is omitted when absent. The return is `<|tool>declaration:NAME{description:quoted,parameters:{properties:{...}} },required:[quoted,...],type:<|"|>OBJECT<|"|>} }<tool|>`, including the space before `},required` and the space before the final `}`.

`tool_response(name: str, result: object) -> str`. A dict result is the body, with keys sorted by `literal`. Any other result is `{value: ...}` using `literal`. Wrapped in the tool-response tokens.

`turn(role: str, body: str) -> str`. `<|turn>{role}\n{body}<turn|>\n`.

`plain(text: str) -> str`. Drops the thought channel, the tool call, and control tokens, then collapses whitespace.

`parse(output: str) -> tuple[str, str, dict]`. Thought text from `<|channel>thought ... <channel|>`, a tool name from `<|tool_call>call:NAME{...}<tool_call|>`, and that call's args. No call returns `(thought, "", {})`. Quoted arg values stay strings. Bare `true`/`false` become bools. Bare integers and decimals become `int` and `float`.

`Brain.__init__()`. `url` from `brain.host` and `brain.port`. `proc` is `None`. `marker` is `None` until `media()`.

`Brain.alive() -> bool`. GET `{url}/health`, timeout 2. True when status is 200. `URLError` or `OSError` returns False.

`Brain.start()`. Returns immediately when `alive()`. Otherwise runs `bin/llama/llama-server.exe` with `--model` and `--mmproj` from `path_of("brain", ...)`, `--host`, `--port`, `--ctx-size` `context`, `--parallel` `slots`, `--n-gpu-layers` `gpu_layers`, `--threads`, `--flash-attn off`, `--cache-type-k f16`, `--cache-type-v f16`, `--image-min-tokens` and `--image-max-tokens` both `image_tokens`, `--no-webui`, `--log-file state/llama-server.log`. Working directory is the exe directory. Stdio is discarded. Window is hidden. Missing exe raises `FileNotFoundError`. Polls 0.5 s for up to 300 s. Process exit raises `RuntimeError`. Timeout raises `TimeoutError`.

`Brain.stop()`. If `proc` is still running, `terminate()` and `wait(10)`. Sets `proc` to `None`. A server that was already up at `start()` is left running.

`Brain.complete(prompt: str, images: list[bytes] = (), schema: dict | None = None, max_tokens: int | None = None, stop: list[str] = ()) -> str`. POST `{url}/completion`, timeout 600. Body: `prompt` (a string, or `{prompt_string, multimodal_data}` of base64 images when `images` is non-empty), `n_predict` (`max_tokens` or `brain.max_tokens`), `cache_prompt` true, `stop`, and sampling from `temperature`, `top_k`, `top_p`, `min_p`. `schema` sets `json_schema`. Writes the prompt to `state/last_prompt.txt`. Returns `content`. Logs elapsed seconds plus `timings.prompt_n` and `timings.predicted_n`.

`Brain.ask_json(question: str, schema: dict, png: bytes | None = None) -> object`. Optional image, given schema, `max_tokens` 400, thinking off. An image prefixes the question with `media()`. Returns `json.loads` of the completion. A point in that object is on the 1000-grid of that image. Pixel conversion is outside this file.

`Brain.think(system: str, tools: dict[str, Tool], history: list[tuple[str, str]], user: str, on_step: Callable[[Step], None] | None = None) -> Reply`. No image. System turn is `THINK`, `system`, and every `declare(tool)`. History is user/model turn pairs. Then the user turn and an open model turn. Up to `max_tool_steps` completions, stopped on the tool-response open token and `TURN_CLOSE`. A completion with no tool returns `Reply(plain(out), steps)`. An unknown tool name yields `unknown tool {name}` and the loop continues. Any exception from `run` yields `bad arguments: {exc}` and the loop continues. Each call appends a `Step` and, when `on_step` is set, calls it. The log line for a thought, a tool call, and a spoken reply is the full text. A `final` tool returns `Reply("", steps)` after it runs. Otherwise the completion and `tool_response` are appended and the loop continues. Exhausting the steps returns `Reply("I am still working on it.", steps)`.

`main()`. `Brain.start()`, then `ask_json` on the command-line question, or `What is the capital of France and what are its GPS coordinates?` when the command line is empty. Schema requires `capital` (string), `latitude` (number), `longitude` (number). Prints that JSON and `think("You are Gemma. Answer in one short sentence.", {}, [], question).text`. Does not call `stop()`.

## organs/ears.py

Talks to `organs` (`CONFIG`, `ROOT`, `path_of`), `models/silero_vad.onnx` through onnxruntime, and `bin/nemo-speech/bin/nemo-speech.exe`. Talked to by `trident` (`transcribe`, `write_wav`) and `telegram` (`Segmenter`). `python -m organs.ears FILE.wav` prints `transcribe` of that path.

`RATE` = 16000. `WINDOW` = 512.

`Segmenter.__init__()`. Loads the onnx file with `CPUExecutionProvider`. `pad`, `min_silence`, `max_len`, and `min_len` come from `pad_ms`, `min_silence_ms`, `max_utterance_s`, and `min_utterance_s`. Calls `reset()`.

`Segmenter.reset()`. Clears RNN state `(2, 1, 128)` float32, a 64-sample context, `pending`, `lead`, `speech`, `talking`, and `silent_for`.

`Segmenter._prob(hop: np.ndarray) -> float`. One Silero hop. Updates `context` and `state`. Returns the speech probability.

`Segmenter.push(samples: np.ndarray) -> np.ndarray | None`. Appends float32 samples. Consumes 512-sample hops. Before speech, keeps the last `pad // WINDOW + 1` hops and starts an utterance when probability reaches `vad_threshold`. During speech, silence counts when probability is below `vad_threshold - 0.15`. An utterance ends after `min_silence` quiet samples or at `max_len`. State is zeroed. A clip shorter than `min_len` is dropped. Returns the last clip in that `push` that reached `min_len`, or `None`.

`write_wav(path: Path, samples: np.ndarray, rate: int = 16000) -> Path`. Mono 16-bit wav, samples clipped to [-1, 1]. Returns `path`.

`transcribe(wav: Path) -> tuple[str, str]`. Runs `nemo-speech.exe transcribe WAV --model {ears model} --device cpu --format json --verbatim --quiet --endpointing=true --stop-history-eou-ms 1200`. Hidden window. Non-zero exit raises `RuntimeError` with stderr. Returns `(text, language)`. Text is NFC, with `<lang>` tags removed, whitespace collapsed. `language` is the recognizer tag before `-`, lowercased. Polish letters `ąćęłńóśźż` in the text set `language` to `pl`.

## organs/eyes.py

Talks to `organs` (`CONFIG`, and `state_dir` in `__main__`), `user32`, and PIL `ImageGrab`. Talked to by `trident.tools`. At import, `SetProcessDpiAwarenessContext(-4)`, and `GetForegroundWindow` returns a pointer.

`screen_size() -> tuple[int, int]`. `GetSystemMetrics(0)`, `GetSystemMetrics(1)`.

`screenshot() -> bytes`. `ImageGrab.grab()` with no arguments. The module docstring calls that the primary monitor. Longest side at most `eyes.max_side` (LANCZOS). RGB PNG, `compress_level` 1.

`shrink(png: bytes) -> bytes`. Longest side at most `eyes.cloud_side` (LANCZOS). Smaller pictures stay that size. RGB PNG, `compress_level` 1. That size is 480. On this 1920 by 1080 desktop, 480 by 270 still showed the Start icon, the clock, and the status text. 360 by 203 left the clock digits unread.

`crop(png: bytes, box: list) -> bytes`. `box` is `[y0, x0, y1, x1]` on the 1000-grid. Crops that rectangle out of `png`. RGB PNG, `compress_level` 1.

`center_px(box_2d: list) -> tuple[int, int]`. `box_2d` is `[y0, x0, y1, x1]` on the 1000-grid. Returns `(x, y)` from `screen_size()`: `round((x0+x1)/2000*(width-1))`, `round((y0+y1)/2000*(height-1))`.

`point_px(box: list, y: float, x: float) -> tuple[int, int]`. `box` is a crop on the full 1000-grid. `y` and `x` are a point on the 1000-grid of that crop. Maps the point through the crop's min and max edges, then `center_px`.

`window_titles(limit: int = 8) -> list[str]`. Foreground window first, then visible windows. Unique, whitespace collapsed, cut at `limit`.

`python -m organs.eyes` writes `state/screen.png`, prints that path and `screen_size()`, then one title per line.

## organs/hands.py

Talks to `user32.SendInput` and `powershell.exe`. Imports no organ. Talked to by `trident.tools`. At import, `SetProcessDpiAwarenessContext(-4)`.

`KEYBDINPUT`, `MOUSEINPUT`, `INPUT` are the SendInput structs.

`VK` names: `enter`, `tab`, `escape`, `esc`, `backspace`, `delete`, `insert`, `home`, `end`, `pageup`, `pagedown`, `up`, `down`, `left`, `right`, `ctrl`, `alt`, `shift`, `win`, `space`, `printscreen`, `f1`–`f12`, `a`–`z`, `0`–`9`, `-`, `=`, `[`, `]`, `\`, `;`, `'`, `,`, `.`, `/`, `` ` ``. Extended keys: page up/down, end, home, arrows, insert, delete, win.

`_send(items: list[INPUT]) -> None`. `SendInput`. A short count raises `OSError`.

`_mouse(x: int, y: int, flags: int) -> INPUT`. Absolute position from virtual-screen metrics 76, 77, 78, 79.

`_key(vk: int, scan: int, flags: int) -> INPUT`.

`click(x: int, y: int, how: str = "left") -> None`. Move, down, up. `right` uses the right button. Any other `how` uses the left button. `double` adds a second left down/up.

`drag(x0: int, y0: int, x1: int, y1: int) -> None`. Left down at the start, ten moves, 0.02 s apart, left up at the end.

`press(keys: str) -> None`. Space-separated chords, lowercased. `+` and `-` split a chord. Each chord is pressed and released, then 0.05 s. An unknown name raises `KeyError`.

`type_text(text: str) -> None`. UTF-16-LE units as Unicode key events, one `SendInput` batch.

`run(command: str, timeout: int = 25) -> str`. `powershell.exe -NoProfile -NonInteractive -Command`, hidden window. Timeout returns `timed out`. Stdout and stderr are joined, whitespace collapsed, cut at 600 characters. A command whose stripped lowercase text starts with `start-process` or `start ` then waits 1.5 s. Empty output returns `exit {code}` when the code is non-zero, otherwise `ok`.

`python -m organs.hands press CHORD` calls `press`. `type TEXT...` calls `type_text` on the joined words. `click X Y [HOW]` calls `click`. `run COMMAND...` prints `run`. No `drag` verb.

## organs/memory.py

Talks to `organs.state_dir` and `state/memory.json`. Talked to by `trident`.

`KEEP_TURNS` = 8. `CLIP` = 300.

`Memory.__init__()`. Loads the json file when it exists. `facts`, `turns`, and `quiet` default to `[]`, `[]`, and `False`.

`Memory.save()`. Writes `facts`, `turns`, and `quiet`. `ensure_ascii` false, indent 1.

`Memory.remember(fact: str)`. Collapses whitespace, cuts at 300 characters, appends when the fact is new and non-empty, then saves.

`Memory.add_turn(user: str, model: str)`. Appends both sides, each collapsed and cut at 300. Keeps the last 8. Saves.

`Memory.set_quiet(value: bool)`. Saves only when the value changes.

`Memory.facts_block() -> str`. Empty when there are no facts. Otherwise a leading newline, `Remembered:`, and `- ` lines.

`Memory.history() -> list[tuple[str, str]]`. The stored turns as pairs.

## organs/mouth.py

Talks to `organs` (`CONFIG`, `log`, `path_of`) and `chatterbox.tts_turbo.ChatterboxTurboTTS`. The voice file is `path_of("mouth", "reference")`, which is `reference.wav` beside `config.toml`. Talked to by `trident`. Logger name `mouth`. The chatterbox import happens inside `load`.

`speakable(text: str) -> str`. Keeps letters, digits, punctuation, and the tags `laugh`, `chuckle`, `sigh`, `gasp`, `cough`, `clear throat`, `sniff`, `groan`. Tags survive as ASCII bracket tags. Other characters are dropped (NFKD, ASCII).

`Mouth.__init__()`. `model` is `None`. `sr` is 24000 until load.

`Mouth.load()`. Returns when `model` is set. `ChatterboxTurboTTS.from_pretrained(device=mouth.device, nano=True)`, then `prepare_conditionals` on the reference path. `sr` becomes `int(model.sr)`.

`Mouth.say(text: str) -> np.ndarray`. Calls `load`. Returns float32 mono at `self.sr` from `generate(speakable(text))`.

`pcm48(samples: np.ndarray, rate: int) -> bytes`. Linear resample to 48000 Hz when `rate` is not 48000. Signed 16-bit mono bytes, clipped to [-1, 1].

## organs/telegram.py

Talks to `organs` (`CONFIG`, `log`), `organs.ears.Segmenter`, Telethon, ntgcalls, opentele, PIL `ImageGrab`, and the Telegram Desktop process. Talked to by `trident` (`Line`). Logger name `telegram`. `OWNER` is `owner.telegram_id`. `RATE_TX` = 48000. `RATE_RX` = 16000. `FRAME_TX` = 960 (10 ms of signed-16 mono). `DESK_W`, `DESK_H` = 960, 540.

`telegram_home() -> Path`. `%APPDATA%\Telegram Desktop` or `%LOCALAPPDATA%\Telegram Desktop` when `Telegram.exe` and `tdata` are there. Otherwise `Telegram.exe` on `PATH` when `tdata` sits beside it. Otherwise `FileNotFoundError`.

`telegram_pids() -> list[int]`. PIDs from `tasklist` for `Telegram.exe`.

`quit_telegram()`. Posts `WM_QUIT` (0x0012) to threads that own windows of those PIDs. Waits up to 25 s. Remaining processes raise `RuntimeError`. No process returns immediately.

`start_telegram()`. Returns when a PID already exists. Otherwise starts `Telegram.exe` from `telegram_home()`.

`desk_i420() -> bytes`. `ImageGrab.grab()` with no arguments, resized to 960×540 (bilinear), converted to I420.

`rtc_servers(connections) -> list[RTCServer]`. `PhoneConnectionWebrtc` and `PhoneConnection` become `RTCServer` rows.

`media(rate: int, camera: bool) -> MediaDescription`. External mono microphone at `rate`. Camera is an external 960×540 stream at `desk_fps` when `camera` is true. Speaker and screen are unset.

`wire_protocol() -> PhoneCallProtocol`. ntgcalls protocol, `min_layer` 65, `max_layer` 92, library versions reversed.

`Line.__init__(on_text, on_utterance, on_line)`. Stores the three callbacks. New asyncio loop. `state` starts as `idle`. Owns one `Segmenter`. `on_text(str)` is started on a daemon thread per message. `on_utterance(float32 ndarray)` runs on the receive thread. `on_line(str)` runs on the asyncio thread with `idle`, `ringing`, or `up`.

`Line.start()`. `quit_telegram()`, starts `telegram-loop` and `telegram-rx`, then `_connect` within 180 s.

`Line.stop()`. `hang()` when `state` is not `idle`. When `client` exists, disconnects within 20 s and then `start_telegram()`. Stops the loop. A session that was never opened does not start Desktop.

`Line._run_loop()`. Runs the asyncio loop.

`Line._await(coro, timeout)`. Runs `coro` on the loop and returns its result.

`Line._disconnect()`. `client.disconnect()`.

`Line._set(state: str)`. On a change, stores it, logs it, and calls `on_line(state)`.

`Line.up -> bool`. True when `state` is `up`.

`Line._connect()`. Opens `tdata` from `telegram_home()`. Uses the first account whose `UserId` is not `OWNER`. `TelegramClient.FromTDesktop` with `MemorySession`, `UseCurrentSession`, `API.TelegramDesktop`. Raw updates go to `_on_raw`. Incoming private messages from `OWNER` go to `_on_message`. Connects, loads dialogs, resolves `OWNER`, sets `idle`.

`Line._on_message(event)`. A stripped private `raw_text` is logged and passed to `on_text` on a new daemon thread.

`Line.send_text(text: str)`. Returns immediately when `client` is `None`. Otherwise `send_message` to `OWNER`, timeout 30. Returns None.

`Line.send_photo(png: bytes, caption: str = "")`. Returns immediately when `client` is `None`. Otherwise sends an in-memory file named `desk.png`, `force_document` false, timeout 60. Returns None.

`Line._on_raw(update)`. Signaling data is forwarded when media is up, otherwise queued. A requested call whose `admin_id` is not `OWNER`, or that arrives while `state` is not `idle`, is discarded as missed (`video` true). Otherwise `_answer` is scheduled. Accepted and confirmed calls store `phone` and complete the matching futures. A discarded call tears the line down.

`Line._dh() -> DhConfig`. `GetDhConfigRequest(0, 256)`.

`Line._begin_media()`. New `NTgCalls`, frame and connection handlers, signaling sent back through `SendSignalingDataRequest`. Creates the p2p call. Capture is `media(48000, desk_video)`. Playback is `media(16000, False)`. Resets the segmenter.

`Line._link(call: PhoneCall)`. `connect_p2p` with `rtc_servers`, then flushes queued signaling. Waits up to 30 s for `ConnectionState.CONNECTED`. Sets `listening`, `began`, and `up`. When `desk_video` is true, starts `telegram-desk`.

`Line._answer(requested)`. Sets `ringing`. Exchanges keys, `AcceptCallRequest`, waits up to 30 s for the confirmed call when the accept result is not already a `PhoneCall`, then `_link`. Failure logs `answer failed` and tears down.

`Line._place()`. Sets `ringing`. `RequestCallRequest` with `video` true. A discarded invite raises `RuntimeError("call discarded")`. Waits up to 90 s. Timeout discards the call as missed and raises `RuntimeError("call missed")`. Confirms and `_link`.

`Line._teardown()`. Stops ntgcalls, discards the call as a hangup when connected (timeout 8 s), clears the receive buffer, sets `idle`.

`Line.dial()`. When `client` is `None`, raises `RuntimeError("line is down")`. When `state` is not `idle`, raises `RuntimeError`. Awaits `_place` within 150 s. On `BaseException`, awaits `_teardown` within 20 s and re-raises.

`Line.hang()`. When `state` is not `idle`, awaits `_teardown` within 20 s.

`Line._on_connection(_uid, info)`. `CONNECTED` completes the connect future. `CLOSED`, `FAILED`, or `TIMEOUT` while `up` schedules `_teardown`.

`Line._on_frames(_uid, mode, _device, frames)`. Playback frames while `listening` are appended to the receive buffer.

`Line._rx_worker()`. Signed-16 playback bytes become float32 at 16 kHz divided by 32768, then `Segmenter.push`. A finished clip is passed to `on_utterance`.

`Line._send_frame(device, data, frame)`. `send_external_frame` for `OWNER`.

`Line.speak(pcm48: bytes)`. Requires `up`, otherwise `RuntimeError("line is down")`. Clears `listening`, sends 10 ms microphone frames paced to real time (each await timeout 5 s), drops queued receive audio, resets the segmenter, and restores `listening` when still `up`.

`Line._desk_video()`. While `up`, sends `desk_i420()` as camera frames. Sleeps `1/desk_fps`. An exception ends the thread.

`python -m organs.telegram` connects, prints text, speech seconds, and line state, and answers a call without sending speech. Ctrl+C calls `stop()`.

## trident.py

Talks to `organs` (`CONFIG`, `log`, `state_dir`), `brain` (`Brain`, `Tool`), `ears` (`transcribe`, `write_wav`), `memory` (`Memory`), `mouth` (`Mouth`, `pcm48`), `telegram` (`Line`), and the local Cursor agent at `%LOCALAPPDATA%\cursor-agent`. `eyes` and `hands` are imported inside `tools`. Logger name `trident`. Import loads config, opens `state/trident.log`, and does not start llama, the mouth model, or Telegram.

`OWNER` is `owner.name`. `INBOX` is `state/inbox.txt`. `SOURCE` is `chat` = `a Telegram message from him`, `call` = `his voice on the call`, `typed` = `a line he typed on the computer`, `idle` = `nobody; an idle moment`.

`SYSTEM` is passed to every `think`, with `{owner.name}` filled in:

```
You are Gemma, the mind of {owner.name}'s computer. You see the screen and act with tools.
Each turn: think in two or three short sentences, then either call exactly one tool or say one or two short sentences in English. He hears only what you say, never the thought.
Do the screen work he asked for. Each look is one small step and you write its prompt. The prompt says where that one element sits, what it looks like, and asks for its center. A taskbar is the strip of icons along the bottom edge. The first icon in that strip is a small square at the left end of the bottom edge, y0 900, x0 0, y1 1000, x1 100, and the crop prompt names that icon on the bottom edge. A pass does not list every element. To click or drag: call crop with that small square. The prompt names the edge, and a taskbar icon names the bottom edge. Then call click or drag with the x and y from the crop. A whole-desktop point is not the box. If the tool says not cropped, not clicked, not dragged, or not pressed, call crop next. When a look is not confident, the next call is look once more before you act.
Open a program with run. After a page opens or a message is sent, run Start-Sleep, then look again before you act.
A command is run. A key he asked for is press. press does not click.
When he asks you to type, the type_text text is that sentence copied unchanged. Example: he says type The note says reply with exactly the word maple. The text is The note says reply with exactly the word maple.
Ring him or send him a message only when he asks. When he says goodbye or asks you to stop, use hang_up and say nothing.
```

`PASS` is the JSON schema for one look: `answer` (string), `confident` (boolean), `y` and `x` (integers 0 through 1000), all required.

`cloud_look(png: bytes, question: str) -> dict`. Writes `png` to `state/cloud/look.png`. Runs the newest `%LOCALAPPDATA%\cursor-agent\versions\*\node.exe` on that version's `index.js`, print mode, ask mode, `--trust`, `--model` `cloud.model`, text output, workspace that folder. The prompt says to read only `look.png` and reply with one JSON object of `answer`, `confident`, `y`, and `x`, then the question. Non-zero exit raises `RuntimeError` with stderr. Returns `json.loads` of stdout. Logs `cloud` plus the model name.

`Trident.__init__()`. Builds `Brain`, `Mouth`, `Memory`, an event queue, and a `stopping` event. `request`, `seen`, and `unsure` start empty or false. `cropped` and `acted` start false. `Line` callbacks: text pushes `("chat", text)`, an utterance pushes `("call", samples)`, a line-state change calls `touch`.

`Trident.start()`. `brain.start()`, `mouth.load()`, `line.start()`, then daemon threads `trident-worker`, `trident-inbox`, and `trident-idle`.

`Trident.stop()`. Sets `stopping`, then `line.stop()` and `brain.stop()`.

`Trident.touch()`. Sets `last_activity` and clears `idle_sent`.

`Trident.push(kind: str, payload)`. `touch()`, then queues `(kind, payload)`.

`Trident.inbox_loop()`. Every 0.5 s until stop, if `state/inbox.txt` exists, reads the non-empty stripped lines, deletes the file, then queues each line as `typed`.

`Trident.idle_loop()`. Every 1 s, when `brain.idle_after` seconds have passed, `idle_sent` is false, `memory.quiet` is false, the line is down, and the queue is empty: sets `idle_sent` and queues `idle` with `Nothing has happened for a while. Look if you are curious, ring him only for a reason, otherwise answer with the single word idle.` This path does not call `touch`.

`Trident.worker()`. Takes one event at a time, waiting up to 0.5 s. `handle` exceptions are logged as `turn failed`.

`Trident.handle(kind: str, payload)`. `call` writes `state/call.wav` and `transcribe`s it. Empty text returns. Other kinds use `payload` as the text. `typed` text that starts with `/` goes to `command` and returns. Any kind other than `idle` calls `memory.set_quiet(False)`. Then `deliver(kind, turn(kind, text))`.

`Trident.command(name: str)`. `call` dials and speaks `I am up. Do you want anything?`. `hang` calls `line.hang()`. `stop` sets `stopping`. Any other name returns. A command does not clear `quiet` and does not call `turn`.

`Trident.turn(kind: str, text: str) -> str`. Stores `text` on `request` and sets `cropped` and `acted` false. User text to the brain is `[{HH:MM} | line {state} | heard from: {SOURCE[kind]}]\n{text}`. Calls `brain.think(SYSTEM + facts_block(), tools(kind), history(), that text, on_step=...)`. `on_step` sends the thought, the tool name, and the arguments as JSON, uncut, with `line.send_text`. When `reply.text` is non-empty and `reply.text.lower().strip(".")` is not `idle`, appends the turn to memory and returns `reply.text`. Otherwise returns `""`. `seen` and `unsure` are kept across turns.

`Trident.deliver(kind: str, text: str)`. Empty text returns. When the line is up, `speak`. When the line is down and `kind` is `chat`, `line.send_text`. A `typed` or `idle` reply with the line down is dropped.

`Trident.speak(text: str)`. `line.speak(pcm48(mouth.say(text), mouth.sr))`.

`Trident.tools(kind: str) -> dict[str, Tool]`. `asked` is true when `kind` is not `idle`. The dict is:

- `look(prompt: str, y0: int = -1, x0: int = -1, y1: int = -1, x1: int = -1)`. Declared as `One look at the whole desktop. It returns no click point. Write the prompt for this pass. Say where the one element sits, what it looks like, and ask for its center. The picture is also sent to {owner.name}'s chat.` Schema requires `prompt` (`STRING`). One new screenshot. A box is used only when every edge is at least 0, and then `eyes.crop` cuts it. When `unsure` is already true, `eyes.shrink` runs first and this pass calls `cloud_look`. Otherwise `brain.ask_json` with `PASS`. The question is her prompt, then `Request:` the stored user text, then `Last:` the previous answer, then one general instruction: one short answer, do not list every element, the point is the center of the one element the request names, and `confident` is false when the requested text is not printed. Each picture is sent with `line.send_photo` before the model sees it. `seen` becomes the answer. A local pass that is not confident sets `unsure`, so the next look is the cloud pass. A cloud pass clears `unsure`. `cropped` is true only when this pass had a box. A crop returns `x` and `y` from `eyes.point_px`. Any other look returns `x` and `y` from `eyes.center_px`, except a click or drag request, which gets no point. When not confident, also `next` = `look once more`. On a click or drag, before a click has landed, a whole-desktop look that is confident replaces `answer` and `seen` with `name that icon on the bottom edge and crop its small square` and sets `next` to `crop`.
- `crop(prompt: str, y0: int, x0: int, y1: int, x1: int)`. Declared as `One look at a tight crop of one element. The prompt names the edge where it sits and asks for its center. y0, x0, y1, x1 are that box, 0 to 1000, origin at the top left, y vertical. One icon is a small square, under 100 units on a side, on the edge the prompt names. A taskbar icon names the bottom edge. The picture is also sent to {owner.name}'s chat.` All five are required. A side longer than 100, a box that reaches y 960 without the prompt naming the bottom edge, a bottom-edge prompt whose box stays above that edge, or a prompt that names the left of the bottom edge while the box starts past x 20, returns `not cropped. name the icon on the bottom edge. the first icon is y0 900, x0 0, y1 1000, x1 100` and does not look. Otherwise calls `look` with that box.
- `click(x: int, y: int, how: str = "left")`. Declared as `Click the x and y returned by crop.` `x` and `y` required, `how` optional enum `left`, `right`, `double`. When not `asked`, returns `nobody asked for this`. When `cropped` is false, returns `not clicked. crop a small square on the edge the prompt names, then click the x and y from that crop` and does not click. Otherwise `hands.click`, sets `acted`, and returns `{how} click at {x} {y}`.
- `drag(x0: int, y0: int, x1: int, y1: int)`. Declared as `Drag from one screen pixel to another.` All four required. Same idle refusal. When `cropped` is false, returns `not dragged. call crop with a tight box around the element, then drag using the x and y from that crop` and does not drag. Otherwise `hands.drag` and returns `dragged {x0} {y0} to {x1} {y1}`.
- `type_text(text: str)`. Declared as `Type the text argument exactly, every word of it, where the cursor is.` `text` is `The text to type, every word.` Idle refusal, or `hands.type_text(text)` and `typed`.
- `press(keys: str)`. Declared as `Press a key he asked for, for example enter, escape, tab, or ctrl-a. Never a click and never a command.` `keys` is `The key or chord.` Idle refusal. When the request asks for a click and does not ask for a key, returns `not pressed. a key does not click. crop the element and click the x and y from that crop` and does not press. Otherwise `hands.press` and `pressed {keys}`. An unknown key still raises `KeyError` from `hands.press`.
- `run(command: str)`. Declared as `Run one PowerShell command. Start-Process opens a program. Start-Sleep -Seconds N waits.` `command` is `The PowerShell command.` Idle refusal. When the request asks for a click, does not ask to run, and `acted` is still false, returns `not run. a command does not click. crop the element and click the x and y from that crop`. Otherwise returns `hands.run(command)`.
- `remember(fact: str)`. Declared as `Keep one short fact for later turns.` `fact` is `The fact.` `memory.remember`. Returns `remembered`.
- `call_owner(opening: str = "I am up. Do you want anything?")`. Declared as `Ring {owner.name} on Telegram. When he answers, the opening is spoken to him first.` `opening` (`The first sentence he hears.`) is optional in the schema. Line already up returns `the line is already up; just speak`. `memory.quiet` returns `you promised to stay quiet until he speaks`. `line.dial()` failure returns `he did not answer: {exc}`. Success speaks `opening` and returns `he answered and heard the opening; now say what he should hear next, or hang_up`.
- `hang_up()`. Declared as `End the call. Say nothing after it.` No params. `final` is true, so `think` returns no spoken text. Calls `line.hang()` and returns `hung up`.
- `send_message(text: str)`. Declared as `Send {owner.name} a Telegram text message.` `text` is `The message.` `line.send_text`. Returns `sent`.
- `stay_quiet(reason: str)`. Declared as `Promise not to ring until he speaks to you again.` `reason` is `Why.` `memory.set_quiet(True)`. The reason is not stored. Returns `quiet until he speaks`.

Tool description strings interpolate `owner.name` for `look`, `crop`, `call_owner`, and `send_message`.

The cloud model is `gpt-5.6-luna-none`, non-fast, on the local Cursor agent. Composer 2.5 cannot see a picture. Grok 4.7 standard is $2 per million input tokens and $6 per million output tokens. Luna is $0.20 and $1.20. A 480 by 270 picture is 135 patches of 32 pixels, times 1.2, which is 162 image tokens, about $0.000032 before the agent prompt. A free no-login browser page was not wired.

`main()`. `say TEXT` appends that line to `state/inbox.txt` and returns. `call`, `hang`, and `stop` append `/call`, `/hang`, or `/stop`. Words after those three commands are ignored. No command builds `Trident`, `start`s it, and waits on `stopping`. `KeyboardInterrupt` falls through. `stop()` always runs afterward.

## install.py

Talks to `config.toml`, the network, `zipfile`, `subprocess`, and `huggingface_hub` (imported inside `voice`). Does not import `organs`. Paths: repo root, `paths.bin`, `paths.models`, `.venv/Scripts/python.exe`.

`say(text: str)`. Prints and flushes.

`download(url: str, dest: Path)`. Returns when `dest` exists. Otherwise downloads with User-Agent `trident`, timeout 60, via a `.part` file, then replaces `dest`.

`github_json(url: str) -> dict`. GET, User-Agent `trident`, Accept `application/vnd.github+json`, timeout 30.

`venv()`. Creates `.venv` when its `python.exe` is missing. When the running interpreter is not that exe, re-runs this file under it and exits with that process's code.

`packages()`. Pip-installs `torch` and `torchaudio` from `install.torch_index`, then `pip install -r requirements.txt`.

`llama()`. Returns when `bin/llama/llama-server.exe` exists. Otherwise `https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/{llama_tag}`, or `.../releases/latest` when the tag is empty. Picks the asset whose name starts with `llama-` and ends with `llama_asset`, plus `cudart_asset`. Extracts both, copies the exe's directory files and every `.dll` into `bin/llama`, writes `release.txt` with the tag name. No matching asset raises `SystemExit`.

`nemo()`. Returns when `bin/nemo-speech/bin/nemo-speech.exe` exists. Otherwise downloads `nemo_url`, finds `nemo-speech.exe`, and copies that exe's grandparent tree to `bin/nemo-speech`.

`models()`. Downloads `gemma_url`, `mmproj_url`, `ear_url`, and `silero_url` to `models/{brain.model}`, `models/{brain.mmproj}`, `models/{ears.model}`, and `models/silero_vad.onnx`.

`voice()`. `snapshot_download("ResembleAI/chatterbox-nano")`.

`main()`. Changes to the repo root, then `venv`, `packages`, `llama`, `nemo`, `models`, `voice`. Prints `done. next: put reference.wav here if it is missing, then  python trident.py`.

## requirements.txt

Installed by `install.packages` after the CPU torch wheels. `numpy`, `Pillow`, `onnxruntime==1.20.1`, `huggingface_hub`, `chatterbox-tts`, `telethon==1.45.0`, `ntgcalls==3.0.0`, `opentele-ng==1.4.0`.

## reference.wav

Tracked binary (`.gitattributes`). Present at the repo root. `mouth.reference` points here. This map did not read the audio.

## .gitignore

Ignores `.venv/`, `bin/`, `models/`, `state/`, `__pycache__/`.

## .gitattributes

`* text=auto eol=lf`. `reference.wav binary`.

## LICENSE

MIT. Copyright (c) 2026 Gianfranco Cordella. The file also names ggml (MIT, 2023-2026, The ggml authors) and Chatterbox / Chatterbox Turbo (MIT, 2025, Resemble AI).
