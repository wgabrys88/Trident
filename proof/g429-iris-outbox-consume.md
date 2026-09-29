# G429 Iris `iris_outbox` consume (mouth only)

Written 2026-09-29 (UTC+2). Iris seat checkout `cursor/iris-outbox-consume-4b6b` on `runner-h`. No live mic. No `:8765` bind, kill, or restart on Iris.

## Contract

See `proof/README.md` — one UTF-8 line in `iris_outbox.txt`, written by PE quiet idle on the brain machine.

## Consumer

```powershell
$env:TRIDENT_IRIS_OUTBOX = "\\192.168.16.31\Users\px-wjt\Downloads\Jarvis\Trident\iris_outbox.txt"  # when SMB is up
.\.venv\Scripts\python.exe .\assistant.py --iris-outbox
```

Flag alone uses `TRIDENT_IRIS_OUTBOX` or repo-root `iris_outbox.txt`. Optional path: `--iris-outbox .\iris_outbox.txt`.

Order: read trimmed line → clear file → `mouth.py` on default speakers (`mouth out: default`). Missing or empty file: exit **0**, stderr `iris outbox missing` or `iris outbox empty` — no speak.

## Local proof (planted line)

```powershell
Set-Content -Path .\iris_outbox.txt -Value "G429 iris outbox consume proof line.`n" -Encoding utf8
.\.venv\Scripts\python.exe .\assistant.py --iris-outbox 2> .\proof\g429-iris-outbox-consume.log
Get-Content .\iris_outbox.txt   # expect empty
```

**2026-09-29 run:** exit 0. Stderr log `proof/g429-iris-outbox-consume.log` shows `iris outbox consumed`, `mouth 1 chunk(s)`, `mouth out: default`, chatterbox resident speak of the planted line. `iris_outbox.txt` was empty after the run.
