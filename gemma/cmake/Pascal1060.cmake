# NVIDIA GTX 1060 6GB (GP106, compute capability 6.1, sm_61)
# CUDA toolkit: use 12.6 or earlier (Pascal dropped in CUDA 12.7+).

include(${CMAKE_CURRENT_LIST_DIR}/PascalCuda.cmake)

# --- CUDA backend (ggml-cuda) ---
set(GGML_CUDA ON CACHE BOOL "")
set(GGML_CUDA_FORCE_MMQ ON CACHE BOOL "Pascal: prefer MMQ kernels over cuBLAS for quant matmul")
set(GGML_CUDA_FORCE_CUBLAS OFF CACHE BOOL "")
set(GGML_CUDA_FA ON CACHE BOOL "FlashAttention CUDA kernels (tile path on Pascal)")
set(GGML_CUDA_FA_ALL_QUANTS OFF CACHE BOOL "Keep compile time/size reasonable")
set(GGML_CUDA_GRAPHS ON CACHE BOOL "CUDA graphs in llama.cpp decode path")
set(GGML_CUDA_NCCL OFF CACHE BOOL "Single GPU — no NCCL")
set(GGML_CUDA_NO_PEER_COPY OFF CACHE BOOL "")
set(GGML_CUDA_NO_VMM OFF CACHE BOOL "")

# --- Inference on CUDA only; CPU backend still required for llama buft/host metadata ---
set(GGML_CPU ON CACHE BOOL "Required by llama.cpp loader (weights still -ngl 999 on GPU)")
set(GGML_VULKAN OFF CACHE BOOL "")
set(GGML_METAL OFF CACHE BOOL "")
set(GGML_OPENMP ON CACHE BOOL "Host-side parallel (mtmd decode)")
set(GGML_BLAS OFF CACHE BOOL "")
set(GGML_ACCELERATE OFF CACHE BOOL "")
set(GGML_NATIVE ON CACHE BOOL "Host ISA for CPU backend stubs")

# --- llama.cpp: Gemma 4 multimodal (text + image + audio on E2B) ---
set(LLAMA_BUILD_COMMON ON CACHE BOOL "")
set(LLAMA_BUILD_TOOLS OFF CACHE BOOL "gemma-brain links mtmd directly")
set(LLAMA_BUILD_MTMD ON CACHE BOOL "libmtmd for gemma-brain")
set(LLAMA_BUILD_TESTS OFF CACHE BOOL "")
set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "")
set(LLAMA_BUILD_SERVER OFF CACHE BOOL "")
set(LLAMA_CURL OFF CACHE BOOL "")
set(LLAMA_OPENSSL OFF CACHE BOOL "")
set(LLAMA_SUBPROCESS OFF CACHE BOOL "")
set(MTMD_VIDEO OFF CACHE BOOL "Skip ffmpeg video path unless you install ffmpeg + enable subprocess")
set(BUILD_SHARED_LIBS OFF CACHE BOOL "")
set(GGML_CCACHE ON CACHE BOOL "")
