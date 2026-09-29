# G429 PE seat forward slice — Iris outbox scratch

Written 2026-09-29 (UTC+2).

## Change (Brain only)

`gemma.py`:

- `iris_outbox.txt` (+ `.tmp`) in repo root, gitignored.
- `write_iris_outbox()` — one atomic line after a successful quiet idle speakable answer.
- Module docstring notes the handoff.

No Python coordinator, no hostname branches, no Iris/voice edits.

## Offline check

```powershell
cd C:\Users\px-wjt\Downloads\Jarvis\Trident
.\.venv\Scripts\python.exe -m py_compile .\gemma.py
.\.venv\Scripts\python.exe -c "from gemma import write_iris_outbox; write_iris_outbox('Say one short sentence: G429 quiet proof lamp is green.')"
Get-Content .\iris_outbox.txt
```

Expect stderr `gemma: iris outbox wrote iris_outbox.txt` and a one-line file matching the Part A idle model text.

## Live idle + outbox

Part A drain ran on the worker **before** this commit was loaded. After an intentional cutover (same recipe as `proof/g429-pe-seat-merge.md`), the next quiet idle that produces a speakable answer will also refresh `iris_outbox.txt`.

## Iris

Read contract: `proof/README.md`.
