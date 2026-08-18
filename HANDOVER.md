# Handover — PT in / EN out next

Use this after session compact. Do not re-install unless the controller is down.

## Where we are

| Item | Value |
|---|---|
| Repo | `https://github.com/wgabrys88/Trident.git` |
| Branch | `runner-x` = `origin/runner-x` = `origin/main` = `c135655` |
| Tag | `MILESTONE-2` on `c135655` (previous: `MILESTONE-1` on `2d2807d`) |
| Last commit | `Fix TTS install patches and make the panel follow the live catalog` |
| Controller | `python main.py` already running, `127.0.0.1:8765`, do **not** start a second one |
| Panel | `http://127.0.0.1:8765/` |

Live this session (`run-1eb76d26bd084991a5afa9de722416ef`): ASR / Brain / TTS all `running`. Config is still Portuguese-only: `conversation.language=pt`, `speech.language=pt`.

## What this session already finished

1. Panel REST only for install/load. No extra install harnesses in the tree.
2. Fixed `patches/chatterbox-cmake.patch` (was corrupt at line 298). It now applies with `git apply --unidiff-zero` and keeps VoiceEncoder + `gguf_split_mtl.cpp`.
3. `main.py`: `command_env()` → `build_env()`; checkpoint download uses `t3_mtl23ls_v3.safetensors` + `s3gen_v3.pt`; S3Gen GGUF pin is `81f8f1a6164b97f71691f4954773dbf5af64f39efd008c7c24967259e1cbf445` (1056431360 bytes).
4. Panel no longer reads missing `schema.models["chatterbox-s3t"].label` (that crash left SSE on Connecting). System board is schema-driven.
5. Listen-back: synthesize, wait `chunk_done`, write `data/last-output.wav`, POST `/api?op=asr`.
   - Counting clip `Um, dois, tres.` → Parakeet `2, 3...` (useless).
   - Three PT sentences → first two came back in Portuguese (`céu`→`seu`); third sentence dropped. T3 165 tokens / 6.72 s.

`ARCHITECTURE.md` is stale in places (still mentions `chatterbox-s3t`, old BricksDisplay pins, monolith `chatterbox.patch`). Trust `main.py` + this file.

## Product shape (do not reinvent)

```
mic/WAV (Portuguese) → Parakeet :8097 → Gemma brain :8098 → Chatterbox V3 WS :8095 → speakers
Controller :8765  POST /api  GET /api?op=state|log|events
TTS audio is WebSocket only. C-CTRL never opens the socket. Native writes data/last-output.wav on last chunk.
```

Control: `POST http://127.0.0.1:8765/api` JSON `{op,...}`. Jobs 202. Job collision 409 on the same `kind:name`. ASR/turn/upload take raw `audio/wav`.

Do not add a product WS client. For listen-back, open `ws://127.0.0.1:8095/tts` only as a check: `tts_session` → init → ready → `tts_event ready` → `tts_request` → synthesize → `chunk_done` → POST that WAV to `asr`.

## Next task — Portuguese input, English output

**Goal:** User speaks Portuguese. Brain reply is English. Chatterbox speaks that English. Input ASR stays Portuguese/multilingual. Chatterbox V3 MTL already tags language on the T3 prompt; it is not PT-only at the model layer.

**What currently forces Portuguese on the way out**

| Lock | File | Effect |
|---|---|---|
| `TTS_LANGUAGES = {"pt": "Portuguese"}` | `main.py:75` | `tts_session` / `voice_options` reject `en` |
| `CONVERSATION_LANGUAGES` = TTS ∩ ASR | `main.py:77` | Conversation select is only `pt` |
| `brain()` system prompt | `main.py:1101-1104` | `Reply in {language_name} ({language}).` uses the **turn** language, today `pt` |
| `run_turn` | `main.py:1308, 1342` | One `language` for ASR gate + brain |
| `panel.js` turn speak | `panel.js:742, 759` | Speaks `result.text` with `conversation-language` (pt) |
| `speech.language` | config / Speech Lab | Separate lab path; also only `pt` in schema |
| load_config coerce | `main.py:267-270` | Unknown codes get rewritten to `pt` |

ASR already lists `en` and `pt` in `ASR_LANGUAGES`. Parakeet is multilingual; it does not need `conversation.language=en`.

**Intended split (implement this, do not couple all three to one dropdown):**

- **Input / ASR:** Portuguese (keep `conversation.language=pt` or an explicit input language).
- **Brain:** English system prompt (`Reply in English (en). …`).
- **TTS session init:** `language=en` so T3 gets the English tag. Text is the English brain reply.

Minimum surface:

1. Add `"en": "English"` to `TTS_LANGUAGES`.
2. Keep conversation/input as `pt` (still in ASR).
3. Stop using the input language as the brain reply language. Either a new config field (`conversation.reply_language` / `speech.language` for output) or a fixed EN reply for this product mode.
4. `panel.js` `runTurn` must `speak(result.text, "en", "natural", "turn", …)` (or the new output field), not the PT conversation select.
5. Speech Lab can stay a typed-text lab; if it should also speak English, point `speech.language` default at `en`.
6. `load_config` must accept `en` and not coerce it back to `pt`.

Do **not** rebuild Chatterbox unless a native language tag is actually wrong. First change is controller + panel catalog. Reload is not required for config `set`; TTS **session** must be reopened after language change (`closeTts` already happens when sample/stream/style change — language change on speak already opens a new session if language differs).

## How to prove it

1. `GET /api?op=state` — engines running; after change, schema must list `en` under `languages.speech` (and whatever output field you add).
2. Speech Lab or API: `tts_session` lane `a`, `language=en`, `style=natural`, speak two short English sentences. Wait `chunk_done`.
3. POST `data/last-output.wav` to `/api?op=asr`. Transcript should be English, not Portuguese.
4. Conversation: Portuguese WAV or mic → Parakeet PT transcript → brain English text in `flow.answer` → TTS `en` → Parakeet on `last-output.wav` is English.
5. Do not use `Um, dois, tres.` as the proof sentence.

## Known TTS limits (not this next ticket unless they block EN)

- Third sentence of a 3-sentence PT prompt was dropped (165 T3 tokens / 6.72 s). Prefer 1–2 short spoken sentences (brain prompt already asks for that).
- Default reference is Iracema Portuguese demo (`data/default-reference.wav`). English T3 tag + that speaker is expected to still speak English (MTL language tag). If listen-back is PT-colored words, check the init `language` field first, then the reference.

## Files you will touch

- `main.py` — `TTS_LANGUAGES`, `CONVERSATION_LANGUAGES` (do not accidentally require TTS=ASR), `brain()`, `run_turn`, `FIELDS`, maybe a reply-language field.
- `panel.html` / `panel.js` — conversation vs speech language labels; turn speak language.
- `data/config.json` — will migrate on next `set` / load.

Do not commit `tools/`, `models/`, `data/last-output.wav`. Do not push unless asked.
