# G429 run.py spoken stop

Date: 2026-09-29. Seat: Iris. Checkout: `C:\Users\eb-wjt\Downloads\Jarvis\Trident`.

`run.py start` is the live microphone. This run injected ASR text so nobody spoke. The sentence is not the word `stop`. Gemma called `stop`. The organism exited. Nothing bound, killed, or restarted `:8765`.

## Command

```powershell
.\.venv\Scripts\python.exe -u .\run.py start inject proof\g429-run-stop-turn.txt
```

Turn: `I am finished. Shut down the voice and stop listening.`

Exit 0. About 5 s. `organism_stop` is false for that sentence and true only for a `call:stop` tool call.

## Log

```text
iris: brain http://192.168.16.31:8765/
iris: inject
assistant: place brain post peer cuda none vulkan Intel(R) Iris(R) Xe Graphics unknown
assistant: nvidia
iris: up pid 12544
<|tool_call>call:stop{}<tool_call|>
assistant: tool stop
assistant: mouth stopped
iris: stopped
```

`assistant: nvidia` is the POST. The reply's tool call is what ended the loop.

## After

| Check | Result |
| --- | --- |
| `192.168.16.31:8765` | accept |
| `127.0.0.1:8765` | timeout |
| `assistant.pid`, `iris.pid`, `mouth.pid`, `vad.pid` | gone |

The mouth resident already up before the run (pid 4920) stopped with the organism.
