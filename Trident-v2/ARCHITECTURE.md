# Architecture

This document is for whoever edits Trident next, human or model. It states the rules the code
follows and why. If a change breaks a rule here, change the rule here first.

## 1. The one idea

Gemma 4 E2B is a small model. It was trained on a specific input format and produces a specific
output format. Trident speaks that format to her and lets Python do everything that is not thinking.

Concretely:

* **Prompt tokens are Gemma's, verbatim.** `<bos>`, `<|turn>role\n ... <turn|>`, `<|think|>` first
  thing in the system turn, `<|channel>thought ... <channel|>` for reasoning,
  `<|tool>declaration:...<tool|>` for tools, `<|tool_call>call:name{k:v}<tool_call|>` for calls,
  `<|tool_response>response:name{...}<tool_response|>` for results, `<|"|>` around every string.
  The renderer in `brain.py` matches Google's chat template, including sorted keys and its stray
  spaces. Never ask her for JSON tool calls, Python tool calls or any format of our invention.
* **Coordinates are on her grid.** Gemma localizes as `[{"box_2d":[y0,x0,y1,x1],"label":...}]`
  on a 1000 x 1000 grid regardless of image size. That is what `Brain.locate()` asks for and what
  the JSON schema enforces. `eyes.center_px()` turns it into screen pixels. She never converts,
  never sees a pixel count, never writes a number for the mouse.
* **The agent does not see images.** The agent turn (`Brain.think`) is text only: situation line,
  his words, tool declarations, short history. Vision is a tool. `look` and `click` each take a
  fresh screenshot and run a separate single-turn vision query (`see`, `locate`) with thinking
  off. The agent gets back words. Benefits: the agent prefix stays cached in llama-server
  (`cache_prompt`), 560-token images never pile up in context, and each query is the shape she
  was trained on (one image, one question).
* **Structure is enforced, not requested.** When output must be machine-readable, pass a JSON
  schema (`Brain.ask_json`, `Brain.locate`). llama-server compiles it to a grammar; the sampler
  cannot leave the shape. No "answer only with JSON" pleading, no regex rescue.
* **Within one model turn, thoughts stay.** When a tool is called, the next request is the previous
  prompt + her full output + the tool response, so her reasoning is still in front of her
  (Gemma docs: "thoughts must NOT be removed between function calls"). Between user turns only
  the final words are kept in history (`memory.py`).
* **Sampling is the published one.** temp 1.0, top-k 64, top-p 0.95, min-p 0.05, in `config.toml`,
  used for every request. Grammar-constrained requests do not need a lower temperature.

## 2. Organs and their contracts

Each organ is one file in `organs/`, importable alone, runnable alone with `python -m organs.X`.
An organ owns one table in `config.toml` and reads nothing else. Organs do not import each other
except `telegram.py` which reuses `ears.Segmenter` for call audio. Only `trident.py` imports all.

| Organ | Public surface | Threads it owns |
|---|---|---|
| `brain.Brain` | `start/stop/alive`, `complete`, `see(png,q)`, `locate(png,target)`, `ask_json(q,schema,png=None)`, `think(system,tools,history,user)` | none (HTTP client); `llama-server.exe` child |
| `eyes` | `screenshot() -> png bytes`, `center_px(box_2d) -> (x,y)`, `window_titles()`, `screen_size()` | none |
| `hands` | `click(x,y,how)`, `drag`, `press(keys)`, `type_text`, `run(command)` | none |
| `ears` | `Segmenter.push(f32@16k) -> clip|None`, `Microphone(on_utterance)`, `transcribe(wav) -> (text,lang)`, `write_wav` | PortAudio callback thread |
| `mouth.Mouth` | `load`, `say(text) -> f32 samples`, `play(samples)`; `pcm48(samples, rate) -> bytes` | none |
| `telegram.Line` | `start/stop`, `dial`, `hang`, `speak(pcm48)`, `send_text`, `send_photo`, `.state`, `.up` | asyncio loop, rx segmenter, desk video |
| `memory.Memory` | `remember`, `add_turn`, `set_quiet`, `facts_block`, `history` | none |

