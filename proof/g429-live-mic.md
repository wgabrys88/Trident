# G429 live mic cue

Date: 2026-09-29. Host: Iris (`EB-W`). Checkout: `C:\Users\eb-wjt\Downloads\Jarvis\Trident`. Branch: `cursor/live-mic-cue-7fcb`. Code: `f941645`.

One cued turn. Default WASAPI mic, resident VAD, `hear.py`, POST to the leave-healthy brain, mouth on the default speakers. No phrase gate. Nothing bound `:8765` on Iris.

```powershell
.\.venv\Scripts\python.exe .\start.py --live --cue --once
```

Exit 0. Elapsed 45.9 s.

## Beeps and the mic

Capture stayed closed until the two-beep cue finished. Times are local.

| Event | Time |
| --- | --- |
| Mic closed, before any beep | 20:38:24.612 |
| One beep | 20:38:25.494 |
| Wait 10 s | 20:38:25.494 → 20:38:35.508 |
| Two beeps finished | 20:38:37.521 |
| Mic opening | 20:38:37.521 |
| Mic open (`vad` ready) | 20:38:37.687 |
| Utterance saved, mic closed | 20:38:48.401 |
| Three beeps | 20:39:07.529, 20:39:08.647, 20:39:09.782 |

Mic: `Microphone Array (Intel® Smart Sound Technology for Digital Microphones)`.

## Heard

`proof/g429-live-mic-hear.json`. Wav `20-38-48-324_vad_out_000.wav` (16 kHz mono, 7.992 s). Copy: `proof/g429-live-mic-utterance.wav`. Hear tag: `pl-PL`.

> Mam najmniej Wojciech, co ostatniomi mówiłeś Termin Polish Langwich odłos The Last Reclass from Me.

## Brain and mouth

POST `http://192.168.16.31:8765/` id `1790707132499773400`. The listen loop also sent the last history pairs already in `assistant.history.txt`. The new line is the transcript above.

Reply, spoken on `mouth out: default` (`mouth: v3 pl`):

> Hello, Wojciech.

Stderr: `assistant: nvidia stream`, `assistant: mouth 1 chunk(s)`, then the three beeps. Saved copies: `proof/g429-live-mic-nvidia_turn.request.txt`, `proof/g429-live-mic-nvidia_turn.response.txt`. Cue log: `proof/g429-live-mic.log`.

## Port 8765

| Check | Before | After |
| --- | --- | --- |
| `192.168.16.31:8765` | accept | accept |
| `127.0.0.1:8765` | timeout | timeout |

Iris did not bind, stop, or restart that port.
