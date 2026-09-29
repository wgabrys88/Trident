# G429 PE merge onto runner-h

Written 2026-09-29. The listener on `0.0.0.0:8765` was not restarted. `gemma.py --stop` was not run. No POST was sent. `main` was not moved.

## Merged

The PE brain stack was already one parent chain. Iris voice had landed on `runner-h` first, so PRs 22, 23, and 24 were dirty against that base. Landing the tip once is the dependency order and the single conflict.

| PR | Branch | Head | What |
| --- | --- | --- | --- |
| 22 | `cursor/pe-resident-gemma-af6c` | `ecb9f4a0ee67cad1a823b05254670aebf0d1ceb4` | One resident Gemma for the text worker |
| 23 | `cursor/pe-brain-memory-a6f7` | `b4ff0a70bb6444b6f003eb84429303801ed7378d` | `gemma.memory.txt` on text turns |
| 24 | `cursor/pe-memory-live-0233` | `dce6096ad4e28fc5ea5d0449aeb6e6d9dfaf6469` | Live memory proof, plus sentence flush `cac5b82adae99a00e3c5753583cf7ba57abf38be` |
| 26 | `cursor/pe-gemma-tools-e2c5` | `b4cdf1e1fd51fb731b0f53417a23e0ffeb2a4212` | `cursor` only when the model calls it |
| 28 | `cursor/pe-place-devices-741f` | `551bd26cc779464187ea21446106f3b40dff7678` | Place the brain from devices. Fail when the peer is down |

Also on that chain, not separate PRs: `13dc76d76c451ffdf2ce8b34d25494ca2ae8d796` (speakable replay and `devices`), `c942b2da66dc3a8d133f6647c7bae302e5a4eb77` (live memory proof), `4a0a20c4b7cb51ecbdfeb36ca63de158c7661a85` (placement code under the proof commit).

Merge commit: `bced695af076d2eb2b32a2372171dddc71faa888`

Parents:

- `123050e556ed193cc1795a5ed182874b510e4ba1` — `runner-h` before this merge
- `551bd26cc779464187ea21446106f3b40dff7678` — PE tip

`origin/main` stayed `d535349a8b99fcdd0e42efb14ec269e0d59c54b1`.

Note commit: `b85f8293e41c385bd209c03387b9344460100322`. The commit that names that id is its child. After the push, that child is `runner-h`.

## Left with the Iris twin

Not merged from this seat. Both heads were already inside `runner-h` before the PE merge. The PRs stay open because their bases are not `runner-h`.

| PR | Head | Base at the time of this note |
| --- | --- | --- |
| 25 | `9274d632c907926066f4ab2940e14dce113cb4d3` | `cursor/iris-voice-loop-5fab` |
| 27 | `123050e556ed193cc1795a5ed182874b510e4ba1` | `cursor/lang-span-mouth-2f02` |

Voice commits already on `runner-h` before this merge: `880663714dc76c11fa25379b9eca4bd35d9a0410`, `940d50ff11fc2e626a115b29cd8ad6db2f25a8d0`, `f03b1f147c42d7f89b1bc31b5f80c8de6a64e68d`, `35ee885e370897f79c45df5e58fb2abd1735c607`, `9274d632c907926066f4ab2940e14dce113cb4d3`, `65cfd44680fff71a2c2cee9b355315bfc9fdfd6f`, `123050e556ed193cc1795a5ed182874b510e4ba1`.

## How the overlap was resolved

Conflicts were `README.md`, `assistant.py`, and `nvidia_client.py`. `.gitignore` and `grok_local_bot.py` auto-merged.

`gemma.py`, `gemma.txt`, `gemma/src/brain.cpp`, and `nvidia_worker.py` are the PE tip, byte for byte.

