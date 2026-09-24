# GTX 1060 (GP106, compute 6.1) — CUDA codegen and nvcc policy for gemma-brain.
# ggml-cuda sets kernel flags (-use_fast_math, -extended-lambda, FA quants, MMQ, graphs)
# via llama.cpp/ggml/src/ggml-cuda/CMakeLists.txt; do not duplicate those on the target.

# Native SASS only for sm_61 (no forward-compatible PTX fatbin).
set(CMAKE_CUDA_ARCHITECTURES 61-real CACHE STRING "GP106 native SASS")

# Release: let nvcc optimize; host-side code in .cu translation units uses the MSVC toolset
# from the VS CUDA integration (configured via configure.ps1: cuda=12.6, CUDA_PATH v12.6).
set(CMAKE_CUDA_FLAGS_RELEASE "-DNDEBUG" CACHE STRING "CUDA Release flags")

# Do not add --use_fast_math here — ggml-cuda already enables it for all kernels.
