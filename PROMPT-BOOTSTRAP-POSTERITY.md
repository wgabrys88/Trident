phase-D table-generated help

Implement Trident in C:\Users\eb-wjt\Downloads\3-way\Trident on real Windows. Continue immediately through remaining phases without permission requests. Python entry: python tts.py nano|turbo|v3 [flags] TEXT [language]. Download, convert, bake, reuse or start one detached named-pipe Vulkan server, synthesize one WAV, print its path.

Completed:
- [x] A: models/server.pid replaces variant pid files. PipeServer migrates every legacy models/*.pid, reuses identical command+voice only when pipe responds within 1000 ms, otherwise taskkills, waits, unlinks, starts detached with DEVNULL and bounded startup. Build and bake kill server.pid. No TerminateProcess, siblings, or exit cleanup. Initial leaked processes stopped on this machine. The standing-order file was initially absent.

- [x] B: Single settings flag table with name/default/help/group/architecture generates argparse and exact server argv. Zero C++ flag defaults. gpu=0 passes to VulkanBackend(device). CMakeLists.txt hash replaces copied build flags. Llama context uses tensor layout plus n-predict, GPT-2 wpe length. Metadata owns shapes/tokenizer/voice, never sampling; speech vocabulary comes from checkpoint/tokenizer.

- [x] C: Removable scripts/quant.py Policy(default,rules), TYPES from GGMLQuantizationType; first matching prefix/suffix/contains/ndim rule, integers preserved, f32/f16/bf16 or gguf quantize, errors propagate. Shipped quant_t3.json and quant_s3.json preserve f32 nonmatrices and sensitive prefixes; CLI overrides default and policy path replaces rules. Split converter from quant_policy.py; delete old policy JSON and force_f32. Contracts contain resolved policy and filenames include default and canonical rules sha8. GgufFile::floats requires F32; native Vulkan weights and F16 convolution expansion remain.

- [x] D: Complete three-group table-generated help: Model, GGUF conversion, Server. Explain rebuild/rebake/restart, all quant types, JSON mixing, integer preservation, Q4_K_M not a type; server changes restart, matching GGUF reused.

Remaining:
- [ ] E: Shared repeat_penalty.h and Audio::fade, retaining Llama 960 tail. Remove duplicate glue and narrating comments. First-party lines down excluding help.
- [ ] F: Build locally; exercise nano, turbo, v3, same-contract reuse, changed flags, q8_0 and layer-zero JSON policy. Confirm one live server and no competing CLI metadata. Rewrite this file to maintenance only with empty work checklist only after all conditions hold.

Standing constraints:
No tests, pytest, harnesses, CI, log files, debug prints, golden WAVs, scratch scripts, README or extra markdown. No pip installs or requirements changes. Do not edit ggml/ or sibling chatterbox.cpp. No CPU backend, watermark, Perth, n-gpu-layers, n-threads, silent fallbacks, swallowed errors, or quant retries. Keep GPT-2 and Llama engines, bakers, S3, T3 and binaries separate. nano/turbo meanflow bake is 15 s; v3 CFG bake is 6 s. Output is 24 kHz, not a CLI or competing GGUF setting. Torch conversion stays CPU; retain tokenizer language dependencies. CMake owns Vulkan ON and CPU/CUDA/OPENMP OFF. Model facts must come from tensor/tokenizer metadata, not drifting C++ literals. Host fills tokenizer paths privately.

Commit ritual after EVERY phase: read this entire file; replace it completely with same mission, updated completed/remaining work and discovered facts, first line phase subject, blank line, full standing order. git add implementation and this file, then git commit -F PROMPT-BOOTSTRAP-POSTERITY.md. Start next phase immediately. Final maintenance order preserves SST, exclusive server, removable quant, no fallbacks and this ritual.
