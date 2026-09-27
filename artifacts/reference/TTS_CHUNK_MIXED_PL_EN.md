# Mixed Polish-English TTS chunk reference

Speakable prose for later chunk experiments. The text file is the thing to read aloud. This note is the attribution and the map of what that prose is built to exercise.

- Text: `artifacts/reference/tts_chunk_mixed_pl_en.txt`
- Source: original
- License: MIT, the repository `LICENSE` (Copyright (c) 2026 Gianfranco Cordella). This prose was written for this repository and is covered by that license.
- Written: 2026-09-27

## Why this file is original

The fallback for this reference is original prose when no suitable licensed mixed Polish-English source exists. A search on 2026-09-27 did not find a public TTS, speech, or multilingual text that is Polish and English inside one passage, under a clear CC, Apache, or MIT license, and long enough for the breath caps below.

What that search did find:

- [MiniMaxAI/TTS-Multilingual-Test-Set](https://huggingface.co/datasets/MiniMaxAI/TTS-Multilingual-Test-Set) keeps each language in its own file. `text/polish.txt` is Polish. The dataset card fetched that day has no license field.
- [CML-TTS](https://github.com/freds0/CML-TTS-Dataset/) (CC-BY 4.0; paper [arXiv:2306.10097](https://arxiv.org/html/2306.10097)) is audiobook speech in Dutch, French, German, Italian, Portuguese, Polish, and Spanish, drawn from LibriVox and Project Gutenberg. English in the related training mix is LibriTTS. Each segment is one language.
- [PELCRA Polish-English parallel corpora (CC-BY)](http://lrt.ilsp.gr:8080/repository/browse/pelcra-polish-english-parallel-corpora-cc-by/e00eedee63f111e2bff4525400d761479d1a4537cfde45da8763b33a4fad20db/) are sentence-aligned parallel texts.
- [MultiBridge/LnNor](https://huggingface.co/datasets/MultiBridge/LnNor) is CC-BY 4.0 speech in Norwegian, English, and Polish. Each clip is labeled one of those three languages. Sampled viewer rows leave the text field empty.
- [CodeMixBench](https://huggingface.co/datasets/Tanushreeeeee/CodeMixBench) covers other language pairs. Polish is absent from its language list.
- [1uckyan/code-switch_chunks](https://huggingface.co/datasets/1uckyan/code-switch_chunks) is Chinese-English. Part of it follows CC-BY-NC-SA 4.0 from BAAI/CS-Dialogue.
- [AxonData/polish-contact-center-speech-dataset](https://huggingface.co/datasets/AxonData/polish-contact-center-speech-dataset) describes Polish calls with Polish and English transcripts. The card points the full set at a commercial purchase.
- Chatterbox Multilingual demo lines that turned up (model weights MIT) are monolingual Polish or monolingual English.

## How the prose is built

Breath length follows the mouth caps already stated in `GOAL.md`: 65 English words, 55 Polish words. A conjunction is relevant once a piece is already at least 50 English words or 45 Polish words. A number, a two-word name, and a quotation are intact targets: a later splitter should keep each one whole.

Word numbers below are whitespace tokens inside that one breath span. A comma stays on its token. The long spans were checked as single spans: the only colon in them sits inside `09:15`, and the period inside each quotation stays inside the quotation.

### Language switches

Mid-sentence, comma only, so the switch sits inside one sentence:

- `Powiedziałem check the cable zanim zamknąłem plik.` Polish, then the English phrase `check the cable`, then Polish.
- `I said sprawdź kabel before I closed the file.` English, then the Polish phrase `sprawdź kabel`, then English.
- `I checked the friendly name on the cable, a potem zapisałem ją bez zmian.` English clause, then a Polish clause.
- `Najpierw ucho oddało tekst, and the mouth did not start.` Polish clause, then an English clause.
- `The gate stayed quiet, ale plik i tak powstał.` English clause, then a Polish clause.

On a breath mark, so the switch sits on a semicolon, an em dash, or a colon:

- `Sprawdziłem nazwę; the device was still the cable output.`
- `Zostaw kabel — playback uses the real speakers.` The dash is U+2014.
- `Jedna uwaga: the default speakers stay real.`

A blank line also separates the long English paragraph from the long Polish paragraph.

### One foreign word

- English word in a Polish sentence: `benchmark` in `Zostawiam tu jedno angielskie słowo benchmark i wracam do polskiego zdania.`
- Polish word in an English sentence: `oddech` in `This English sentence holds one Polish word oddech and then stays in English.`

`oddechu` in the long Polish paragraph is ordinary Polish (genitive of the same noun). It is native there. The single-word insert is the bare `oddech` in the English sentence.

`Maja Nowak` is the two-word name. It is an intact-name target in both long paragraphs. It is not a foreign-word insert.

### Long English span (78 words)

One sentence, past the 65-word cap. No `and` / `but` / `or` / `so` / `because` / `however` in words 51–65, so a cap split lands on the word boundary after word 65.

| Target | Tokens | Words |
| --- | --- | --- |
| Two-word name, wholly before the cap | `Maja` `Nowak` | 30–31 |
| Time, colon between digits | `09:15,` | 35 |
| Quotation, straight `"` quotes, 8 words | `"Leave` `the` `count` `fully` `safe` `1` `847` `whole."` | 60–67 |
| Spaced number on the cap, inside that quotation | `1` then `847` | 65–66 |

The quotation itself is 8 words, under the 65-word cap, so a rule that keeps a quotation whole can move it as one piece. A cut after word 65 falls between `1` and `847`, inside the quotation. The closing period of `whole.` is inside the quotes. The sentence period is the one after `sentence`.

### Long Polish span (68 words)

One sentence, past the 55-word cap. `oraz` is word 30, before the 45-word floor, so it does not pull the cut forward. Words 46–55 contain no `i` / `oraz` / `ale` / `lub` / `albo` / `więc` / `bo` / `jednak`, so a cap split lands after word 55.

| Target | Tokens | Words |
| --- | --- | --- |
| Time, colon between digits | `09:15,` | 29 |
| Conjunction before the floor | `oraz` | 30 |
| Spaced number, wholly before the cap | `2` `400,` | 34–35 |
| Two-word name on the cap | `Maja` then `Nowak,` | 55–56 |
| Quotation, curly `“ ”` quotes, 4 words, wholly after the cap | `“Zostaw` `liczbę` `w` `całości.”` | 61–64 |

The Polish quotation is under the 55-word cap and does not contain the cap boundary. The name does: a cut after word 55 falls between `Maja` and `Nowak`.
