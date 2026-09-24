cmake_minimum_required(VERSION 3.20)
project(gemma-brain LANGUAGES C CXX CUDA)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

set(CMAKE_CUDA_STANDARD 17)
set(CMAKE_CUDA_STANDARD_REQUIRED ON)

if(NOT CMAKE_CUDA_COMPILER)
    message(FATAL_ERROR
        "CUDA compiler (nvcc) is required. GPU-only build.\n"
        "  Install CUDA 12.6 + Visual Studio CUDA integration.\n"
        "  cmake -D CMAKE_CUDA_COMPILER=\"C:/.../CUDA/v12.6/bin/nvcc.exe\" ...")
endif()

if(MSVC AND CMAKE_CUDA_COMPILER_VERSION VERSION_GREATER_EQUAL "12.7")
    message(WARNING "CUDA ${CMAKE_CUDA_COMPILER_VERSION}: Pascal (GTX 1060) needs CUDA <= 12.6.")
endif()

include(${CMAKE_CURRENT_SOURCE_DIR}/cmake/HostCpu.cmake)
include(${CMAKE_CURRENT_SOURCE_DIR}/cmake/Pascal1060.cmake)

add_subdirectory(llama.cpp)

if(NOT TARGET mtmd)
    message(FATAL_ERROR "mtmd library missing — set LLAMA_BUILD_MTMD=ON in Pascal1060.cmake")
endif()

add_executable(gemma-brain src/brain.cpp)
target_link_libraries(gemma-brain PRIVATE llama-common mtmd)
target_include_directories(gemma-brain PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/llama.cpp/include
    ${CMAKE_CURRENT_SOURCE_DIR}/llama.cpp/ggml/include
    ${CMAKE_CURRENT_SOURCE_DIR}/llama.cpp/common
    ${CMAKE_CURRENT_SOURCE_DIR}/llama.cpp/tools/mtmd)
target_compile_definitions(gemma-brain PRIVATE GEMMA_GPU_ONLY=1 NOMINMAX WIN32_LEAN_AND_MEAN)
if(DEFINED GEMMA_HOST_THREADS)
    target_compile_definitions(gemma-brain PRIVATE GEMMA_HOST_THREADS=${GEMMA_HOST_THREADS})
endif()

if(MSVC)
    target_link_options(gemma-brain PRIVATE "/MANIFEST:EMBED" "/MANIFESTINPUT:${CMAKE_CURRENT_SOURCE_DIR}/../utf8.manifest")
    set(_gemma_host_opts /O2 /Ob2 /Oi /Ot /GL /fp:fast /Gy /Gw)
    if(GEMMA_MSVC_ARCH_FLAG)
        list(APPEND _gemma_host_opts ${GEMMA_MSVC_ARCH_FLAG})
    endif()
    target_compile_options(gemma-brain PRIVATE ${_gemma_host_opts})
    target_link_options(gemma-brain PRIVATE /LTCG)
    foreach(t llama-common llama ggml mtmd)
        if(TARGET ${t})
            target_compile_options(${t} PRIVATE $<$<COMPILE_LANGUAGE:CXX>:${_gemma_host_opts}>)
        endif()
    endforeach()
    # ggml-cuda: CUDA flags come only from upstream ggml (PascalCuda.cmake + Pascal1060.cmake).
endif()
