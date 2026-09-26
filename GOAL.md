# GOAL

Trident is a voice assistant that stays in memory.

The microphone stays open. Voice activity detection notices speech by itself. The recognizer writes the words. The gate and the brain are each one model: the text they read is the whole prompt, and the text they write is the generation, unchanged. The mouth speaks that text on the real speakers. The person hears the assistant because the mouth spoke.

## Programs

Five residents. Each role is one executable. When they run together they meet only through files.

| Role | Executable | Job |
| --- | --- | --- |
| Capture | `vad.exe` | WASAPI capture by friendly name. Silero notices the utterance. Writes the wav and a text file that names that wav. |
| Recognizer | `ear.exe` | Transcribes that wav. Writes the recognizer's text, unchanged. |
| Gate | `sense.exe` | Its own model. The text in its file is the whole prompt. Writes the generation, unchanged. |
| Brain | `gemma-brain.exe` | The text in its file is the whole prompt, in the model's own form. Writes the generation, unchanged. |
| Mouth | `chatterbox.exe` | Speaks the text in its settings file as written. When `chatterbox.play` is `on`, plays the wav on the default speakers. |

`chatterbox-bake.exe` bakes one reference voice into the mouth's model files and exits. It is not a resident.

`mouth.py` is the speak one-shot. It does not synthesize. Each chunk is a cold `chatterbox.exe` run with `chatterbox.play off`. `mouth.py` plays the wavs on the default speakers and synthesizes the next chunk while the current wav plays.

`hear.py` is the hearing one-shot. It records the PC microphone, runs `nemo-speech.exe transcribe` once, and prints the transcript. It is not a resident. Cable proofs stay on the virtual cable. `hear.py` is the laptop-mic entry.

`install.py` builds the tree. The converters it uses are part of building. Nothing starts the five residents or carries their messages. There is no orchestrator, supervisor, harness, message bus, or service manager.

## How they meet

The Lego piece is one settings file handed to one executable. `vad.exe`, `ear.exe`, `sense.exe`, `gemma-brain.exe`, and `chatterbox.exe` each take that text file and no flags. Usage is `usage: program file.txt`.

The repository root holds one filled template for each program: `vad.txt`, `ear.txt`, `sense.txt`, `gemma.txt`, `chatterbox.txt`, and `bake.txt`. The installer reads `install.txt` and nothing else for its own parameters.

That file is the only source of the program's parameters. The program uses each value as written. It does not replace a number, fill a value the file left empty, or keep a second default that wins over the file. A missing required key is an error. An empty value is empty. There is no shared settings file. Paths in a file are names in that file's directory.

The result is a text file in the current directory. Its name is the local time as `HH-MM-SS-mmm`, an underscore, the role, `_out_`, and a number that starts at `000`. The year, the month, and the day are not in the name. The file is created with `CREATE_NEW`. If that name exists, the number increases. An existing file is never replaced. Those files are the history of the run. The person connects one program's result to the next program's input by editing text files. The code does not watch a neighbor, keep a pid file, or keep a stop file.

Text is UTF-8. A text block is the model's own prompt or sentence, passed through unchanged. Letters outside ASCII, including Polish, pass through. The mouth does not rewrite a sentence into English. The language value is a parameter of that model file.

When the brain's file includes an image, that image is the base64 the user wrote, and the prompt already contains the marker that model needs. When it does not, the turn is text only. The code does not add a token the user did not write, does not build a tool schema, and does not search the disk for a picture.

## Mouth one-shot

From the repository root, one process, every chunk a positional argument:

```
.\.venv\Scripts\python.exe mouth.py [--model nano|turbo|v3] [--lang TAG] [--diag-log DIR_OR_PATH] TEXT [TEXT ...]
```

The seated Mouth role uses that one process. A separate process per chunk is a reported fallback, not a silent one.

`--model` is `nano`, `turbo`, or `v3`. The default is `nano`. When `--lang` is omitted, `nano` and `turbo` use `en`, and `v3` uses `pl`. A passed `--lang` is written as given. The same model and language apply to every chunk.

At least one TEXT argument is required. Empty text, an unknown model, an empty language, and any text line whose entire content is `<<` exit 2 before the first `chatterbox.exe`. Every chunk is validated first.

