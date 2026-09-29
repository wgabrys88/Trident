# G429 PE placement — devices, one brain, honest fail

Written 2026-09-29. The listener on `:8765` was not restarted. `gemma.py --stop` was not run. No POST was sent. The mouth was not started.

## What changed

`assistant.py` decides once, from the CUDA device, the Vulkan device, and one TCP connect. The computer name is not an input.

`--nvidia` names one peer (`--url`, or `TRIDENT_NVIDIA_URL`). If that port accepts, the turn is one POST to that URL. If it does not, the process exits `peer missing`. It does not start `qwen.py` and it does not start a local Gemma for that failure.

With no peer named, an accepting `127.0.0.1:8765` is a POST to that listener. With no listener and a CUDA device, the turn is the local Gemma resident. With no listener and no CUDA device, the turn stays on `--brain` (default `qwen`).

The mouth stops the resident only when this turn's brain is on this computer and the two device names match. A peer on another address does not. `devices` reports the same fact: CUDA, Vulkan, adapter, whether `127.0.0.1:8765` is accepting, and flip. `grok_local_bot.py --inbox` exits `peer missing` when `0.0.0.0:8765` is down. It no longer starts `--drop`.

Voice, mouth, and ASR files were not edited.

## How this was checked

Injected device names and an injected connect, then the real devices on this machine. The real checks were TCP connects. They did not send HTTP.

- Listener up, both names `NVIDIA GeForce GTX 1060 6GB`: brain `post`, `local`, flip, script `nvidia_client.py`. The qwen flag is not the script.
- No listener, CUDA present, same names: brain `resident`, flip, script `gemma.py`.
- No listener, no CUDA, Vulkan `Intel(R) Iris(R) Xe Graphics`: brain `cpu`, no flip, script `qwen.py`.
- Peer `192.0.2.1:8765` closed, while a connect to `127.0.0.1:8765` would have succeeded: brain `missing`. The only connect attempted was `('192.0.2.1', 8765)`. `run_brain` exited 2 and called neither `say` nor `offload`.
- Peer `192.0.2.9:8765` accepting, local adapters the same name: brain `post`, `peer`, no flip.
- URL host `192.168.16.31`, which is a local interface address: brain `post`, `local`, flip.
- URL host `brain.example`: brain `post`, `peer`, no flip. A host that is not an interface address is not treated as this computer.
- Different adapter names, listener up: `flip no`, listener `up`.

On this machine, `place(None)` was `brain post local cuda NVIDIA GeForce GTX 1060 6GB vulkan NVIDIA GeForce GTX 1060 6GB same flip`, URL `http://127.0.0.1:8765/`. `place("http://127.0.0.1:9/")` was `brain missing` and the only connect was `('127.0.0.1', 9)`. `place("http://192.168.16.31:8765/")` was `post`, `local`, flip. `assistant.turn_place` with no peer printed the same post line. `turn_place` with `http://127.0.0.1:9/` printed `brain missing` and exited 2.

`run_devices` printed:

```text
gemma: tool devices cuda NVIDIA GeForce GTX 1060 6GB; vulkan NVIDIA GeForce GTX 1060 6GB; same; brain post; listener up; flip yes
```

That line has no computer name.

`release_shared_gpu(False)` did not call stop. `release_shared_gpu(True)` called the patched stop. The real `stop_resident` was put back and was not the function that ran.

Before and after: `gemma.memory.txt` sha256 `b29679a284c7a9177ef2345c9edf9f2cfe67d78ccfbae42251c85341cc30e4a2`. `gemma.lastprompt.txt` sha256 `005cdf0ed488c4a6b9131acab38eaa6af3875dc2a56b517264d4b2d2b94739d5`. `grok_bot_spawn.txt` absent. `gemma.pid` pid 2064, state `ready`, same fingerprint. `netstat` showed `0.0.0.0:8765` LISTENING, pid 10044. One `gemma-brain.exe`, pid 2064. The worker process stayed pid 10044, parent 11060.

`py_compile` of `gemma.py`, `assistant.py`, `nvidia_client.py`, and `grok_local_bot.py` succeeded.

## Not run

A spoken turn on this PC. The place line says flip, and `speak_raw` would stop this resident before the mouth. That stop was not called. The worker does not flip between turns.

Iris hear, mouth, and ASR were not edited. A machine with no CUDA device still uses `--brain`. Loading Gemma there is that seat's work.
