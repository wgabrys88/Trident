cmake_minimum_required(VERSION 3.19)
project(trident LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_CONFIGURATION_TYPES Release CACHE STRING "" FORCE)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_BINARY_DIR}/bin")
set(BUILD_SHARED_LIBS ON CACHE BOOL "" FORCE)
set(CMAKE_SKIP_INSTALL_RULES ON CACHE BOOL "" FORCE)
set(GGML_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(GGML_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(GGML_VULKAN ON CACHE BOOL "" FORCE)
set(GGML_CPU OFF CACHE BOOL "" FORCE)
set(GGML_CUDA OFF CACHE BOOL "" FORCE)
set(GGML_OPENMP OFF CACHE BOOL "" FORCE)
find_package(Vulkan REQUIRED)
add_subdirectory(ggml)

add_library(trident STATIC
    src/common/gguf_file.cpp src/common/vulkan_backend.cpp
    src/common/audio.cpp src/common/voice_encoder.cpp
    src/common/campplus.cpp src/common/s3_tokenizer.cpp src/common/s3_dsp.cpp
    src/common/s3.cpp
    src/gpt2/t3.cpp src/gpt2/bpe.cpp src/gpt2/text_en.cpp src/gpt2/engine.cpp
    src/llama/t3.cpp src/llama/mtl_tokenizer.cpp src/llama/mtl_numbers.cpp src/llama/engine.cpp)
target_include_directories(trident PUBLIC src ggml/include)
target_link_libraries(trident PUBLIC ggml Vulkan::Vulkan icu)

add_executable(chatterbox-server src/server.cpp)
add_executable(chatterbox-bake src/bake.cpp)
target_link_libraries(chatterbox-server PRIVATE trident)
target_link_libraries(chatterbox-bake PRIVATE trident)

foreach(target trident chatterbox-server chatterbox-bake)
    target_compile_definitions(${target} PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN
        GGML_USE_VULKAN _USE_MATH_DEFINES _CRT_SECURE_NO_WARNINGS)
    target_compile_options(${target} PRIVATE /bigobj /O2 /DNDEBUG)
endforeach()
