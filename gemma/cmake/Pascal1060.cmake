# NVIDIA GTX 1060 6GB (GP106, compute capability 6.1, sm_61)
# CUDA toolkit: use 12.6 or earlier (Pascal dropped in CUDA 12.7+).

include(${CMAKE_CURRENT_LIST_DIR}/PascalCuda.cmake)

# --- CUDA backend (ggml-cuda) ---
set(GGML_CUDA ON CACHE BOOL "" FORCE)
set(GGML_CUDA_FORCE_MMQ ON CACHE BOOL "Pascal: prefer MMQ kernels over cuBLAS for quant matmul" FORCE)
set(GGML_CUDA_FORCE_CUBLAS OFF CACHE BOOL "" FORCE)
set(GGML_CUDA_FA ON CACHE BOOL "FlashAttention CUDA kernels (tile path on Pascal)" FORCE)
set(GGML_CUDA_FA_ALL_QUANTS OFF CACHE BOOL "Keep compile time/size reasonable" FORCE)
set(GGML_CUDA_GRAPHS ON CACHE BOOL "CUDA graphs in llama.cpp decode path" FORCE)
set(GGML_CUDA_NCCL OFF CACHE BOOL "Single GPU — no NCCL" FORCE)
set(GGML_CUDA_NO_PEER_COPY OFF CACHE BOOL "" FORCE)
set(GGML_CUDA_NO_VMM OFF CACHE BOOL "" FORCE)

# --- Inference on CUDA only; CPU backend still required for llama buft/host metadata ---
set(GGML_CPU ON CACHE BOOL "Required by llama.cpp loader (weights still -ngl 999 on GPU)" FORCE)
set(GGML_VULKAN OFF CACHE BOOL "" FORCE)
set(GGML_METAL OFF CACHE BOOL "" FORCE)
set(GGML_OPENMP ON CACHE BOOL "Host-side parallel (mtmd decode)" FORCE)
set(GGML_BLAS OFF CACHE BOOL "" FORCE)
set(GGML_ACCELERATE OFF CACHE BOOL "" FORCE)
set(GGML_NATIVE ON CACHE BOOL "Host ISA for CPU backend stubs" FORCE)

# --- llama.cpp: Gemma 4 multimodal (text + image + audio on E2B) ---
set(LLAMA_BUILD_COMMON ON CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TOOLS OFF CACHE BOOL "gemma-brain links mtmd directly" FORCE)
set(LLAMA_BUILD_MTMD ON CACHE BOOL "libmtmd for gemma-brain" FORCE)
set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_SERVER OFF CACHE BOOL "" FORCE)
set(LLAMA_CURL OFF CACHE BOOL "" FORCE)
set(LLAMA_OPENSSL OFF CACHE BOOL "" FORCE)
set(LLAMA_SUBPROCESS OFF CACHE BOOL "" FORCE)
set(MTMD_VIDEO OFF CACHE BOOL "Skip ffmpeg video path unless you install ffmpeg + enable subprocess" FORCE)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(GGML_CCACHE ON CACHE BOOL "" FORCE)
