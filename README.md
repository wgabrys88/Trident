# Trident

Local voice assistant: C++ HTTP bus and mouth, Python brain (Gemma) and ear (Nemotron ASR).

## Run

```text
python install.py turbo
build\bin\trident-host.exe turbo
build\bin\trident-host.exe turbo inbox
```

Environment:

- `TRIDENT_PORT` (default `8765`)
- `TRIDENT_IDLE_SECONDS` (default `1800`)
- `TRIDENT_VULKAN_DEVICE` — force GPU index
- `TRIDENT_FLASH_ATTN` — set `1` to enable flash attention in brain (default off)
- `TRIDENT_ALLOW_PYTHON` — set `0` to disable the `python` tool

## Layout

| Component | Binary / script |
|-----------|-----------------|
| Bus + supervisor | `build/bin/trident-host.exe` |
| Mouth (TTS + playback) | `build/bin/trident-mouth.exe` + `chatterbox.exe` (in-process Vulkan) |
| Brain (wire-up) | `gemma/build/Release/gemma-brain.exe` (target); `python brain.py` legacy |
| Ear | `python ear.py <server>` or `--inbox` |

Python is only required for install, model conversion scripts, brain, ear, and v3 tokenizer subprocesses inside chatterbox.
