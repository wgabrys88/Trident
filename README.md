# Trident v2

Gemma lives on this computer. She hears the room, sees the screen, moves the mouse, and talks to
Wojciech over Telegram: text messages and voice calls, in both directions. Everything runs locally
on a GTX 1060 6 GB and 32 GB of RAM. Nothing is compiled.

| Organ | What | Runs on |
|---|---|---|
| brain | Gemma 4 E2B-it (Q4_0 + vision projector) through `llama-server.exe` | GPU |
| eyes | screenshot, Gemma's 1000-grid to pixels | CPU |
| hands | mouse, keyboard, PowerShell | CPU |
| ears | Silero VAD + Nemotron ASR (`nemo-speech.exe`) | CPU |
| mouth | Chatterbox nano, Wojciech's reference voice | CPU |
| telegram | Telethon + ntgcalls, session borrowed from Telegram Desktop | - |
| memory | facts, last turns, quiet flag (`state/memory.json`) | - |

## Install

Requirements: Windows 10/11, Python 3.11 or newer on PATH, an NVIDIA driver that supports CUDA 12,
Telegram Desktop logged into Gemma's account **and** Wojciech's account (Gemma's is any account
whose id is not `owner.telegram_id`), VB-Audio Virtual Cable for the room microphone.

```powershell
python install.py
```

`install.py` creates `.venv`, installs the packages, downloads the llama.cpp CUDA release,
the NeMo-Speech.cpp release, the four model files and the Chatterbox nano weights. It is safe to
run again. `reference.wav` (10 to 15 s of the voice Gemma should speak with) sits next to it.

## Run

```powershell
.venv\Scripts\python trident.py          # start; Ctrl+C stops
.venv\Scripts\python trident.py say "what do you see?"
.venv\Scripts\python trident.py call
.venv\Scripts\python trident.py hang
.venv\Scripts\python trident.py stop
```

While Trident runs, Telegram Desktop is closed (Trident holds Gemma's session). It is reopened on stop.

Each organ also runs alone, which is how you test one region without the rest:

```powershell
.venv\Scripts\python -m organs.brain "What is the capital of France?"
.venv\Scripts\python -m organs.eyes
.venv\Scripts\python -m organs.hands press win-d
.venv\Scripts\python -m organs.ears            # listen to the room
.venv\Scripts\python -m organs.ears clip.wav   # transcribe a file
.venv\Scripts\python -m organs.mouth "Hello."
.venv\Scripts\python -m organs.telegram
```

## Files

```
config.toml        every setting, one table per organ
trident.py         the organism: senses -> one turn -> reply
organs/brain.py    Gemma's native prompt format, tool loop, vision queries
organs/eyes.py     screenshot and grid geometry
organs/hands.py    SendInput and PowerShell
organs/ears.py     VAD segmenter, room microphone, transcription
organs/mouth.py    text to speech
organs/telegram.py the line: calls, messages, photos, desktop camera
organs/memory.py   what persists between turns
install.py         downloads
state/             logs, memory.json, inbox.txt, last_prompt.txt (gitignored)
bin/ models/       binaries and weights (gitignored)
```

Read `ARCHITECTURE.md` before changing anything: it is the contract every file follows and the
reasoning behind the prompt format.
