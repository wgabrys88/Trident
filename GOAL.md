# GOAL

Trident is a voice assistant that stays in memory. The microphone stays open. Voice activity detection notices speech. The recognizer writes the words. The gate and the brain each read one whole prompt and write the generation unchanged. The mouth speaks that text on the real speakers. The person hears the assistant because the mouth spoke.

Files are the only meeting place for the five residents. One role, one executable, one settings file. Nothing starts those residents or carries messages between them. There is no resident supervisor, harness, message bus, or service manager.

`assistant.py` is one Python one-shot beside the others. It runs `hear.py`, then `qwen.py` or `gemma.py`, then `mouth.py`, and passes their text on the command line. It does not start the five residents, and it does not close the stay-loaded finish line.

## Programs

Five residents. Each takes one text file and no flags. Usage is `usage: program file.txt`. Run from the directory where the result should appear. Paths inside a settings file resolve from that file's directory.

| Role | Executable | Settings | Writes |
| --- | --- | --- | --- |
| Capture | `vad.exe` | `vad.txt` | `HH-MM-SS-mmm_vad_out_NNN.txt` names the wav; the wav sits beside it |
| Recognizer | `ear.exe` | `ear.txt` | `HH-MM-SS-mmm_ear_out_NNN.txt`, recognizer text unchanged |
| Gate | `sense.exe` | `sense.txt` | `HH-MM-SS-mmm_sense_out_NNN.txt`, generation unchanged |
| Brain | `gemma-brain.exe` | `gemma.txt` | `HH-MM-SS-mmm_gemma_out_NNN.txt`, generation unchanged |
| Mouth | `chatterbox.exe` | `chatterbox.txt` | `HH-MM-SS-mmm_chatterbox_out_NNN.txt` names the wav |

`vad.exe` captures WASAPI by friendly name (`PKEY_Device_FriendlyName`), shared mode. Silero ONNX, window 512. It resamples to `vad.rate` and exits after one finished utterance.

`ear.exe` reads `ear.input` (one wav) and launches `nemo-speech.exe transcribe`. Empty `ear.language` omits `--language`.

`sense.exe` reads `sense.text` as the whole prompt, including the Qwen3 turn markers the user wrote. CPU, `sense.gpu-layers` 0. The model is Qwen3-0.6B. It has no vision input.

`gemma-brain.exe` reads `gemma.text` in Gemma 4's own form. `gemma.image` is raw base64 or empty. When the image is set, the prompt already contains `<__media__>`.

`chatterbox.exe` speaks `chatterbox.text` as written. `chatterbox.variant` is `nano`, `turbo`, or `v3`. The GGUF architecture selects the engine. `chatterbox.play` is `on` or `off`. `on` plays the wav with `PlaySoundW` on the default speakers. `off` writes the wav and skips PlaySound.

`chatterbox-bake.exe` bakes one reference voice into the mouth models and exits. It is not a resident. `bake.txt` names `bake.t3`, `bake.s3`, and `bake.reference`. `bake.cond-seconds` is 15 for nano and turbo, 6 for v3. It rewrites those two model files in place and writes `HH-MM-SS-mmm_bake_out_NNN.txt` with both paths.

`install.py` builds the tree. The only install command is `python install.py install.txt`. It reads `install.txt` and nothing else for its parameters. It creates `.venv` when needed, clones the pinned ggml, llama.cpp, and NeMo-Speech trees under `.install`, configures CMake, builds, downloads the models named in `install.txt`, and bakes nano, turbo, and v3 with `chatterbox-bake.exe`. `install.publish` stays `off`. The installer does not start the residents.

`mouth.py`, `hear.py`, `gemma.py`, and `qwen.py` exit after one result. `assistant.py` repeats that chain until quit or Ctrl-C. Each turn starts those one-shots fresh. None of them stay loaded, and none of them start the five residents.

| Entry | Drives | Job |
| --- | --- | --- |
| `mouth.py` | `chatterbox.exe` | Speak one or more sentences. Playback stays in `mouth.py`. |
| `hear.py` | `nemo-speech.exe` | Record the PC mic, print one transcript. |
| `gemma.py` | `gemma-brain.exe` | Gemma question, optional image file. Prefer an Nvidia GPU. |
| `qwen.py` | `sense.exe` | Qwen3 text question. |
| `assistant.py` | `hear.py`, `qwen.py` or `gemma.py`, `mouth.py` | One local voice turn. Hear or `--text`, then the brain, then the mouth. |

## Lego

The repository root holds one filled template per program: `vad.txt`, `ear.txt`, `sense.txt`, `gemma.txt`, `chatterbox.txt`, and `bake.txt`.

That file is the only source of the program's parameters. Each value is used as written. The program does not replace a number, fill a value the file left empty, or keep a second default that wins over the file. A missing required key is an error. An empty value is empty. There is no shared settings file.

