# Gemma 4 E2B — bare-metal CUDA brain (GTX 1060)

Single binary **`gemma-brain`**: **text, image, and audio** via llama.cpp + mtmd. **CUDA-only** ggml build (no CPU tensor backend). **`-m` and `--mmproj` are required.**

Build and tuning: [BUILD.md](BUILD.md).

## Hardware

| Spec | Value |
|------|--------|
| GPU | GTX 1060 6GB, **sm_61** |
| CUDA toolkit | **≤ 12.6** (Pascal dropped in 12.7+) |

## Models

```powershell
.\scripts\fetch_multimodal.ps1
```

## Build

```powershell
cd gemma
cmake -S . -B build -G "Visual Studio 17 2022" -A x64 `
  -DCMAKE_CUDA_COMPILER="C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6\bin\nvcc.exe" `
  -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release --target gemma-brain -j
```

## Run

```powershell
.\build\bin\Release\gemma-brain.exe `
  -m models\gemma-4-e2b-it-q4_k_m.gguf `
  --mmproj models\mmproj-gemma-4-E2B-it-bf16.gguf `
  --image photo.png -p "What is in this image?"
```

Interactive: run without `-p`, then `/image path`, `/audio path`, type a message, `/quit`.

```powershell
.\scripts\update_llama.ps1
```
