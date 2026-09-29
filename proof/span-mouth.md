# Language spans on the mouth (G429)

Date: 2026-09-29. Branch: `cursor/lang-span-mouth-2f02` off `cursor/iris-voice-loop-5fab`. Code commit `35ee885`. Playback on that commit. Default device: Speakers (Realtek(R) Audio).

One mouth process. English spans use nano. Any other language uses Chatterbox v3 for that span only. v3 is not loaded for an English span. `--model turbo` is the only switch that makes English use turbo.

The word packer cannot do this. It has one budget for the whole atom. Sentence-level `lid.176.ftz` labels the owner line Polish at 0.65, so one label is the wrong mouth. The span cut on the same model is:

| Span | Model |
| --- | --- |
| hi my name is Wojciech and I want to | nano en |
| opowiedzieć wam po polsku | v3 pl |
| the story | nano en |

`lid.176.ftz` is 938013 bytes. On this CPU the load was 0.013 s and 25 labels took 0.4 ms. linguonnx (2026) runs the same fastText family on ONNX. Its default file is glotlid-int8 at about 425 MB, and the lid.176 ONNX exports sit at the 33 MB end of that range. Those files were not downloaded. They are the same classifier in a larger pack, so the mouth stayed on the compressed model.

A span whose label is at least 0.95 stays whole. An English span at 0.80 or above stays whole, so `The door is shut.` is not peeled into Dutch. `The lamp is on. The door is shut.` is two nano atoms on the stream and one packed nano chunk when the reply is already finished. A one-word side needs a solo score of 0.90, so `My name is Wojciech.` stays English. Short function words can stay with the previous language: `Bonjour, I am` is one French span, then the rest is English.

## Injected playback

No POST. No bind on `:8765`. TCP to `http://192.168.16.31:8765/` was open before and after. `mouth.pid` was absent before the run.

English only, cold start. Plan: one nano span. Pid 332, `nano` / `en`. Elapsed 3.30 s. v3 was not started.

```text
The lamp is on.
```

Then the mixed line on the same process. Pid 332 was adopted for the English prefix, pid 10848 was v3 `pl`, pid 3884 was nano `en` again. Mixed elapsed 16.47 s. After the last span, `mouth.txt` was `chatterbox.variant nano`, `chatterbox.language en`, `chatterbox.cfm-steps 2`.

| Wav | Clock | Audio |
| --- | --- | --- |
| `15-43-17-342_chatterbox_out_000.wav` | 15:43:17 | 24 kHz mono, 1.00 s, nano, `The lamp is on.` |
| `15-43-21-174_chatterbox_out_000.wav` | 15:43:21 | 24 kHz mono, 3.12 s, nano, the English prefix |
| `15-43-31-017_chatterbox_out_000.wav` | 15:43:31 | 24 kHz mono, 2.28 s, v3 pl |
| `15-43-33-572_chatterbox_out_000.wav` | 15:43:33 | 24 kHz mono, 1.12 s, nano, `the story` |

Play of a clip starts after that clip's wav exists. The next model load starts while that clip is in PlaySound. The Polish wav was written 9.8 s after the long English wav; that English clip is 3.12 s, so the v3 load ran past the end of playback. The following nano wav was written 2.6 s after the Polish wav, and the Polish clip is 2.28 s.

`mouth.py --stop` then removed pid 3884. It did not open `:8765`. The brain port was still open. Local `:8765` had no listener.

## Prefetch on the speakers

Date: 2026-09-29. Branch: `cursor/prefetch-v3-span-e7af`. Playback commit `65cfd44`. Same default device: Speakers (Realtek(R) Audio). No POST. No bind on `:8765`. TCP to `http://192.168.16.31:8765/` was open before and after. A connect to `127.0.0.1:8765` timed out both times. `mouth.pid` was absent before the run.

`speak_pieces` starts a v3 one-shot when a non-English span is already known. The fast resident stays. The one-shot exits when its wav is written.

Silent timing on this PC, PlaySound not called. A cold v3 resident was ready in 0.83 s. Speaking `opowiedzieć wam po polsku` then took 8.60 s, and the next speak on that same resident took 8.60 s again. The model load is the short part. The utterance is the long part.

English only, cold start. Plan: one nano span. No prefetch line. Pid 7896, `nano` / `en`. Elapsed 3.43 s.

```text
The lamp is on.
```

Then the mixed line, same Python process. Prefetch printed at 16:18:56.637, before the English prefix wav. Pid 7896 was adopted and was still the resident after the last span. During that English clip, tasklist showed pid 7896 and pid 13364 (`chatterbox.exe`). 13364 was the v3 one-shot. It was gone before the Polish clip played. After the line, `mouth.txt` was still `chatterbox.variant nano`, `chatterbox.language en`, `chatterbox.cfm-steps 2`. Mixed elapsed 15.23 s.

| Wav | Clock | Audio | Play |
| --- | --- | --- | --- |
| `16-18-55-153_chatterbox_out_000.wav` | 16:18:55 | 24 kHz mono, 1.00 s, nano, `The lamp is on.` | 16:18:55.190–16:18:56.621 |
| `16-18-59-649_chatterbox_out_000.wav` | 16:18:59 | 24 kHz mono, 3.12 s, nano, the English prefix | 16:18:59.680–16:19:03.270 |
| `16-19-07-120_chatterbox_out_000.wav` | 16:19:07 | 24 kHz mono, 2.28 s, v3 pl | 16:19:07.613–16:19:10.307 |
| `16-19-08-973_chatterbox_out_000.wav` | 16:19:08 | 24 kHz mono, 1.12 s, nano, `the story` | 16:19:10.307–16:19:11.860 |

The Polish play started 4.343 s after the English prefix ended. The Polish wav is 7.47 s after the English prefix wav. On the run above, that gap was 9.8 s between those wavs. The following nano clip started as the Polish play ended. Nano was synthesized during the Polish clip, so that handoff had no extra wait.

`mouth.py --stop` then removed pid 7896. It did not open `:8765`. The brain port was still open. `mouth.pid` was absent. Local `:8765` had no listener.

The remaining silence is the v3 utterance. Ready is 0.83 s. The speak is 8.60 s. The English prefix covers about 3 s of synthesis plus 3.51 s of playback, and the one-shot beside nano took 10.5 s from the prefetch line to the Polish wav. That leaves the 4.343 s before Polish.
