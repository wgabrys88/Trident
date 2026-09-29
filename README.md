# Trident

Trident is one voice organism on two home Windows PCs. Iris is the body: it hears, places a brain, and speaks. PE is the mind: Gemma returns text. Audio stays on Iris. The computer name is not a route key.

There is no online bridge. A fresh clone installs and runs from this tree.

Checked-in code outranks this file. If a sentence here disagrees with `run.py`, `assistant.py`, `seat.py`, `hear.py`, `mouth.py`, `gemma.py`, `nvidia_worker.py`, `nvidia_start.py`, `nvidia_stop.py`, or `nvidia_client.py`, follow the code.

License: MIT. See `LICENSE`.

## Seats

| Seat | Machine | Account | Checkout | Owns |
| --- | --- | --- | --- | --- |
| Iris, body | EB-W | `eb-wjt` | `C:\Users\eb-wjt\Downloads\Jarvis\Trident` | Microphone, `hear.py`, `mouth.py`, playback |
| PE, mind | PE-DMLW | `px-wjt` | `C:\Users\px-wjt\Downloads\Jarvis\Trident` | Gemma and the HTTP worker on port 8765 |

The worker that has been serving turns is `http://192.168.16.31:8765/`. On PE it binds `0.0.0.0:8765`. Iris does not bind that port. The measured PE card is a GeForce GTX 1060 6 GB, CUDA 12.6, architecture `61-real`. The mouth on Iris prints `mouth out: default`. On the measured day that default was Speakers (Realtek(R) Audio).

## Install

From a fresh clone, on each PC:

```powershell
git clone https://github.com/wgabrys88/Trident.git
cd Trident
git checkout runner-h
python install.py install.txt
```

`install.py` takes that one argument. It creates `.venv`, installs the packages in `install.txt`, clones the pinned ggml, llama.cpp, and NeMo trees, builds the mouth (`chatterbox.exe`, `vad.exe`, `ear.exe`), `nemo-speech.exe`, `gemma-brain.exe`, and `sense.exe`, downloads the weights, and bakes nano, turbo, and v3 from `reference.wav`.

On this PC you need:

- Windows, Python 3 on PATH (with `venv`), Git, CMake, Visual Studio 2022
- A Vulkan SDK under `C:\VulkanSDK` (`install.vulkan_sdk` is empty, so the newest SDK there is used)
- A writable `C:\tgemma` (the Gemma build path stays short because the shader step fails on a long path)
- Network access to GitHub and Hugging Face

`install.gemma_backend` is `auto`. A machine with an NVIDIA controller and `nvcc` builds Gemma for CUDA. Otherwise the build is Vulkan. The PE brain card in `install.txt` is CUDA 12.6, architectures `61-real`. A different GPU means a new `install.cuda_architectures` and a re-check of `gemma.ctx` and `gemma.gpu-layers` in `gemma.txt` before those 1060 numbers are reused.

Weights, executables, and `.venv` stay gitignored. Run programs with `.\.venv\Scripts\python.exe`.

The first reply that is split by language downloads `lid.176.ftz` beside the checkout if that file is missing. Install does not fetch it.

## Run

Leave a healthy port 8765 alone. Start the worker only when the port is free.

On PE:

```powershell
.\.venv\Scripts\python.exe .\nvidia_start.py
```

`nvidia_start.py` takes no arguments. It runs `nvidia_worker.py --host 0.0.0.0 --port 8765` only when port 8765 is free. If the port is already listening, the script exits 2 and prints that it is leaving the listener alone.

```powershell
.\.venv\Scripts\python.exe .\nvidia_stop.py --cutover
```

`nvidia_stop.py` without `--cutover` exits 2 and stops nothing. `--cutover` is an owner cutover. It stops the `nvidia_worker.py` process that owns the listen socket. It does not stop `gemma-brain.exe`.

On Iris, live microphone. A person is at the machine. `run.py` re-execs into `.venv`.

```powershell
.\.venv\Scripts\python.exe .\run.py start
.\.venv\Scripts\python.exe .\run.py start --url http://192.168.16.31:8765/
```

`start` with no `inject` opens the default WASAPI microphone (`iris: live mic`) and runs `assistant.py --nvidia --timeout 180`. There is no separate cue flag.

Closed microphone. One turn per line, or blocks split by a line that is only `---`. No path reads stdin.

```powershell
.\.venv\Scripts\python.exe .\run.py start inject PATH
.\.venv\Scripts\python.exe .\run.py stop
```

The URL is `--url`, else `TRIDENT_NVIDIA_URL`, else `http://192.168.16.31:8765/`. `run.py` does not pass `--model`. English through `run.py` is nano.

`run.py stop` stops this organism and the mouth. It does not open the brain and it does not bind, stop, or restart port 8765.

If the house LAN address changes, change the URL. Keep port 8765.

`grok_local_bot.py` is an optional local turn and an unattended wav door (`--wav` writes mouth wavs and does not play them). The voice entry is `run.py`.

## Alone and LAN

`assistant.turn_place` asks `gemma.place`. One TCP connect. The computer name is not an input. This decision does not bind port 8765.

