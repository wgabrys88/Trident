# Trident Best-Evidence Final Configuration

This repository is a consolidated, model-specific configuration built from the experimental record around 2026-09-13. It is designed to cooperate with the sibling `chatterbox.cpp` checkout pinned to:

`aaafdb6e1d83ecb8fd963ac2e76bc5e5718074e5`

Required layout:

```text
<PARENT>\Trident
<PARENT>\chatterbox.cpp
```

Copy the original voice prompt to:

```text
<PARENT>\Trident\reference.wav
```

The reference file is part of the effective model state. Do not substitute a generated output WAV for it.

## Family policy

### Nano

- HF revision: `71ccd1d0081b430592cea481f4307e764e07bc64`
- T3: F16 conversion
- Critical T3 interfaces retained F32 by engine commit `aaafdb6`:
  - `text_emb.weight`
  - `speech_emb.weight`
  - `speech_head.weight`
- S3Gen: Q4_0
- Known-good single-pass region includes the attached historical 1-20 result.
- Automatic count chunking only begins above 20.

### Turbo

- HF revision: `749d1c1a46eb10492095d68fbcf55691ccf137cd`
- T3: F16 conversion
- Same F32 interface tensors as Nano
- S3Gen: Q4_0
- F16 was decisively better than the earlier Q8_0 Turbo state.
- Conservative automatic count chunking is used because the C++ path still had a residual parity gap versus official Python on the n=20 ladder.

### V3

- HF revision: `5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18`
- T3: Q8_0 conversion
- S3Gen: V3 Q4_0
- Keeps the V3-specific model, tokenizer, language argument, sampler defaults, CFG/min-p controls, and acoustic path.
- Q8_0 is selected because the strongest recorded V3 state produced a clean 1-30 result; a later V3 F16 experiment did not reproduce that roof. This is a model-specific evidence choice, not a claim that Q8_0 is universally superior to F16.

## Important release improvements

This repository fixes several reproducibility weaknesses in the historical launcher:

1. The sibling engine is accepted only when its exact commit SHA matches. Detached HEAD is valid; branch names are not used as identity.
2. The CMake build is fingerprinted against the engine SHA, ggml SHA, family, and build flags. A stale build directory is discarded when the contract changes.
3. Converted GGUF files are fingerprinted against the exact HF asset hashes, converter script hash, conversion flags, engine SHA, and family.
4. T3 and S3 are treated as one conversion pair. If either conversion contract changes, both are regenerated from source checkpoints before baking.
5. Voice baking is fingerprinted against the reference WAV hash, conversion identities, and bake executable hash.
6. If the reference or bake contract changes, pristine GGUFs are regenerated before rebaking. The launcher does not depend on repeated in-place mutation semantics.
7. Every generated WAV receives a `.provenance.json` sidecar containing the complete effective state: model hashes, reference hash, engine/ggml pins, converter policy, build hashes, sampler overrides, text, chunking, and output hash.
8. One family server runs at a time.
9. Empty sampler overrides preserve the family header defaults rather than silently applying a shared cross-family tuning profile.

## Usage

Nano:

```powershell
python tts_nano.py "Hello from Nano."
```

Turbo:

```powershell
python tts_turbo.py "Hello from Turbo."
```

V3:

```powershell
python tts_v3.py "Hello from V3." en
```

For a deliberate single-pass experiment, bypass automatic chunking:

```powershell
python tts_nano.py --no-chunk "One, two, three, four, five, six, seven, eight, nine, ten, eleven, twelve, thirteen, fourteen, fifteen, sixteen, seventeen, eighteen, nineteen, twenty."
```

## Toolchain

- Windows
- Visual Studio 17 2022 x64
- CMake: `C:/Program Files/CMake/bin/cmake.exe`
- Vulkan SDK: `C:/VulkanSDK/1.4.357.0`
- Python 3.11+
- Git
- ggml is pinned to `7840aaba1989c6deeefede1d77d5aaf8f52b947e`

## Confidence statement

This is the strongest coherent all-family configuration supported by the supplied experiment record. It intentionally does not claim that the unresolved Turbo C++/official-Python parity gap has been proven away, nor that one precision policy is universally optimal for every prompt and seed. The repository is designed to preserve the best-supported family-specific choices and make every future WAV exactly traceable.
