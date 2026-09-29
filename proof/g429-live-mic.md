# G429 live mic cue

Date: 2026-09-29. Host: Iris (`EB-W`). Checkout: `C:\Users\eb-wjt\Downloads\Jarvis\Trident`. Branch: `cursor/live-mic-cue-7fcb`. Code under test: `f941645`.

One cued turn. Default WASAPI mic, resident VAD, `hear.py`, POST to the leave-healthy brain, mouth on the default speakers. No phrase gate. Nothing bound `:8765` on Iris.

```powershell
.\.venv\Scripts\python.exe .\start.py --live --cue --once
```

Exit 0. Elapsed 48.8 s.

## Beeps and the mic

Capture stayed closed until the two-beep cue finished. Times are local.

| Event | Time |
| --- | --- |
| Mic closed, before any beep | 20:23:43.908 |
| One beep | 20:23:44.849 |
| Wait 10 s | 20:23:44.849 → 20:23:54.851 |
| Two beeps finished | 20:23:56.880 |
| Mic opening | 20:23:56.880 |
| Mic open (`vad` ready) | 20:23:57.050 |
| Utterance saved, mic closed | 20:24:09.211 |
| Three beeps | 20:24:29.815, 20:24:30.972, 20:24:32.078 |

Mic: `Microphone Array (Intel® Smart Sound Technology for Digital Microphones)`.

`vad mic:` is logged after the two-beep line. `vad.pid` is gone after the turn.

## Heard

`proof/g429-live-mic-hear.json`, wav `20-24-09-087_vad_out_000.wav` (16 kHz mono, 8.216 s). Copy: `proof/g429-live-mic-utterance.wav`.

> Hello, this is mi I mójch. How much is two plus two teraz mówię po polsku jest ósma dwadzieścia cztery zero pięć sekund

Hear tag: `en-US`.

## Brain and mouth

POST `http://192.168.16.31:8765/` id `1790706253423829700`. The listen loop also sent the last history pairs already in `assistant.history.txt`. The new line is the transcript above.

Reply, spoken on `mouth out: default`:

> Two plus two equals four.

Stderr: `assistant: nvidia stream`, `assistant: mouth 2 chunk(s)`, then the three beeps. Saved copies: `proof/g429-live-mic-nvidia_turn.request.txt`, `proof/g429-live-mic-nvidia_turn.response.txt`. Raw cue log: `proof/g429-live-mic.log`.

## Port 8765

| Check | Before | After |
| --- | --- | --- |
| `192.168.16.31:8765` | accept | accept |
| `127.0.0.1:8765` | timeout | timeout |

Iris did not bind, stop, or restart that port.
