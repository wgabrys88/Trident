cmake_minimum_required(VERSION 3.20)
project(trident_tts LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(GGML_VULKAN ON CACHE BOOL "" FORCE)
set(GGML_CUDA OFF CACHE BOOL "" FORCE)
set(GGML_NATIVE ON CACHE BOOL "" FORCE)
set(GGML_CCACHE OFF CACHE BOOL "" FORCE)
set(TTS_CPP_BUILD_EXECUTABLES OFF CACHE BOOL "" FORCE)
set(TTS_CPP_BUILD_TESTS OFF CACHE BOOL "" FORCE)
add_compile_definitions(_CRT_SECURE_NO_WARNINGS NOMINMAX WIN32_LEAN_AND_MEAN)
add_compile_options(/MP /bigobj)
if(NOT CHATTERBOX_CPP_ROOT)
    get_filename_component(CHATTERBOX_CPP_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../third_party/chatterbox.cpp" ABSOLUTE)
endif()
if(NOT EXISTS "${CHATTERBOX_CPP_ROOT}/include/tts-cpp/chatterbox/engine.h")
    message(FATAL_ERROR "chatterbox.cpp missing at ${CHATTERBOX_CPP_ROOT}")
endif()
add_subdirectory("${CHATTERBOX_CPP_ROOT}" "${CMAKE_BINARY_DIR}/chatterbox" EXCLUDE_FROM_ALL)
add_executable(trident-tts-server
    src/audio.cpp
    src/cli.cpp
    src/server.cpp
    src/session.cpp
)
target_include_directories(trident-tts-server PRIVATE include "${CHATTERBOX_CPP_ROOT}/include")
target_link_libraries(trident-tts-server PRIVATE tts-cpp ws2_32)
