# Voice-seat act (G429 evening)

Date: 2026-09-29. Checkout: `C:\Users\eb-wjt\Downloads\Jarvis\Trident`. Branch: `cursor/voice-act-loop-8fe8`. Act code: `83a7366`.

Closed-mic only. No live mic. Nothing bound `:8765`. `mouth.py --stop` ran after the spoken checks. TCP to `http://192.168.16.31:8765/` still accepted. This PC had no LISTENING socket on `:8765`.

One injected text can speak a brain reply and then run one local act. A text that is only the act does not place the brain and does not POST. The act line is not sent to the worker.

## Fail closed

Each case was one process: `assistant.py --nvidia --url http://192.168.16.31:8765/ --timeout 30` and the text below. Exit 2. Stdout empty. Stderr was one line.

| Text | Stderr |
| --- | --- |
| `act: nope` | `unknown act nope` |
| `act: time extra` | `time takes no words` |
| `act: note` | `empty note` |
| `act: time` then `act: note hi` | `one act` |
| `Say hello.` then `act: time` then `more` | `act line last` |
| `act:` | `empty act` |
| `act: note` plus 201 `x` | `note over 200 characters` |
| `act: next extra` | `next takes no words` |
| `act: next` (no note file yet) | `no note` |

`nvidia_turn.request.txt` sha256 stayed `3e38991cc1684122ade43d804457b86a2a4d5e0a45eaa8cfde0eb3a154f1e260`. `assistant.note.txt` stayed absent. `mouth.pid` stayed absent. `assistant.session.txt` sha256 stayed `7983711687a9edda70a7e1f0bd4847557d01759da607298ea0ad11ef59c5eeff`. No `assistant: place`. No `assistant: nvidia`.

A plain lamp sentence splits to that sentence and no act. `Say the lamp is on.` plus `act: note The lamp is on.` splits to the sentence and `('note', 'The lamp is on.')`.

## Local acts (peer named, no POST)

Mouth was not resident. The time turn started chatterbox pid 12840, nano, `en`. Later acts adopted that pid. `--nvidia` and the peer URL were set. The request sha256 did not change. `assistant.history.txt` stayed absent.

```powershell
.\.venv\Scripts\python.exe .\assistant.py --nvidia --url http://192.168.16.31:8765/ --text "act: time" --timeout 30
```

Exit 0. Elapsed 5.418 s. Stdout: `The time is 17:45.` Stderr: `assistant: local act`, `assistant: act time`, `mouth out: default`, `assistant: mouth 1 chunk(s)`, `mouth: nano en | The time is 17:45.` Wav `17-45-21-832_chatterbox_out_000.wav`, 24 kHz mono, 2.12 s.

```powershell
.\.venv\Scripts\python.exe .\assistant.py --nvidia --url http://192.168.16.31:8765/ --text "act: note The lamp is on." --timeout 30
```

Exit 0. Elapsed 4.741 s. Stdout: `Noted. The lamp is on.` `assistant.note.txt` gained `The lamp is on.` Wav `17-45-45-095_chatterbox_out_000.wav`, 24 kHz mono, 2.04 s.

```powershell
.\.venv\Scripts\python.exe .\assistant.py --nvidia --url http://192.168.16.31:8765/ --text "act: next" --timeout 30
```

Exit 0. Elapsed 4.472 s. Stdout: `The note is The lamp is on.` No second copy of the fact was typed, and there was no POST. Wav `17-45-49-858_chatterbox_out_000.wav`, 24 kHz mono, 2.12 s.

## Question, then one act

The `--text` value was two lines. The process was `assistant.py --nvidia --url http://192.168.16.31:8765/ --timeout 180`.

```text
Reply with exactly one short sentence. The lamp is on.
act: note The door is shut.
```

Exit 0. Elapsed 9.668 s. Stdout:

```text
The lamp is on.
Noted. The door is shut.
```

Stderr order: `assistant: place brain post peer ...`, `assistant: nvidia stream`, `mouth out: default`, `assistant: mouth`, `mouth: nano en | The lamp is on.`, `assistant: mouth 1 chunk(s)`, then `assistant: act note`, then `mouth: nano en | Noted. The door is shut.`

The request body posted only the question:

```text
Reply with exactly one short sentence. The lamp is on.
```

The response file was `ok` and `The lamp is on.` The act line was not in the request. `assistant.note.txt` after this turn:

```text
The lamp is on.
The door is shut.
```

Wavs: `17-46-19-749_chatterbox_out_000.wav` (24 kHz mono, 1.0 s) and `17-46-23-446_chatterbox_out_000.wav` (24 kHz mono, 2.24 s). Same resident pid 12840.

## Stream path still early-flushes

No mouth. `nvidia_client.iter_stream` to the same URL, text `Reply with exactly two short sentences. The lamp is on. The door is shut.`

| Piece | Offset | Text |
| --- | --- | --- |
| 1 | +1688 ms | `The lamp is on. ` |
| 2 | +1793 ms | `The door is shut.` |

N=2. Total 1796 ms.

`mouth.py --stop` then printed `mouth: chatterbox stopped`. A later TCP connect to `192.168.16.31:8765` succeeded. `assistant.session.txt` was restored to the sha256 from before these runs.