The result file is created in the current directory with `CREATE_NEW`. The name is local time `HH-MM-SS-mmm`, an underscore, the role, `_out_`, and a number that starts at `000`. The name carries clock time only. If that name exists, the number increases. An existing file is kept. The person connects one program's result to the next program's input by editing text files. The code does not watch a neighbor, and it keeps no pid file and no stop file.

Text is UTF-8. A text block is the model's own prompt or sentence, passed through unchanged. Letters outside ASCII, including Polish, pass through. The language value is a parameter of that model file. The mouth speaks the sentence as written.

A tool does not exist until the user names it. Until then a turn is text in and text out. When the user names one, that tool is text the model can write and work that one program performs. The files stay the only meeting place.

## Mouth

From the repository root:

```
.\.venv\Scripts\python.exe mouth.py [--model nano|turbo|v3] [--lang TAG] TEXT [TEXT ...]
```

One process takes every chunk. A separate process per chunk is only a reported fallback. `--model` defaults to `nano`. Omitted `--lang` is `en` for `nano` and `turbo`, and `pl` for `v3`. A passed `--lang` is written as given and shared by every chunk.

At least one TEXT is required. Empty text, an unknown model, an empty language, and any text line whose entire content is `<<` exit 2 before the first `chatterbox.exe`. Every chunk is validated first.

For each chunk the script re-reads `chatterbox.txt`, keeps the GGUF pair lines and the numeric knobs, and drops `chatterbox.variant`, `chatterbox.language`, `chatterbox.play`, and the existing `chatterbox.text` block. It writes UTF-8 with no BOM to `mouth.txt`. `chatterbox.txt` stays untouched. It appends the variant, the language, `chatterbox.play off`, and a `chatterbox.text` block holding that chunk, then runs `.\chatterbox.exe mouth.txt` once, current directory the repository root, no shell.

`mouth.py` plays each wav with `PlaySoundW` (`SND_FILENAME | SND_NODEFAULT`) on the default speakers and synthesizes the next chunk while the current wav plays. The next wav starts when the current play returns. A non-zero synthesize exit stops the loop. `mouth.txt` holds the last chunk. Synthesis stays inside `chatterbox.exe`.

Direct `chatterbox.exe` with `chatterbox.play on` plays the wav itself. A Mouth one-shot is heard on the real speakers. Chain playback uses those speakers. The cable input stays free of the mouth.

## Hear

From the repository root, with the repo virtualenv so `sounddevice` is present:

```
.\.venv\Scripts\python.exe hear.py SECONDS [--model PATH] [--device cpu] [--language TAG] [--mic NAME_OR_INDEX] [--format text] [--rate 16000] [--endpointing on|off] [--stop-history-eou-ms 1200] [--verbatim] [--no-punctuation] [--stream]
```

`SECONDS` must be greater than 0. Rate below 8000 is rejected. Defaults: model `ear.gguf` beside the script, device `cpu`, format `text`, rate `16000`, endpointing `on`, stop-history end-of-utterance `1200`. The recognizer command always includes `--quiet`. `--language` is added only when the tag is non-empty. `--verbatim`, `--no-punctuation`, and `--stream` are added only when those flags are set.

With no `--mic`, the Windows default input is used when it has input channels and the name does not contain `cable`; otherwise the first input whose name contains `microphone` or `mic` and does not contain `cable`. A numeric `--mic` is that device index. A name substring matches only devices whose names do not contain `cable`.

The script records for `SECONDS`, writes a temporary wav in a `hear_*` directory under the repo root, runs `nemo-speech.exe transcribe` once, prints that stdout, and exits with that process's code. Subprocess pipes and device-name prints use UTF-8 with `errors=replace`. The temporary directory is removed when the process exits. `hear.py` does not speak. Cable proofs stay on the virtual cable. This entry is the PC microphone.

## Brain one-shots

`gemma.py` drives `gemma-brain.exe`. Prefer an Nvidia GPU. A Vulkan build runs the same executable where CUDA is absent. The script does not load the model in Python. It copies knobs from `gemma.txt` into `gemma_run.txt` and leaves `gemma.txt` untouched. `gemma.text` is a Gemma 4 turn that opens the thought channel. `--image PATH` is a file on disk. The script stores raw base64 in `gemma.image` and puts `<__media__>` in the prompt when that marker is absent. The settings value is that raw base64. It runs `gemma-brain.exe gemma_run.txt` once and prints the generation to stdout, thinking included. The child's stdout and stderr are discarded, so stdout is only that generation file. `--verbose` passes the child's stderr through. An empty question exits 2.

```
.\.venv\Scripts\python.exe gemma.py [--image PATH] [--verbose] "Question."
```

`qwen.py` drives `sense.exe`. Text only. Qwen3-0.6B has no vision. The script copies knobs from `sense.txt` into `sense_run.txt`, wraps the question in Qwen3 turn markers, runs `sense.exe sense_run.txt` once, and prints the generation to stdout. The child's stdout and stderr are discarded, so stdout is only that generation file. `--verbose` passes the child's stderr through. `qwen.py --image` exits 2. An empty question exits 2. `sense.exe` has no image key.

