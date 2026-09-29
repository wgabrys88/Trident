# G429 Iris closed-mic inject loop

Date: 2026-09-29. Checkout: `C:\Users\eb-wjt\Downloads\Jarvis\Trident`. Branch: `cursor/closed-mic-loop-1bce`. Parent: `runner-h` at `900860d`.

Voice seat only. No live mic. Nothing bound `:8765` on Iris. Leave-healthy PE listener `http://192.168.16.31:8765/` was not stopped for these runs.

## One command

Continuous closed-mic listen→speak: one process, brain placed once, each injected line (or `---`-separated block) is POST→stream mouth→wait for the next line. No VAD, no mic gate.

```powershell
$env:TRIDENT_NVIDIA_URL = "http://192.168.16.31:8765/"
.\.venv\Scripts\python.exe .\assistant.py --nvidia --url http://192.168.16.31:8765/ --timeout 180 --inject .\proof\inject-two-turn.txt
```

Flag alone reads stdin until EOF (use a `---` line between turns when typing interactively):

```powershell
.\.venv\Scripts\python.exe .\assistant.py --nvidia --inject
```

`quit`, `exit`, or `stop` as the only text of a turn ends the loop. `--once` stops after the first turn. `--text` stays one shot.

## Fail closed

```powershell
.\.venv\Scripts\python.exe .\assistant.py --nvidia --url http://127.0.0.1:9/ --inject .\proof\inject-two-turn.txt
```

Exit 2. Stderr: `peer missing`. No mouth, no `assistant.pid`.

## Two-turn proof (one process)

Input file `proof/inject-two-turn.txt` (two non-empty lines). Exit 0. Elapsed about 22 s. Stderr shows two `assistant: nvidia stream` blocks and two `mouth out: default` lines in one run. Stdout:

```text
The lamp is on.
Two plus two equals four.
```

After the run, TCP to `192.168.16.31:8765` still connected. Iris had no listener on `:8765`. Raw log: `proof/g429-closed-mic-loop.log`.

Last `nvidia_turn.request.txt` on disk is turn 2 only (each turn overwrites); the log is the record of both POSTs.