For each chunk the script reads `chatterbox.txt`, copies every line (the six GGUF pair lines and the numeric knobs included), and drops `chatterbox.variant`, `chatterbox.language`, `chatterbox.play`, and the existing `chatterbox.text` block. It writes UTF-8 with no BOM to `mouth.txt`, overwriting `mouth.txt` only. `chatterbox.txt` stays untouched. It appends the variant, the language, `chatterbox.play off`, and a `chatterbox.text` block holding that chunk. It then runs `.\chatterbox.exe mouth.txt` once, current directory the repository root, no shell. That run synthesizes and writes the wav. `chatterbox.play off` skips PlaySound inside `chatterbox.exe`.

`mouth.py` plays each wav with `PlaySoundW` on the default waveform device (`SND_FILENAME | SND_NODEFAULT`). It synthesizes the next chunk while the current wav plays, about one chunk ahead. The next wav starts when the current play returns. A non-zero synthesize exit stops the loop. `mouth.txt` holds the last chunk. There is no second synthesizer in Python or in C++. Optional `--diag-log DIR_OR_PATH` writes phase timestamps and a 0.5s Iris Xe GPU Engine (3D/Compute) utilization log under that directory; omit it for a quiet SPEAK run.

`chatterbox.play` is required in the settings file and is `on` or `off`. Direct `chatterbox.exe` with `on` plays the wav itself. A Mouth one-shot is heard on the real speakers. Those speakers are not the virtual-cable input.

## Hear one-shot

From the repository root, with the repo virtualenv so `sounddevice` is present:

```
.\.venv\Scripts\python.exe hear.py SECONDS [--model PATH] [--device cpu] [--language TAG] [--mic NAME_OR_INDEX]
```

Further flags match the ear/NeMo surface: `--format`, `--rate`, `--endpointing on|off`, `--stop-history-eou-ms`, `--verbatim`, `--no-punctuation`, `--stream`. The default model is `ear.gguf` beside the script. The default device argument is `cpu`. Omitted or blank `--language` omits `--language` on the recognizer command. `SECONDS` is required and must be greater than 0.

The microphone is the normal PC input. With no `--mic`, the Windows default input is used when its name does not contain `cable`; otherwise the first non-cable input whose name contains `microphone` or `mic`. A numeric `--mic` is that device index. A name substring skips any device whose name contains `cable`. On this worker the usual mic is the Intel Smart Sound array.

The script records for `SECONDS`, writes a temporary wav under the repo root, runs `nemo-speech.exe transcribe` once, prints that process's stdout, and exits with that process's code. Subprocess pipes and device-name prints use UTF-8 with `errors=replace`. It does not speak.

## Tools

A tool does not exist until the user names it. Do not invent tools. Do not build a tool framework, a registry, or a parser that chooses a call. Until the user names one, a turn is text in and text out. When the user names one, that tool is text the model can write and work that one program performs. The files stay the only meeting place.

## Finish line

Stop when all of this is true on the computer where the work is running:

1. The five residents stay in memory. The microphone stays open. Voice activity detection runs by itself.
2. Each role is still one executable. Nothing starts the five or carries their messages. They still meet only through the text files above. A Mouth one-shot is `mouth.py` driving `chatterbox.exe` once per chunk with `chatterbox.play off`, playing wavs on the speakers with one-chunk overlap. A hearing one-shot is `hear.py` recording the PC mic and printing one `nemo-speech.exe` transcript.
3. The source matches the templates. Duplicate and unused paths are gone. No code overrides a value the user wrote.
4. A virtual-audio-cable proof passes for each program and for the whole chain. Speech goes in on the cable, the text files carry the words, and one utterance comes out of the real speakers because the mouth spoke. During that proof the mouth is not playing into the cable.
5. A new session with no prior chat can continue from `GOAL.md`, `AGENTS.md`, `RULES.md`, and `BOTS.md`. Use `CODE_REVIEW_CHECKLIST.md` when reviewing.

Each executable still performs one unit of work and exits. Closing that gap means the same executable stays loaded. It does not mean adding a second program to supervise the five. `mouth.py` and `hear.py` do not close that gap.
