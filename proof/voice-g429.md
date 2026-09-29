# Iris voice seat (G429)

Date: 2026-09-29. Checkout: `C:\Users\eb-wjt\Downloads\Jarvis\Trident`. Branch: `cursor/iris-voice-loop-5fab` off `runner-h`.

This PC's GPU is Intel Iris Xe only. Physical cores measured: 4 (8 logical). `sense.threads` stays 4. An early TCP probe of `http://192.168.16.31:8765/` failed. A later one connected. Placement uses that probe and `gemma/scripts/detect_gpu.ps1` (cuda only when an NVIDIA controller and `nvcc` both exist). No source branch on a computer name, an account name, or `EB-W`.

## Commands

Two-PC listen. VAD stays up. The brain port must accept or the process exits. Chunking and the mouth stay here. No wav is posted.

```powershell
.\.venv\Scripts\python.exe .\assistant.py --nvidia
```

Stop the assistant tree on this PC. It does not open a connection to the brain and it does not bind `:8765`.

```powershell
.\.venv\Scripts\python.exe .\assistant.py --stop
```

`assistant.py` with no flags probes once. Port open: POST, local Vulkan, mouth v3. Port closed and `--nvidia`: exit 2, and `qwen.py` is not called. Port closed and no local CUDA: stderr is `assistant: Qwen`, and the listen mouth is still v3.

Closed mic stays nano:

```powershell
.\.venv\Scripts\python.exe .\assistant.py --text "Say one short sentence." --nvidia
```

Door stays `grok_local_bot.py --wav` with `mouth.py --model nano --no-play`.

## What ran on this PC

| Check | Result |
| --- | --- |
| `--seconds` removed | `assistant.py --seconds 5` exits 2, unrecognized argument. |
| Brain down, `--nvidia` | `brain down http://192.168.16.31:8765/`, exit 2. No `sense.pid`, no `nvidia_turn.request.txt`, no `vad.pid`. |
| Brain down, no `--nvidia`, image | stderr `assistant: Qwen`, then `qwen/sense vision is N/A`. Exit 2. |
| Resident VAD | `vad mic: Microphone Array (Intel® Smart Sound Technology for Digital Microphones)` once, then `resident ready`. Listen card is `vad_run.txt` (`vad.max-ms 30000`). `vad.txt` is still the cable one-shot. |
| Live turn, brain down | The mic returned a 5.6 s utterance. Hear JSON `languages` was empty, so the mouth tag is `en`. Qwen answered. stderr: `assistant: mouth 1 chunk(s)`, `mouth out: default`, Chatterbox v3. Stop hit during that first synthesis. |
| PlaySound default | `mouth.py --play-wav` on `13-38-08-578_chatterbox_out_000.wav` printed `mouth out: default` and exited 0. |
| v3 card | `mouth.txt` `chatterbox.variant v3`, `chatterbox.language pl`, `chatterbox.cfm-steps 10`. `[en] Hello.` and `[pl] Cześć.` both used pid 9776. Vulkan log: device 0 Intel Iris Xe. |
| nano card | After `--model nano`, `chatterbox.cfm-steps 2`, language `en`, a new pid. |
| Hold and stop | With `vad.hold` present, resident pid 8648 wrote no `vad.utterance.txt`. `vad.stop` logged `resident stop pid 8648` and removed the pid. |
| One-shot cable | `vad.exe vad.txt` printed the cable mic, wrote `13-41-00-267_vad_out_000.wav`, and exited 0. |
| Short English pack | One sentence under 65 words is one chunk and does not call `qwen.py`. Four 20-word sentences pack as 60 words then 20. No Qwen. |
| One atom over 65 | Cut into word windows (65, then the remainder). `qwen.py` is not called. |
| `--stop` with nothing running | `stop: nothing started here`, exit 0. |

Hear flags used for that utterance: `hear.py --wav … --language auto --format json --verbatim`. Endpointing and `--stop-history-eou-ms 1200` stay the hear defaults. `--stream` stays off.

## Stream client

`--nvidia` posts `stream: true`. `nvidia_client.iter_stream` yields text pieces and drops the worker's trailing blank line. `StreamFeed` speaks each closed atom. Text before `<channel|>`, and an unclosed `<think>`, stays silent. An atom over 65 English words (55 otherwise) is cut into word windows. The door still posts without `stream`. `--stop` still does not open a connection.

`urllib` `read(n)` keeps pulling HTTP chunks until `n` bytes, so the client reads one chunk at a time.

### Live brain

TCP to `http://192.168.16.31:8765/` succeeded. One POST, `stream: true`, timeout 90 s. The worker returned text, not JSON, and the body ended with the blank line. The only piece arrived at 6881 ms and the stream ended at 6884 ms. It was one chunk: a thinking trace, then `<channel|>Hello from the brain. The rest is still arriving.`

`StreamFeed` on that text yields `Hello from the brain.` and `The rest is still arriving.` It does not yield the thinking trace. This listener did not flush earlier pieces, so the mouth cannot start while the 1060 is still decoding that turn.

### Overlap when pieces arrive

A local worker flushed `Hello there. ` and held the second sentence until v3 synth started. Chatterbox pid 10360, `mouth out: default`, model v3, chunk text `[en] Hello there.` Synth of that sentence started, and `Second sentence is here.` was then written on the still-open body. Synth of sentence 1 finished about 7.2 s later. Second chunk: `[en] Second sentence is here.` The mouth was stopped after the proof. No wav left this PC.

## Blockers

The live `:8765` accepts `stream: true` and returns one chunk after the whole generation. Until that worker flushes token pieces, a two-PC turn still speaks only after the body arrives.

This PC has no CUDA device, so the same-GPU flip does not run. `gemma-brain.exe` here is still one-shot. That is the brain seat.
