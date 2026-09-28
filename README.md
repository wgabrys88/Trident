# Trident — bot scratchbook

Operating picture for Cursor agents and Grok Bot coordinators. Checked-in code outranks this file.

## Now

- Local Grok-bot team on two home PCs, plus the Iris voice door.
- Jarvis (`assistant.py`) is second. New work goes through the file team and the door.

## Seats

- Iris EB-W: microphone. This checkout: `C:\Users\eb-wjt\Downloads\Jarvis\Trident`. Door runs here.
- NVIDIA PE-DMLW: GPU and speakers. No microphone.
- LAN worker: `http://192.168.16.31:8765/`
- That process is already bound `0.0.0.0:8765`. Leave the bind alone. Do not restart it. Do not bind a second listener.

## Branch rules

- Develop on `runner-h` only. Branch from `runner-h`. PR into `runner-h`.
- `main` is the old trunk. Leave it.
- No force-push. Never delete `main` or `runner-h`.
- SPOC merges. Agents open the PR and stop.
- One writer per checkout.

## Cursor spawn lock

- My Machines only: `trident-iris`, `trident-nvidia`.
- Model `grok-4.7`. Context 256k. `reasoning_effort` `xhigh`. `fast=false`.

## Unattended door

Worker must already be up on NVIDIA. One command on Iris. The door does not start `nvidia_worker.py`.

```powershell
$env:TRIDENT_NVIDIA_URL = "http://192.168.16.31:8765/"; .\.venv\Scripts\python.exe .\grok_local_bot.py --wav .\FILE.wav
```

`hear.py --wav` transcribes the file. Coordinator, then reasoner, append `grok_bot_history.txt`. Reasoner POSTs to `TRIDENT_NVIDIA_URL`. `mouth.py --no-play` writes the reply wav. Microphone stays closed. Speakers stay closed.

## Key files

- `grok_local_bot.py` — file team (`--proof`, `--role`) and the door (`--wav`)
- `nvidia_worker.py` — stateless Gemma worker on NVIDIA
- `nvidia_client.py` — request file plus POST
- `gemma.py` — brain; tools `hello` and `cursor` (log `grok_bot_spawn.txt`)
- `hear.py` — door uses `--wav`
- `mouth.py` — door uses `--no-play`
- Team: `grok_bot_history.txt`, `grok_bot_request.txt`, `grok_bot_response.txt`
- Worker slot: `nvidia_turn.request.txt`, `nvidia_turn.response.txt`
- Door record: `iris-door.txt`

## Proven

- PROVEN: file team (`grok_local_bot.py --proof`) and the unattended wav door (`--wav` with the worker already up). Both are on `runner-h`.
- UNPROVEN: live mic 30s auto pipeline. Do not claim it. Do not run it.

## While the user is away

- Quiet. No speaker playback.
- Max-util: send heavy turns to the NVIDIA worker already on the GPU.
- No new LAN daemon.
- No microphone.

## License

MIT. See `LICENSE`.
