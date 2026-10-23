# Trident

Trident is a living program. `main.py` is the mind, the eye and the tools. `win32.py` is the hand. `story.txt` and `ledger.md` are the memory. A file `skills/NAME.py` defining `NAME(engine, *args)` becomes the tool `--NAME`.

The brain is Grok through the Cursor CLI. The default is `grok-4.7-medium-fast`. `TRIDENT_MODEL` overrides it. A `grok-4.7` slug named in the sentence wins. Each wake spawns a new agent and does not pass `--resume` or `--continue`.

Retina (Fara 1.5, a computer-use agent) is the only image-recognition tool. Give it one goal that names the window; it answers with one action whose coordinate (0-1000 of the capture) is the target point. It does not return lists or boxes. For small targets (icons, links, swatches) `--retina "zoom GOAL"` asks again on a crop of the same capture around the first point and returns the finer point. Jev (`typesafe/jev-1.14` on OpenRouter) answers typed decisions.

```
.\.venv\Scripts\python.exe .\main.py --work . --mind "SENTENCE" 45
```