`assistant.py` keeps the Iris listen loop: resident VAD, language spans, `StreamFeed`, history pairs inside the POST. The brain decision is `gemma.place`: CUDA name, Vulkan name, one TCP connect. No computer name and no built-in host. `--nvidia` names `--url` or `TRIDENT_NVIDIA_URL`. An empty or closed peer exits `peer missing` and does not start Qwen or a local Gemma. A POST to another address streams and speaks while later tokens arrive. The same-GPU path waits for the whole reply, does not set `stream`, and then `release_shared_gpu` stops the resident before the mouth. The worker does not flip between turns.

`nvidia_client.py` keeps `iter_stream` (one HTTP chunk at a time). The baked-in `192.168.16.31` default is gone. A connect failure is `peer missing`.

`grok_local_bot.py` keeps `mouth.speak_pieces` for the door. Inbox exits `peer missing` when `:8765` is down and does not start `--drop`. `--proof` does not require a cursor spawn. The reasoner prompt does not order that call.

## runner-h in words

`runner-h` has both seats' day-to-day code. Iris hears with resident VAD and speaks with the span mouth, including speak-while-decode when the brain is on another address. PE serves one resident Gemma on the worker, replays `gemma.memory.txt` on text turns, flushes speakable sentences on `stream: true`, runs `cursor` only when the generation contains that call, and places the turn from device names. `main` is still the old flag-manual tip.

## Leave-healthy

Not a cutover. `0.0.0.0:8765` stayed LISTENING, pid 10044, parent 11060, started 2026-09-29 15:30:22. `gemma-brain.exe` pid 2064, started 14:23:35, `gemma.pid` state `ready`, same fingerprint as before the checks. The merge was built in another worktree, so this checkout's files were not swapped under that process.

The worker file on disk was last written at 15:22, before that process started, and this merge does not change `nvidia_worker.py` relative to the PE tip. `bufsize=0` is in that file. The process loaded whatever was on disk at 15:30. It was not restarted to prove that.

## Checks

`py_compile` of `assistant.py`, `nvidia_client.py`, `gemma.py`, `nvidia_worker.py`, and `grok_local_bot.py` succeeded.

Injected `gemma.place` (no POST, patched `stop_resident`):

- Closed `192.0.2.1:8765`: `brain missing`. The only connect was that host.
- Accepting peer on another address, same adapter names: `post`, `peer`, no flip.
- Accepting `192.168.16.31:8765`, which is a local interface address: `post`, `local`, flip.
- `127.0.0.1:8765` accepting, same names: `post`, `local`, flip.
- No listener, CUDA present, same names: `resident`, flip, script `gemma.py`.
- No listener, no CUDA, Vulkan Iris Xe: `cpu`, no flip, script `qwen.py`.
- Different adapter names, listener up: `post`, `local`, no flip.
- `release_shared_gpu(False)` did not call stop. `release_shared_gpu(True)` called the patched stop. The real `stop_resident` was put back.

`place(None)` on this machine, real TCP and real device names: `brain post local cuda NVIDIA GeForce GTX 1060 6GB vulkan NVIDIA GeForce GTX 1060 6GB same flip`. After that, pid 10044 was still LISTENING and pid 2064 was still `ready`.

## Later Composer reinstall

Do not wipe and do not reinstall yet. Wait until both twins report `runner-h` ready.

When that cutover is intentional:

- Rebuild `gemma-brain.exe` from `gemma/src/brain.cpp` on this `runner-h`. Card stays ctx 65536, flash-attn off, KV f16, image tokens 70 and 280, poll 100. The live exe was not replaced.
- Start the worker again only then, so the process is the merged listener. If `:8765` is already accepting, a new worker leaves it alone. Do not kill pid 10044 before that decision.
- Do not delete `.venv`, the GGUFs, or `gemma.memory.txt`.
- Iris `assistant.py --nvidia` needs `--url` or `TRIDENT_NVIDIA_URL`. An empty peer exits `peer missing`.
- A same-GPU assistant turn stops the resident before the mouth. Do not run that on this PC until the cutover is the point.
