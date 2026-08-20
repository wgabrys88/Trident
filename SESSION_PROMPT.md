You're joining Trident, a Windows listen-think-speak box. Keep it small. Less code is cheaper and easier for any human or AI. Do not add layers.

Every session starts the same. Read the full git history, including long commit messages. Then read every file the repository actually tracks. That snapshot is the truth. This text names no files and no line numbers so it still works later. Do not assume. If you did not just see it, you do not know it. Review first. Change only what you verified. Talk like a person. Act only at full confidence.

Listen and think are shared. Speak is not. Chatterbox is several families on purpose. They differ in more than knobs: languages, flags, checkpoints, tokenizers, stop rules, execution. Each family is its own pipeline with its own config, program, models, and install. Delete one family and the others must still work. Never fold them into one binary, one config blob, or special-case branches. Shared voice work is only packing sentences, joining quiet edges, and writing twenty-four kilohertz mono sound. Build only the family in use.

The transcriber wants sixteen kilohertz mono PCM sixteen. The speaker accepts a normal WAV of any rate or channel count, at least five seconds. Output is twenty-four kilohertz mono. Celebrity reference voices are mandatory on install so a human can hear if the clone is honest. Pin them. Do not skip them. Do not store them in git.

Bare invocation must print one-line command help. Each listen, think, or speak run writes a new timestamped folder and never overwrites an older wav or transcript. After converting models, delete the conversion cache. No test suite: prove the box by running it with logs on. No browser panel, websocket server, fake caches, extra knob tables, environment variables, or narrating comments.

Appendix, later, not now: the thinking model may become an agent that drives the machine through the shell, including fetching reference wavs. Do not start that until asked.
