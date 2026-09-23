# Trident

Three processes and one folder. The folder is the conversation. There is no separate API. Whoever can create a file in `workspace/` is speaking to the machine, and the part that watches that name answers.

```mermaid
flowchart LR
  speaker["Person, agent, or program"]
  inbox["inbox/*.txt"]
  ear["Ear · asr.py"]
  heard["transcription-N.txt"]
  brain["Brain · brain.py"]
  speech["speech-N.txt"]
  mouth["Mouth · tts.py"]
  wav["done/wav/"]
  speaker --> inbox --> ear --> heard --> brain --> speech --> mouth --> wav
```

The ear also opens the microphone. A microphone utterance and an inbox file become the same kind of transcription file. After that, the path is one path.

## Files

| File | Who writes it | Who reads it |
| --- | --- | --- |
| `inbox/*.txt` | anyone, renamed into place | the ear |
| `transcription-N.txt` | the ear | the brain |
| `live.txt` | the brain | the brain, this process only |
| `memory.md` | the brain, and it survives a restart | the brain |
| `speech-N.txt` | the brain | the mouth |
| `said.txt` | the brain, last spoken answer | the brain, as an echo filter |
| `job-N.py` | the brain | the script |
| `job-N.result.txt` | the brain, from the script streams | the brain, appended to `live.txt` |
| `decision-N.txt` | the brain, the tool-call text, before apply | the brain |
| `ready/ear` | the ear, when it prints ready | trident.py |
| `ready/brain` | the brain, when it prints ready | trident.py |
| `ready/mouth` | the mouth, when it prints ready | trident.py |
| `done/inbox/` | the ear, after it has read the file | anyone reading the record |
| `done/transcription/` | the brain, after it has read the file | anyone reading the record |
| `done/speech/` | the mouth, after it has spoken the file | anyone reading the record |
| `done/job/` | the brain, after the result is in `live.txt` | anyone reading the record |
| `done/decision/` | the brain, after apply returns | anyone reading the record |
| `done/live/` | the brain, when `live.txt` is replaced or its head is retired | anyone reading the record |
| `done/memory/` | the brain, when distill replaces memory | anyone reading the record |
| `done/said/` | the brain, when say replaces `said.txt` | anyone reading the record |
| `done/ready/` | trident.py, at the next start | anyone reading the record |
| `wav/` | the mouth, while it writes and plays | the mouth |
| `done/wav/` | the mouth, after the wav exists | anyone listening to the record |

A consumed file is renamed into `done/<kind>/`. The name and the bytes stay. A wav moves to `done/wav/` and is never removed.

```mermaid
flowchart TB
  subgraph tools [Brain tools]
    pass["pass · writes nothing"]
    say["say · writes speech files, then empties live.txt"]
    note["note · appends memory.md, leaves live.txt"]
    distill["distill · replaces memory.md, empties live.txt"]
    runpy["run_python · writes job-N.py and job-N.result.txt, looks again"]
    quit["quit · ends the brain"]
  end
  say --> mouth2["Mouth reads one speech file, writes a wav, plays, moves the wav, then moves the speech file"]
```

`say` longer than 60 words is split at sentence ends. The first line of a speech file is the language, `en` or `pl`. The rest is the words. Nano and turbo reject a language other than `en`. A `live.txt` longer than 12000 characters has its head renamed into `done/live/` before the brain reads it.

## Run

From the repository root, with the venv Python:

```text
python install.py
python trident.py nano
```

`python install.py` installs the ear, the brain, and mouth nano. `python install.py turbo` and `python install.py v3` install those mouths. `python install.py all` installs the ear, the brain, mouth turbo, and mouth v3.

`python trident.py <variant>` opens the microphone and still reads `inbox/` files. `python trident.py <variant> inbox` leaves the microphone closed. The variant is `nano`, `turbo`, or `v3`.

trident.py moves a stale ready file into `done/ready/`, waits until `ready/ear`, `ready/brain`, and `ready/mouth` exist, and then prints `jarvis ready`. An inbox file is renamed into place, or a person speaks and stays quiet long enough for the ear to cut. The proof of a run is the files left under `workspace/done/`, including `done/wav/`.

The brain offloads every layer (`n_gpu_layers` is -1). Both mouths use repeat penalty 1.2. The llama mouth, v3, uses 10 diffusion steps and `gpu` 0. `python brain.py <variant> --say <text>` writes speech files and returns. The mouth is the only reader of those files.

## Layout

```mermaid
flowchart TB
  trident["trident.py · starts the three, loads no model"]
  trident --> brainPy["brain.py"]
  trident --> ttsPy["tts.py · nano, turbo, or v3"]
  trident --> asrPy["asr.py"]
  ttsPy --> server["chatterbox-server.exe"]
  install["install.py · venv, weights, server"]
  install --> trident
```

`install.py` is what a fresh clone runs before the three processes. The tracked tree is the program. The venv, the weights, and `chatterbox-server.exe` are produced by that install.
