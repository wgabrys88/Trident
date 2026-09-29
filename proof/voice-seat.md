# Voice seat

Date: 2026-09-29. Seat: Iris. Checkout: `C:\Users\eb-wjt\Downloads\Jarvis\Trident`. No microphone. Nothing bound, stopped, or restarted `:8765`.

One start path. A peer that accepts is the brain. A closed port is this PC. The computer name is not a key. `status`, `work`, and `say` are text. The mouth speaks here.

## Offline

```powershell
.\.venv\Scripts\python.exe proof\voice_seat_check.py
```

`STATUS PASS`. A plain "Please stop listening." is not a stop. A `call:stop` tool call is. `The lamp is on.` is nano/en. `Drzwi są zamknięte.` is v3/pl. `http://127.0.0.1:9/` is `assistant: alone` and `brain cpu`. `http://192.168.16.31:8765/` is `assistant: lan` and `brain post`. That check does not generate.

## LAN inject

Peer `http://192.168.16.31:8765/` was accepting. Seat file: say `Drzwi są zamknięte.`, status `peer ready`, work `The kettle finished.` Turn file: `Reply with exactly two short sentences. The lamp is on. The door is shut.`

```powershell
$env:TRIDENT_IRIS_SEAT = "...\proof\voice-seat-lan-seat.txt"
$env:TRIDENT_IRIS_OUTBOX = "...\proof\voice-seat-no-outbox.txt"
.\.venv\Scripts\python.exe -u .\run.py start --url http://192.168.16.31:8765/ inject proof\voice-seat-turn.txt
```

Exit 0. Log `proof/voice-seat-lan.log`. The redirect mangled Polish glyphs. The mouth tags are:

```text
assistant: lan
assistant: say Drzwi są zamknięte.
mouth: v3 pl | ...
assistant: status peer ready
assistant: nvidia
assistant: work The kettle finished.
assistant: nvidia
Hello, Wojciech.
assistant: lan
assistant: nvidia
The lamp is on. The door is shut.
mouth: chatterbox settings changed
mouth: nano en | The lamp is on. The door is shut.
iris: stopped
```

The short line `Hello, Wojciech.` was spoken as v3/pl. The following English answer switched the resident to nano/en.

## Alone inject

Same turn file. Peer `http://127.0.0.1:9/`. Seat: say `The lamp is on.`, status `local ready`, work `The kettle finished.`

```powershell
.\.venv\Scripts\python.exe -u .\run.py start --url http://127.0.0.1:9/ inject proof\voice-seat-turn.txt
```

Exit 0. Log `proof/voice-seat-alone.log`.

```text
assistant: peer unreachable
assistant: place brain cpu local cuda none vulkan Intel(R) Iris(R) Xe Graphics unknown
assistant: alone
assistant: say The lamp is on.
mouth: nano en | The lamp is on.
assistant: status local ready
assistant: qwen
assistant: work The kettle finished.
assistant: qwen
assistant: alone
assistant: qwen
mouth: nano en | The lamp is on. The door is shut.
iris: stopped
```

No `assistant: nvidia` and no `assistant: lan` on this run.

## Stop

```powershell
.\.venv\Scripts\python.exe -u .\run.py stop
```

Exit 0. `qwen: sense stopped`, `mouth: chatterbox stopped`, `iris: stopped`. `assistant.pid`, `iris.pid`, `mouth.pid`, `sense.pid`, and `vad.pid` were absent. `iris_status.txt` held `peer ready` and `local ready`.

| Check | Result |
| --- | --- |
| `192.168.16.31:8765` | accept |
| `127.0.0.1:8765` | down |

## Remote branches

`main` and `runner-h` were not moved. No force-push. `origin/cursor/pe-brain-finish-df7c` stays: `81a90a5` and `d7f279c` are not on `runner-h`. Nineteen other `cursor/*` tips were ancestors of `origin/runner-h` and were deleted.
