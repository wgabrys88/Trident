cmake_minimum_required(VERSION 3.20)
project(gemma-brain LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT GEMMA_BACKEND)
    message(FATAL_ERROR "GEMMA_BACKEND is required")
endif()
if(NOT LLAMA_SRC)
    message(FATAL_ERROR "LLAMA_SRC is required")
endif()
if(NOT TRIDENT_UTF8_MANIFEST)
    message(FATAL_ERROR "TRIDENT_UTF8_MANIFEST is required")
endif()
if(NOT GEMMA_COMPILE OR NOT GEMMA_LINK OR NOT GEMMA_DEFINITIONS)
    message(FATAL_ERROR "GEMMA_COMPILE, GEMMA_LINK, and GEMMA_DEFINITIONS are required")
endif()
if(NOT DEFINED GEMMA_MSVC_ARCH_FLAG)
    message(FATAL_ERROR "GEMMA_MSVC_ARCH_FLAG is required")
endif()

include(${CMAKE_CURRENT_SOURCE_DIR}/cmake/HostCpu.cmake)

if(GEMMA_BACKEND STREQUAL "cuda")
    enable_language(CUDA)
    set(CMAKE_CUDA_STANDARD 17)
    set(CMAKE_CUDA_STANDARD_REQUIRED ON)
elseif(GEMMA_BACKEND STREQUAL "vulkan")
    find_package(Vulkan REQUIRED)
else()
    message(FATAL_ERROR "GEMMA_BACKEND must be vulkan or cuda")
endif()

add_subdirectory(${LLAMA_SRC} ${CMAKE_BINARY_DIR}/llama.cpp)

if(NOT TARGET mtmd)
    message(FATAL_ERROR "mtmd library missing")
endif()

separate_arguments(gemma_compile NATIVE_COMMAND "${GEMMA_COMPILE}")
separate_arguments(gemma_link NATIVE_COMMAND "${GEMMA_LINK}")
separate_arguments(gemma_definitions NATIVE_COMMAND "${GEMMA_DEFINITIONS}")

add_executable(gemma-brain src/brain.cpp)
target_link_libraries(gemma-brain PRIVATE llama-common mtmd)
target_include_directories(gemma-brain PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src
    ${LLAMA_SRC}/include
    ${LLAMA_SRC}/ggml/include
    ${LLAMA_SRC}/common
    ${LLAMA_SRC}/tools/mtmd)
target_compile_definitions(gemma-brain PRIVATE ${gemma_definitions})
if(GEMMA_BACKEND STREQUAL "cuda")
    target_compile_definitions(gemma-brain PRIVATE GEMMA_CUDA=1)
endif()

if(MSVC)
    target_link_options(gemma-brain PRIVATE "/MANIFEST:EMBED" "/MANIFESTINPUT:${TRIDENT_UTF8_MANIFEST}" ${gemma_link})
    set(_gemma_host_opts ${gemma_compile})
    if(GEMMA_MSVC_ARCH_FLAG)
        list(APPEND _gemma_host_opts ${GEMMA_MSVC_ARCH_FLAG})
    endif()
    target_compile_options(gemma-brain PRIVATE ${_gemma_host_opts})
    foreach(t llama-common llama ggml mtmd)
        if(TARGET ${t})
            target_compile_options(${t} PRIVATE $<$<COMPILE_LANGUAGE:CXX>:${_gemma_host_opts}>)
        endif()
    endforeach()
endif()