| Situation | Brain | What Iris prints |
| --- | --- | --- |
| The peer URL accepts | POST to that URL | `assistant: lan` |
| No peer, and `127.0.0.1:8765` accepts | POST to `http://127.0.0.1:8765/` | `assistant: lan` |
| Port closed, this PC has a CUDA device | Local `gemma.py` | `assistant: alone` |
| Port closed, no CUDA device | `qwen.py` (`--brain gemma` uses local `gemma.py`) | `assistant: alone` |
| The named peer does not accept | Iris prints `assistant: peer unreachable`, then uses the no-peer row | |

A voice turn that POSTs uses `assistant.remote_whole`: `stream` false, then the mouth after the whole body. The log line is `assistant: nvidia`. `nvidia_client.py --stream` still exists for a client that asks. The worker returns text. It does not return audio. There is no GET.

When the brain and the mouth would share one GPU on this PC, Iris stops the local Gemma resident before speaking. A peer on another address leaves that resident loaded.

## Mouth

`mouth.py` keeps Chatterbox resident and plays with `PlaySoundW` (`mouth out: default`).

- English: nano. Turbo only when `--model turbo` is passed. `run.py` leaves the default.
- Any other tag in the v3 set (`assistant.V3_LANGS`): v3 for that tag. English on v3 exits. nano or turbo with a non-English tag exits.
- Budgets: English 65 words, any other tag 55.
- The fast resident stays up. A known later v3 span is synthesized beside it (`mouth: prefetch`) and that process exits.

Hearing is `hear.py` and `nemo-speech.exe` on `ear.gguf`. The live loop keeps `vad.exe --resident`. `ear.exe` is a separate one-shot.

## Memory, tools, and quiet work

Text turns on the machine that runs `gemma.py` replay `gemma.memory.txt` and declare tools. Python runs a tool only when the generation contains the call. It does not match keywords in the question. Image turns skip tools and memory replay.

| Call | What it does |
| --- | --- |
| `remember` | Appends one fact (at most 200 characters) and asks once more. |
| `place` | Reports CUDA device 0, Vulkan device 0, whether they are the same adapter, whether `127.0.0.1:8765` accepts, and whether a mouth here would share that GPU. No hostname. |
| `next` | Appends one work line and does not run it. `iris_status.txt` gains `work` plus that line. |
| `cursor` | Starts one local `agent` (`composer-2.5`, worktree base `runner-h`) and does not wait. A missing CLI is `BLOCKED`. |
| `stop` | Leaves the call in the answer. Does not ask again. Does not unload `gemma-brain.exe`. Does not close port 8765. Empty line, or a line that is not waiting work: `iris_status.txt` gains `stop voice`. A matching work line is dropped and the status line is `stop` plus that line. |

A spoken shutdown is that `stop` call (`assistant.organism_stop`). The sentence "Please stop listening." is ordinary text.

Facts and work lines are clipped to 200 characters. Duplicate facts and duplicate work lines are kept once. The prompt cap is 80_000 characters. Oldest turns drop first.

When no POST has arrived for 60 seconds, no connection is waiting, and the resident is not busy, the worker runs one `gemma.py --idle`. A speakable notice replaces `iris_outbox.txt` (one line, at most 2000 characters, via `iris_outbox.txt.tmp`) and appends `say` to `iris_status.txt`. Status keeps the last 40 lines. The word `idle` is not written onward. `--drop` does not run the notice.

Iris, on the live microphone and between inject turns, reads `iris_seat.txt`, `iris_outbox.txt`, and `iris_status.txt` in the checkout, or the paths in `TRIDENT_IRIS_SEAT`, `TRIDENT_IRIS_OUTBOX`, and `TRIDENT_IRIS_STATUS`. `say` is spoken. `stop voice` ends this PC's voice and leaves the brain up. `stop` plus a work line drops that line and does not speak. `status` and `work` ask the brain. The line format is `proof/README.md`. On two PCs the files are written on the brain disk; point those variables at them when Iris should see them.

## Hard laws

- Code outranks this file.
- Leave a healthy `:8765` alone. Iris does not bind it. `run.py stop` does not contact it. The only worker stop is `nvidia_stop.py --cutover`, and only as an owner cutover.
- Live microphone only when a person starts it. Inject is the closed-mic path.
- The computer name is not a route key.
- English is nano, or turbo with `--model turbo`. Every other v3 language is v3.
- A spoken stop is the `stop` tool call.
- One Gemma beside the listener. Do not start a second one.
- No online bridge.
- Work on `runner-h`. Do not force-push. Do not delete `main` or `runner-h`.

## Cards

Knobs live in the text cards the programs read: `install.txt`, `gemma.txt`, `sense.txt`, `chatterbox.txt`, `bake.txt`, `ear.txt`, `vad.txt`. `hear.py` does not read `ear.txt`. `mouth.py` rewrites variant, language, play, and text; the other `chatterbox.txt` keys are the voice. `chatterbox.play on` in the template is not the speaker switch.

## Proof

Measured notes under `proof/` are the runs. They are not a second manual. The voice-seat record is `proof/voice-seat.md` (`STATUS PASS`: alone inject, LAN inject, `run.py stop`, port left accepting). The spoken stop is `proof/g429-run-stop.md`. The live-mic measurement is `proof/g429-live-mic.md` (the `start.py` command in that note is gone; `run.py start` is the entry). Mouth spans are `proof/span-mouth.md`. The offline check is `proof/voice_seat_check.py`. Brain measurements that still match the tools above stay beside them, including `proof/g429-pe-brain-finish.md`.