```
.\.venv\Scripts\python.exe qwen.py [--verbose] "Question."
```

`sense.exe` must set `cpuparams_batch.n_threads` to `cpuparams.n_threads`; otherwise the batch path access-violates.

## Assistant

From the repository root, with the repo virtualenv:

```
.\.venv\Scripts\python.exe assistant.py [--once] [--seconds N] [--brain qwen|gemma] [--model nano|turbo|v3] [--lang TAG] [--image PATH]
.\.venv\Scripts\python.exe assistant.py --once --text "Say only: ready." --model nano
```

`assistant.py` runs those one-shots with `.venv\Scripts\python.exe`. It does not load a model. It does not start `vad.exe`, `ear.exe`, `sense.exe`, `gemma-brain.exe`, or `chatterbox.exe`. Those executables start only inside `hear.py`, `qwen.py`, `gemma.py`, and `mouth.py`, as they already do.

The default brain is `qwen.py`: Qwen3-0.6B, text only, the Iris path. `--brain gemma` is opt-in and runs `gemma.py`. Prefer an Nvidia GPU for Gemma. On Iris the Vulkan build can run it and can be slow. `--image PATH` requires `--brain gemma`. An image with the default brain exits 2. The script makes no network call. Grok Bot stays an optional remote path and is not on this inference path.

With no `--text`, each turn runs `hear.py` for `--seconds` (default 8), then the brain, then `mouth.py`. The loop repeats until the heard transcript is `quit`, `exit`, or `stop`, or until Ctrl-C. `--once` is a single heard turn. `--text` skips the microphone and runs one brain-then-mouth round. `--seconds` is unused on that round.

`--model` defaults to `nano`. Omitted `--lang` is `en` for nano and turbo, and `pl` for v3. A passed `--lang` is written through to `mouth.py` and shared by every chunk.

The brain child's stdout is printed unchanged. `mouth.py` receives the speakable span of that stdout. When `</think>` is present, the span is the text after the last one. Otherwise, when `<channel|>` is present, the span is the text after the last one. An unclosed `<think>` and no answer span is not spoken. With neither marker, the span is the generation with control tokens removed. `**`, `__`, and backticks are dropped so the mouth does not speak those marks. The words stay the model's.

That span is one `mouth.py` argument when it is at most 65 words, or 55 when the mouth language is `pl`. A longer span is split on a paragraph, on `.` `!` `?` `;`, on an em dash, or on a colon that is not inside a time. A conjunction is preferred once the piece is already at least 50 English words or 45 Polish words. A quotation is kept whole unless that quotation itself exceeds the cap. One `mouth.py` process takes every chunk.

A non-zero child exit is a non-zero assistant exit. The stage name and the child exit code go to stderr. The wrapper passes `--verbose` to the brain one-shot and prints that stderr only when the child exits non-zero. `mouth.txt`, `sense_run.txt`, `gemma_run.txt`, and `hear_*` stay untracked.

This one-shot does not supervise the five residents and does not keep them loaded.

## Chain

The five residents still connect by hand. `assistant.py` is not that path. Play known speech into the virtual-cable input. Point `vad.device` at the cable output. Put the wav name `vad.exe` wrote into `ear.input`. Put the recognizer text into the next prompt file as that model's own prompt, still unchanged. Put the sentence the brain wrote into `chatterbox.text`, or pass that sentence to `mouth.py`. Judge each resident by the file it wrote. Judge `hear.py`, `gemma.py`, `qwen.py`, and the brain stage of `assistant.py` by stdout. Judge the mouth stage by the speakers.

## Finish line

Stop when all of this is true on the computer where the work is running:

1. The five residents stay in memory. The microphone stays open. Voice activity detection runs by itself.
2. Each role is still one executable and one settings file. They meet only through those files. One-shots remain `mouth.py`, `hear.py`, `gemma.py`, `qwen.py`, and `assistant.py` as specified above. `assistant.py` only chains the other one-shots.
3. The source matches the templates. Duplicate and unused paths are gone. No code overrides a value the user wrote.
4. A virtual-audio-cable proof passes for each program and for the whole chain. Speech goes in on the cable, the text files carry the words, and one utterance comes out of the real speakers because the mouth spoke. During that proof the mouth stays off the cable input. `hear.py` is a separate PC-mic proof.
5. A new session with no prior chat can continue from `GOAL.md`, `AGENTS.md`, `RULES.md`, and `BOTS.md`. Use `CODE_REVIEW_CHECKLIST.md` when reviewing.

Each executable still performs one unit of work and exits. Closing that gap means the same executable stays loaded. It does not mean a second program that supervises the five. `assistant.py` chains one-shots and exits. The one-shots do not close that gap.
