cmake_minimum_required(VERSION 3.16)
project(trident_tts LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
add_compile_definitions(_CRT_SECURE_NO_WARNINGS NOMINMAX WIN32_LEAN_AND_MEAN)
add_compile_options(/MP /bigobj)

if(NOT CHATTERBOX_CPP_ROOT)
    get_filename_component(CHATTERBOX_CPP_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../third_party/chatterbox.cpp" ABSOLUTE)
endif()
if(NOT EXISTS "${CHATTERBOX_CPP_ROOT}/include/tts-cpp/chatterbox/engine.h")
    message(FATAL_ERROR "chatterbox.cpp missing at ${CHATTERBOX_CPP_ROOT}")
endif()

find_package(Vulkan REQUIRED)
set(CHATTERBOX_BUILD "${CHATTERBOX_CPP_ROOT}/build")
set(GGML_BUILD "${CHATTERBOX_BUILD}/ggml/src")

add_executable(trident-tts-server
    src/server.cpp
)
target_include_directories(trident-tts-server PRIVATE
    "${CHATTERBOX_CPP_ROOT}/include" "${CHATTERBOX_CPP_ROOT}/ggml/include" ${Vulkan_INCLUDE_DIRS})
target_link_libraries(trident-tts-server PRIVATE
    "${CHATTERBOX_BUILD}/Release/tts-cpp.lib"
    "${CHATTERBOX_BUILD}/Release/mtl_tokenizer.lib"
    "${GGML_BUILD}/Release/ggml.lib"
    "${GGML_BUILD}/Release/ggml-cpu.lib"
    "${GGML_BUILD}/ggml-vulkan/Release/ggml-vulkan.lib"
    "${GGML_BUILD}/Release/ggml-base.lib"
    ${Vulkan_LIBRARIES})
target_compile_definitions(trident-tts-server PRIVATE GGML_USE_VULKAN _USE_MATH_DEFINES)
target_link_libraries(trident-tts-server PRIVATE ws2_32)
add_custom_command(TARGET trident-tts-server POST_BUILD COMMAND ${CMAKE_COMMAND} -E copy_if_different
    "${CHATTERBOX_BUILD}/bin/Release/ggml.dll"
    "${CHATTERBOX_BUILD}/bin/Release/ggml-cpu.dll"
    "${CHATTERBOX_BUILD}/bin/Release/ggml-vulkan.dll"
    "${CHATTERBOX_BUILD}/bin/Release/ggml-base.dll"
    $<TARGET_FILE_DIR:trident-tts-server>)
