# Build & inference profile (Gemma 4 E2B, CUDA 12.6, GTX 1060 6GB)

## Stack

| Piece | Version / source |
|--------|------------------|
| **llama.cpp** | `gemma/llama.cpp` — track `master` ([ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp)) |
| **CUDA** | **12.6.x** for Pascal `sm_61` (GTX 1060). CUDA **12.7+** drops Pascal. |
| **Model** | [ggml-org/gemma-4-E2B-it-GGUF](https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF) |
| **Multimodal** | Same repo: `mmproj-gemma-4-E2B-it-*.gguf` (vision + audio projector) |

Gemma 4 **E2B** is multimodal: **text, image, audio** (and video via ffmpeg in upstream; we disable `MTMD_VIDEO` by default).

Update llama.cpp:

```powershell
.\scripts\update_llama.ps1
```

## CMake cache (set in `cmake/Pascal1060.cmake`)

These are the knobs that matter for **your GPU**, not the long tail of unused backends.

### CUDA / ggml (performance)

| Option | Our value | Meaning |
|--------|-----------|---------|
| `CMAKE_CUDA_ARCHITECTURES` | **`61-real`** | SASS for GP106 only (see `cmake/PascalCuda.cmake`). |
| `GGML_CUDA` | ON | CUDA backend only. |
| `GGML_CUDA_FORCE_MMQ` | **ON** | On Pascal, **MMQ** quant matmul is usually faster than default cuBLAS path. |
| `GGML_CUDA_FA` | ON | **Flash Attention** CUDA kernels (tile path on Pascal, not Hopper “mma FA”). |
| `GGML_CUDA_FA_ALL_QUANTS` | OFF | Faster builds; default quant combos suffice for Q4_K_M. |
| `GGML_CUDA_GRAPHS` | ON | CUDA graphs in decode (good for steady token generation). |
| `GGML_CUDA_NCCL` | OFF | Single GPU — no collective comms. |
| `GGML_CPU` | **OFF** | No CPU tensor backend (GPU-only product). |

If FlashAttention misbehaves on Pascal at runtime, rebuild with `-DGGML_CUDA_FA=OFF` (rare; tile FA is the Pascal path).

**nvcc flags (kernels):** set only in upstream `ggml-cuda` (`-use_fast_math`, `-extended-lambda`, `-O3` via MSVC). Do not add a second `--use_fast_math` on the `ggml-cuda` target — it duplicates ggml and can worsen diagnostics.

**nvcc warning `#221-D` (`1e+300` in `common.cuh`):** older llama.cpp used huge float literals as sentinels; current `master` uses `-INFINITY`. If you still see `#221-D`, run `.\scripts\build.ps1` (clean `ggml-cuda` first) or delete `gemma/build` and reconfigure — stale `.obj` files keep the old source.

**Host CPU (decode / threads):** `.\scripts\detect_cpu.ps1` then build; runtime uses `GEMMA_HOST_THREADS` physical cores and MSVC `/arch:AVX2` on this i7-4790S when detected.

### llama.cpp (multimodal)

| Option | Our value | Meaning |
|--------|-----------|---------|
| `LLAMA_BUILD_COMMON` | ON | Chat templates, arg parsing, shared CLI helpers. |
| `LLAMA_BUILD_MTMD` | ON | **`libmtmd`** linked into **`gemma-brain`** (text + image + audio). |
| `LLAMA_BUILD_TOOLS` | OFF | No upstream CLI tools; only `gemma-brain.exe`. |
| `MTMD_VIDEO` | OFF | No ffmpeg/video unless you enable subprocess + ffmpeg. |
| `LLAMA_BUILD_SERVER` | OFF | No HTTP server. |
| `BUILD_SHARED_LIBS` | OFF | Static link — single portable `bin/` folder. |

Upstream reference: [docs/multimodal.md](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md).

## VRAM budget (6 GB)

Rough planning:

| Asset | Size (typical) |
|-------|----------------|
| `gemma-4-e2b-it-Q4_K_M.gguf` | ~3.1 GB |
| `mmproj-gemma-4-E2B-it-bf16.gguf` | ~1.0 GB |
| KV cache (`-c 4096`) | ~0.5–1.5+ GB |

**6 GB is tight for full multimodal + long context.** Practical defaults:

- Text-only: `gemma-brain` with **Q4_K_M**, `-c 4096`, `-ngl 999`.
- Multimodal: **Q4_K_M** + **mmproj** (bf16 or Q8 mmproj if you use it), **`-c 2048`**, `-ngl 999`, small images / short audio.

## Build

```powershell
cd gemma
cmake -S . -B build -G "Visual Studio 17 2022" -A x64 `
  -DCMAKE_CUDA_COMPILER="C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6\bin\nvcc.exe" `
  -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release --target gemma-brain -j
```

Output:

- `build\bin/Release\gemma-brain.exe` — multimodal text + image + audio (CUDA only).

## Models

```powershell
.\scripts\fetch_model.ps1          # text GGUF Q4_K_M
.\scripts\fetch_multimodal.ps1     # adds mmproj
```

## Runtime (inference) flags

### `gemma-brain` (required: model + mmproj)

```powershell
.\build\bin\Release\gemma-brain.exe `
  -m models\gemma-4-e2b-it-q4_k_m.gguf `
  --mmproj models\mmproj-gemma-4-E2B-it-bf16.gguf `
  -c 2048
```

| Flag | Recommended (1060) | Notes |
|------|-------------------|--------|
| `-m` / `--mmproj` | Q4_K_M + bf16 mmproj | Both required; no text-only path. |
| `-c` | `2048` | Raise only if VRAM allows. |
| `--image` / `--audio` | paths | CLI or REPL `/image` `/audio`. |
| `-p` | prompt | Single turn with attached media. |

Build-time: full GPU offload (`n_gpu_layers=999`), Flash Attention **enabled** (`GGML_CUDA_FA` + runtime `LLAMA_FLASH_ATTN_TYPE_ENABLED`), `fit_params=false` (no silent context shrink).

Audio: 16 kHz mono WAV. Images: PNG/JPEG via mtmd (CPU decode only for file load; inference on GPU).

### “Flux” vs Flash Attention

In this stack, **Flash Attention** is **`GGML_CUDA_FA`** at **build** time and llama’s **flash-attn** context options at **runtime** in full `llama-cli` — not a separate “Flux” product. On **Pascal**, ggml uses the **tile** FA path, not Blackwell/Ampere MMA FA.

## Compatibility checklist

- [ ] CUDA **12.6** installed, VS **CUDA build tools** integration selected in installer.
- [ ] `nvcc --version` shows 12.6.x.
- [ ] `llama.cpp` at recent `master` (`.\scripts\update_llama.ps1`).
- [ ] GPU driver new enough for CUDA 12.6 runtime.
- [ ] Gemma 4 weights from **ggml-org** (not legacy Gemma 2/3 only builds).
