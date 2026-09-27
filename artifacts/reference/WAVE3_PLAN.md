# Wave 3 plan

Frozen experiment for a qwen chunker. Residents and the one-shots stay as they are. This cook adds new scripts and these artifacts only.

`mouth.py` plays positional TEXT arguments in order. It does not split. `--lang` is out of scope except as the shared tag on an optional play, documented below. One language is shared by every chunk.

The new splitter owns breath and a language switch. A foreign word that the LID marks is its own chunk. `qwen.py` stays the brain one-shot and is not on this path.

Untouched: `mouth.py`, `qwen.py`, `gemma.py`, `hear.py`, `assistant.py`, `gemma/src/sense.cpp`, and the tip `sense.txt`. No C++ edit. No pull request.

`sense.exe` reads only the keys it already reads. The run copy keeps `sense.gpu-layers 0`, `sense.ctx 4096`, `sense.threads 4`, `sense.batch 512`, and `sense.model` from the tip. It does not add grammar, stop, or seed. Temperature 0 is never written.

v3 already prefixes one `[lang]` per synthesize. This cook does not put a second `[xx]` in the text.

## Files

| Path | Role |
| --- | --- |
| `wave3_harness.py` | Copies tip knobs into untracked `wave3_sense.txt`, writes the arm prompt, runs `sense.exe`, scores, writes `wave3_run.json`. |
| `wave3_split.py` | B1 LID and switch, then `chunks_for_mouth`. B0 calls that function on the whole text. Writes `wave3_chunks.json`. |
| `artifacts/reference/wave3_fixtures.json` | S1–S4 bodies and S5 gold F1–F8. |
| `artifacts/reference/wave3_lexicon.json` | Closed PL and EN lists. |
| `artifacts/reference/WAVE3_PLAN.md` | This plan. |

Whitelist:

```
!/wave3_harness.py
!/wave3_split.py
!/artifacts/reference/wave3_fixtures.json
!/artifacts/reference/wave3_lexicon.json
!/artifacts/reference/WAVE3_PLAN.md
```

Deny, beside the other run artifacts:

```
/wave3_sense.txt
/wave3_run.json
/wave3_chunks.json
/wave3_mouth_argv.json
```

The harness does not use `sense_run.txt`.

## Matrix

Arm A matches `qwen_prompt`: user body, then `<|im_end|>`, then `<|im_start|>assistant`. No think closer.

Arm B is the same body with a final line `/no_think` immediately before `<|im_end|>`. The closer exists only in this harness.

| Cell | temp | top-p | top-k | n-predict | gpu-layers | ctx | Run |
| --- | --- | --- | --- | --- | --- | --- | --- |
| H0 | 0.7 | 0.8 | 20 | 128 | 0 | 4096 | S1–S4 default |
| H1 | 0.7 | 0.8 | 20 | 256 | 0 | 4096 | One retry, rule 3 below |
| Hneg | 0 | 0.8 | 20 | 128 | 0 | 4096 | Do not launch |

Prompt bodies are in `wave3_fixtures.json`. S1 document `en`. S2 document `pl`. S3 document `en`, clause switch. S4 document `en`, one foreign word `żółty`. S5 is parse-only. No `sense.exe`.

| Order | Cell | Cold start |
| --- | --- | --- |
| 0 | S5 B0 and S5 B1 | No |
| 1–8 | S1–S4 × A/B × H0 | Yes |
| 9 | One truncated S1–S4 cell × H1 | Only under rule 3 |

## LID

Bare word matches `assistant._bare`: `casefold`, strip `.,;:!?—–\"“”«»()[]`.

1. A letter in `ąćęłńóśźż` marks `pl`.
2. Else the PL list marks `pl`. Else the EN list marks `en`.
3. Any other word stays unmarked and inherits the document language.
4. One unmarked word whose two neighbors are already marked with the same language inherits that language. The bridge does not cross a marked word and does not span two unmarked words.

PL: `oraz`, `ale`, `lub`, `albo`, `więc`, `wiec`, `jednak`, `się`, `sie`, `że`, `ze`, `jest`, `są`, `sa`, `potem`, `jedziemy`, `nie`, `tak`, `na`.

EN: `the`, `and`, `but`, `we`, `then`, `hello`, `lamp`, `desk`, `keyboard`, `meeting`, `start`, `stop`, `now`, `yellow`.

Left off both lists: `i`, `a`, `to`, `do`, `me`, `bo`.

## B1 and B0

B1 splits into words, marks language, applies the bridge, then inherits. Contiguous words of one language are one span. Each span calls `chunks_for_mouth(span, span_lang)`. English cap 65, floor 50. Polish cap 55, floor 45. Under the cap that function returns one chunk.

Kind, in order:

- One-word span whose language differs from the document language: `foreign-word`.
- A cap cut: `breath` when the next chunk starts with a conjunction, else `cap`.
- A one-chunk span whose language differs from the document language and from the previous chunk: `switch`.
- Otherwise `whole`.

A span that returns to the document language and fits in one chunk is `whole`. F5’s third chunk is that case. F6’s third chunk uses the same rule. F6’s middle chunk is `switch`.

B0 is `chunks_for_mouth(text, document_lang)` on the whole text. Under the cap it is one chunk, including when a foreign word is present.

Joining chunks with one space equals `" ".join(source.split())`.

## S5 gold

