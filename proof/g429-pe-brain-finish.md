# G429 PE brain finish — tools, status, quiet handoff

Written 2026-09-29 (UTC+2). Checkout `cursor/pe-brain-finish-df7c`. Listener on `0.0.0.0:8765` was not restarted. No microphone. No mouth. No `nvidia_stop.py --cutover`.

## Leave-healthy

Before the injects, `netstat` showed `0.0.0.0:8765` LISTENING pid **2636**. After them, the same pid was still listening.

`nvidia_stop.py` with no `--cutover` exited **2** and printed `nvidia stop: refused... Pass --cutover`. It did not stop the worker.

## Tools (text POST, Gemma emits the call)

`nvidia_client.py` POSTed to `http://127.0.0.1:8765/`. Python did not pick the tool.

- **remember.** The generation contained `call:remember`. The follow-up prompt contained `response:remember`. `gemma.memory.txt` gained `fact <<` / `the proof token is amber-lamp-429`. That fact is still the first block after the seat history was restored.
- **stop.** The generation contained `<|tool_call>call:stop{}`. `iris_status.txt` gained `stop voice`. The resident and port 8765 stayed up. The HTTP body on that inject was `voice` (a follow-up). After the runner-h merge below, the call stays in the answer and there is no follow-up.
- **next.** On the long replay the model answered the work line and did not emit `next` (that replay includes turns that say not to call a tool). On a short memory, the same listener stored `work <<` / `Say one short sentence: the amber lamp is on.` The follow-up prompt contained `response:next`. The short file was a copy-aside. The 59-pair seat history was restored afterward.
- **place.** That short-memory turn contained `response:place`. A direct `run_place()` while pid 2636 was listening reported `cuda NVIDIA GeForce GTX 1060 6GB; vulkan NVIDIA GeForce GTX 1060 6GB; same; brain post; listener up; flip yes`. The line has no computer name. The spoken follow-up on the short-memory POST said the port was not accepting. The tool report above is the one `place()` prints while the listener is idle.

## Quiet drain → Iris

After `next` stored the work line, no further proof POST was sent. About 75 seconds later:

- The work line was gone.
- `iris_outbox.txt` was replaced with `Next work added: Say one short sentence: the amber lamp is on.`
- `iris_status.txt` ends with `work Say one short sentence: the amber lamp is on.` and `say Next work added: Say one short sentence: the amber lamp is on.`

Iris already speaks `iris_outbox.txt` (`assistant.py --iris-outbox`). The status lines are the work/stop/say log. No audio was played on this seat.

## Local bot

`grok_local_bot.py` places the turn with `gemma.place`: POST if the port accepts, `gemma.py` if this computer has a CUDA device and the port is closed, `qwen.py` otherwise. An image on the CPU row is refused. There is no coordinator file and no online bridge. That routing was checked offline (`turn_argv('resident')` is `gemma.py`). It was not given a second live generate in this proof. The injects above are the live brain.

## After the runner-h merge

`origin/runner-h` is merged on this branch. Gemma `stop` still writes `iris_status.txt` and drops one matching work line. It does not ask the model again, and it does not unload the resident or close 8765. The voice seat ends the local organism from the call left in the answer.

Checked with `tool_turn` on temp files (the same function a text POST runs after Gemma emits the call). The listener was not restarted.

- `call:stop{}` is the answer. Status line `stop voice`.
- `call:stop` with line `amber lamp later` is the answer. That work line is dropped. Status line `stop amber lamp later`.
- The system header declares `place` and lists `Work waiting`. It does not declare `devices`.
- `place()` has no hostname branch.
