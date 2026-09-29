# G429 Iris closed-mic start and stop

Date: 2026-09-29. Checkout: `C:\Users\eb-wjt\Downloads\Jarvis\Trident`. Branch: `cursor/iris-start-stop-dc91`. Base: `runner-h` at `6e7354c`.

Voice seat only. No live mic and no VAD. Nothing bound `:8765` on Iris. Leave-healthy PE listener `http://192.168.16.31:8765/` was not stopped.

## Commands

`start.py` runs `assistant.py --nvidia --timeout 180 --inject` and waits on stdin. The peer URL is `--url`, else `TRIDENT_NVIDIA_URL`, else `http://192.168.16.31:8765/`. A turn is text, then a line that is only `---`.

`stop.py` runs `assistant.py --stop` and `mouth.py --stop`. It does not open a socket.

```powershell
$env:TRIDENT_NVIDIA_URL = "http://192.168.16.31:8765/"
.\.venv\Scripts\python.exe -u .\start.py
.\.venv\Scripts\python.exe .\stop.py
```

On this venv, `python.exe` is a redirector. `assistant.pid` is that process's child. Start waits until that file changes to a live Python. Stop `taskkill /T` on the redirector, then the existing `--stop` paths.

## Run

Before: TCP to `192.168.16.31:8765` accepted. TCP to `127.0.0.1:8765` timed out. No `nvidia_worker.py` on Iris. Mouth resident was already up (`chatterbox.exe` pid 9196, nano/en). Stale `assistant.pid` 8576 was dead.

Start with `TRIDENT_NVIDIA_URL` set and no `--url`. Stderr:

```text
iris: brain http://192.168.16.31:8765/
assistant: place brain post peer cuda none vulkan Intel(R) Iris(R) Xe Graphics unknown
iris: up pid 340
```

One stdin turn: `Reply with exactly: The lamp is on.` then `---`. Stderr continued:

```text
assistant: nvidia stream
mouth out: default
assistant: mouth
mouth: chatterbox pid 9196 resident
mouth: nano en | The lamp is on.
assistant: mouth 1 chunk(s)
```

Stdout after the process exited: `The lamp is on.`

`stop.py` exit 0. Stderr: `assistant: stopped`, `mouth: chatterbox stopped`, `iris: stopped`. The start waiter exited 1 because `taskkill` ended it.

After: TCP to `192.168.16.31:8765` still accepted. TCP to `127.0.0.1:8765` still timed out. `assistant.pid`, `iris.pid`, and `mouth.pid` were gone. `chatterbox.exe` was not running. No `nvidia_worker.py` on Iris.

Raw log: `proof/g429-iris-start-stop.log`.