| Id | Document | Expectation |
| --- | --- | --- |
| F1 | en | One chunk. `12:30` stays intact. Kind `whole`. |
| F2 | en | One chunk. The quotation stays in that chunk. |
| F3 | en | 70 words, `and` at index 60. Chunk 1 is indices 0–59. Chunk 2 starts with `and`. Kinds `breath`, `whole`. |
| F4 | pl | 60 words, `ale` at index 50. Chunk 2 starts with `ale`. Both `pl`. Kinds `breath`, `whole`. |
| F5 | en | `The lamp is` / `żółty` / `on the desk.` Kinds `whole`, `foreign-word`, `whole`. B0 is one chunk. |
| F6 | en | `We start now.` / `Żółta łódź płynie.` / `Then we stop.` Middle kind `switch`. B0 is one chunk. |
| F7 | pl | `Potem jest` / `the` / `biurko.` Middle kind `foreign-word`, lang `en`. `biurko` inherits `pl`. |
| F8 | en | `Żółta do łodzi` is one `pl` chunk. `do` bridges the two marked words. |

Sidecar shape:

```json
{"source":"...","document_lang":"en","splitter":"B1","chunks":[{"i":0,"text":"...","lang":"en","kind":"whole"}]}
```

## Scores

Pass: one JSON object, string field `answer`, non-empty, no chat markers, no line that is only `<<`. The speakable span is `assistant.speakable`.

S3 also needs at least two PL-marked words. S4 needs `żółty` once and no other Polish diacritic. S2 needs one Polish diacritic and no EN-list word. `F-LANG-MISS` scores the model and skips B1.

| Code | When |
| --- | --- |
| `F-THINK-OPEN` | `<think>` present, no `</think>` |
| `F-THINK-EMPTY` | Span empty after the closer |
| `F-JSON` | Span is not one object with a string `answer` |
| `F-TRUNC` | No closing `}` after a closed or absent think |
| `F-MARKERS` | Markers or a `<<` line inside `answer` |
| `F-EMPTY` | Blank answer |
| `F-LANG-MISS` | S2, S3, or S4 lacks the required foreign span |
| `F-B1-LOSS` | Join mismatch |
| `F-B1-CAP` | Chunk over its language cap |
| `F-B1-FOREIGN` | Required foreign word is not its own chunk |
| `F-B0-DRIFT` | B0 differs from `chunks_for_mouth` |
| `F-MOUTH-PRE` | Empty chunk, `<<` line, or a `[xx]` token |
| `F-TEMP0` | Temp 0 requested |
| `F-KEY` | Run file contains grammar, stop, or seed |
| `F-LID-OTHER` | A letter outside the Polish set and outside ASCII; inherit the document language and keep going |

Before any play: chunks non-empty, no `<<` line, no `[xx]` token, `chatterbox.txt` and `sense.txt` unchanged. Play is `mouth.py` on `Speakers (Realtek(R) Audio)`.

## Wave 5 order

Working directory is the repo root. PowerShell uses `;`. Interpreter is `.\.venv\Scripts\python.exe`.

```
.\.venv\Scripts\python.exe wave3_split.py --gold
.\.venv\Scripts\python.exe wave3_harness.py
.\.venv\Scripts\python.exe wave3_harness.py --play-only
```

1. S5. Stop if B1 misses gold or B0 drifts. No cold start.
2. Cold starts 1–8, H0 only. Append each cell to `wave3_run.json` before the next launch.
3. A 9th launch only when exactly one S1–S4 cell is `F-TRUNC`, that cell’s twin arm is not a hard think or JSON failure, and launches so far are 8. Rerun that cell at H1. Two truncations end the sweep.
4. Score. Run B0 and B1 on each passing answer.
5. Optional one `mouth.py --model v3` from `--play-only`, on validated B1 chunks from one isolated S3 or S4 cell. `--lang` is `pl` for S2 and `en` for S1, S3, and S4. Confirm `[en]` and `[pl]` in `v3-t3.gguf` key `chatterbox.tokenizer.language_tokens` before play.

Also compare B0 and B1 on `artifacts/reference/tts_chunk_mixed_pl_en.txt` for document `en` and document `pl`. No `sense.exe`. That compare is not the adopt gate. A B1 join miss on it is a splitter bug.

Stop: temp 0, a forbidden key, or a dirty `sense.txt` / `chatterbox.txt` before `sense.exe`. Nine launches is the ceiling. Two non-zero `sense.exe` exits in a row stop the sweep. A failed prove runs once more. Do not drive this with `assistant.py`, `qwen.py`, `gemma.py`, or `hear.py`.

## Optional play

`mouth.py --lang` is not edited. The tag is the scenario document language and is shared. Foreign chunks stay in the argument list. Their pronunciation under that one tag is deferred.

If the default playback device cannot be confirmed as `Speakers (Realtek(R) Audio)`, the prove is `MANUAL` and `mouth.py` stays unstarted.

## Decision

`adopt-optional-qwen_chunk` when S5 passes, at least one of S3 or S4 has the foreign span and B1 isolates it while B0 stays one chunk, and the winning arm has a non-empty JSON answer on at least 3 of S1–S4. The winner is the arm with fewer `F-THINK-OPEN` and `F-JSON` codes. A tie keeps arm A. Mixed arm scores, or a 9th H1 start, stay in research. Adoption means a later cook may run `wave3_split.py` on qwen stdout and pass those chunks to `mouth.py`. It does not edit `mouth.py`, `qwen.py`, or `assistant.py`.

`keep-research` when S5 passes and the model misses the foreign span, the arm scores are mixed, the 9th start was spent, or an unmarked foreign word is the only LID miss.

`abandon` when B1 breaks the lossless join, breaks F1–F4, or fails to isolate F5–F7. Also when the only way forward is an edit to a frozen file, a grammar, stop, or seed key, or temperature 0.

If `mouth.py` rejects a chunk, do not adopt. If play fails twice on `PlaySoundW` only, the text decision stands.
