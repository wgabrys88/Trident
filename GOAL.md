# GOAL

Trident is a voice assistant that stays in memory.

The microphone stays open. Voice activity detection notices speech by itself. The recognizer writes the words. The gate and the brain are each one model: the text they read is the whole prompt, and the text they write is the generation, unchanged. The mouth speaks that text on the real speakers. The person hears the assistant because the mouth spoke.

## Programs

Five residents. Each role is one executable. When they run together they meet only through files.

| Role | Executable | Job |
| --- | --- | --- |
| Capture | `vad.exe` | WASAPI capture by friendly name. Silero notices the utterance. Writes the wav and a text file that names that wav. |
| Recognizer | `ear.exe` | Transcribes that wav. Writes the recognizer's text, unchanged. Does not decide what the words mean. |
| Gate | `sense.exe` | Its own model. The text in its file is the whole prompt. Writes the generation, unchanged. |
| Brain | `gemma-brain.exe` | The text in its file is the whole prompt, in the model's own form, which the user wrote. Writes the generation, unchanged. |
| Mouth | `chatterbox.exe` | Speaks the text in its settings file as written and plays the wav on the default speakers. |

`chatterbox-bake.exe` bakes one reference voice into the mouth's model files and exits. It is not a resident.

`mouth.py` is the speak entry for a Mouth one-shot. It writes one settings file and runs `chatterbox.exe` once. The executable speaks. The script does not synthesize, and it does not start capture, the recognizer, the gate, or the brain.

The residents have no orchestrator, no supervisor, no harness, no message bus, and no service manager. Nothing starts the five or carries their messages. `install.py` builds the tree. The converters it uses are part of building. `mouth.py` is only the one-shot speak entry.

## How they meet

The Lego piece is a settings file handed to an executable. `chatterbox.exe` takes one argument, that text file, and no flags. The same shape holds for `vad.exe`, `ear.exe`, `sense.exe`, and `gemma-brain.exe`.

The repository root holds one filled template for each program: `vad.txt`, `ear.txt`, `sense.txt`, `gemma.txt`, `chatterbox.txt`, and `bake.txt`. The installer reads `install.txt` and nothing else for its own parameters.

That file is the only source of the program's parameters. The program uses each value as written. It does not replace a number, fill in a value the file left empty, or keep a second default that wins over the file. A missing required key is an error. An empty value is empty. There is no shared settings file. Paths in a file are names in that file's directory.

The result is a text file in the current directory. Its name is the local time as `HH-MM-SS-mmm`, an underscore, the role, `_out_`, and a number that starts at `000`. The year, the month, and the day are not in the name. If that name already exists, the number increases. An existing file is never replaced. Those files are the history of the run, and they are the only way one program's result can become another program's input. The person makes that connection by what the text files say. The code does not watch a neighbor, keep a pid file, or keep a stop file.

Text is UTF-8. A text block in the file is the model's own prompt or sentence, passed through unchanged. Letters outside ASCII, including Polish, pass through. The mouth does not rewrite a sentence into English, and it does not refuse a language the model file already contains. The language value is a parameter of that model file, not a translation step.

When the brain's file includes an image, that image is the base64 the user wrote, and the prompt already contains the marker that model needs. When it does not, the turn is text only. The code does not add a token the user did not write, does not build a tool schema, and does not search the disk for a picture.

## Mouth one-shot

From the repository root:

```
python mouth.py [--model nano|turbo|v3] [--lang TAG] TEXT
```

`--model` is `nano`, `turbo`, or `v3`. The default is `nano`. `--lang` is optional. When `--lang` is omitted, `nano` and `turbo` use `en`, and `v3` uses `pl`.

`mouth.py` requires one text argument. It rejects empty text, an unknown `--model`, an empty `--lang`, and any text line whose entire content is `<<`.

It reads `chatterbox.txt` beside itself. It copies every line, including the six GGUF pair lines and the numeric knobs. It drops only `chatterbox.variant`, `chatterbox.language`, and the existing `chatterbox.text` block. It writes UTF-8 with no BOM to `mouth.txt`, overwriting `mouth.txt` only. It never overwrites `chatterbox.txt`. It appends `chatterbox.variant` and `chatterbox.language` from the flags, then:

```
chatterbox.text <<
TEXT
<<
```

It runs `.\chatterbox.exe mouth.txt` once, with the current directory set to the repository root, and with no shell. It exits with that process's exit code. It does not run the executable again.

This speak entry did not change any C++ file. The mouth that speaks is still `chatterbox.exe`.

## Tools

A tool does not exist until the user names it. Do not invent tools. Do not build a tool framework, a registry, or a parser that chooses a call. Until the user names one, a turn is text in and text out. When the user names one, that tool is text the model can write and work that one program performs. The files stay the only meeting place.

## Finish line

Stop when all of this is true on the computer where the work is running:

1. The five residents stay in memory. The microphone stays open. Voice activity detection runs by itself.
2. Each role is still one executable. Nothing starts the five or carries their messages. They still meet only through the text files above. A Mouth one-shot is `mouth.py` driving `chatterbox.exe` once.
3. The source matches the templates. Duplicate and unused paths are gone. No code overrides a value the user wrote.
4. A virtual-audio-cable proof passes for each program and for the whole chain. Speech goes in on the cable, the text files carry the words, and one utterance comes out of the real speakers because the mouth spoke. During that proof the mouth is not playing into the cable, so capture does not hear the mouth.
5. A new session with no prior chat can continue from `GOAL.md`, `AGENTS.md`, and `RULES.md`.

On the tip that introduced one text file per program, each executable still performs one unit of work and exits. Closing that gap means the same executable stays loaded. It does not mean adding a second program to supervise the five. `mouth.py` does not close that gap.