Data between organs: numpy float32 arrays at 16 kHz for speech in, PNG bytes for pictures,
`bytes` of s16 mono 48 kHz for speech out, plain `str` everywhere else. No custom file protocols,
no pid files, no lock files. The only file another process writes is `state/inbox.txt`
(`trident.py say ...`), one line per message.

## 3. The organism loop (`trident.py`)

```
senses ---> queue ---> worker: handle(kind, payload) ---> deliver(kind, words)
chat      Telegram message       transcribe if audio        line up?   -> speak on the call
call      owner voice on call    brain.think(...)           chat?      -> send_text
room      cable microphone         tools run inline         room/typed -> speakers
typed     state/inbox.txt        memory.add_turn            idle       -> nothing
idle      timer (once)
```

One worker, one turn at a time, in arrival order. Tools run inside `brain.think` on the worker
thread, so a `call_owner` tool can block on dialing and then speak the opening. `hang_up` is a
`final` tool: the turn ends with no words.

The user turn is `"[HH:MM | line idle|ringing|up | heard from: ...]\n<his words>"`. The volatile
facts go in the user turn so the system prefix (and its KV cache) stays identical across turns.

Policy that lives in code, not in the prompt: hands tools refuse during an `idle` turn (nobody
asked); `call_owner` refuses while `memory.quiet` is set; any input from him clears `quiet`; the
microphone is muted while a call is up or while the speakers play her voice.

## 4. llama-server

Started by `Brain.start()` from `bin/llama/llama-server.exe` with `--mmproj`, two slots
(`--parallel 2`, so vision requests do not evict the agent prefix), f16 KV, flash attention off,
`--image-min-tokens/--image-max-tokens 560`. Requests go to `POST /completion` with:

```json
{"prompt": {"prompt_string": "...<__media__>...", "multimodal_data": ["<base64 png>"]},
 "n_predict": 1024, "cache_prompt": true, "stop": ["<|tool_response>", "<turn|>"],
 "temperature": 1.0, "top_k": 64, "top_p": 0.95, "min_p": 0.05,
 "json_schema": {...}}
```

`<__media__>` is llama.cpp's placeholder; mtmd replaces it with Gemma's own image tokens.
`<|tool_response>` as a stop string is what the model emits right after a tool call, so generation
halts exactly where the application must act. The last prompt sent is always in
`state/last_prompt.txt`; read it when her behaviour surprises you.

## 5. What was removed from v1 and must not come back

* The C++ tree (ggml/Vulkan chatterbox port, baker, converters, `brain.cpp`, `vad.exe`) and the
  CMake/CUDA/Vulkan build. Prebuilt llama.cpp, prebuilt NeMo-Speech.cpp, pip Chatterbox.
* The v3/turbo voices and all multilingual TTS logic. Nano only, English only. He may speak
  Polish; she answers in English on the call and in text in the chat.
* File-based IPC between residents (`*.pid`, `*.stop`, `*.busy`, `*.prompt.txt`, card protocol
  on a TCP socket, scheduler, node ids). One process, threads, a queue.
* Four tool-call parsers and string heuristics for "ring" and "stop". One parser for the one
  format she is trained on.
* "Percent" coordinates and a one-string `line` parameter for every tool. Typed parameters, named
  screen elements, grid math in Python.
* Fallback backends, auto-detection, publish steps, settings files with 100 knobs. One
  `config.toml`.

## 6. Adding a tool

1. Write the function in `Trident.tools()` (or a new organ if it needs hardware).
2. Register it: `Tool(name, one_sentence_description, {param: {"description", "type", "enum"?}}, fn, optional=(...), final=False)`.
   Types are `STRING`, `INTEGER`, `NUMBER`, `BOOLEAN`. Parameter names become Python keyword
   arguments, so they must match the function signature.
3. Return a `str` or a flat `dict`; it is rendered as the tool response.
4. Mention when to use it in `SYSTEM` only if the description is not enough.

Keep descriptions short and concrete; a 2B model reads them every turn.
