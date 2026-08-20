cmake_minimum_required(VERSION 3.16)
project(trident_tts LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(WIN32)
    add_compile_definitions(_CRT_SECURE_NO_WARNINGS NOMINMAX WIN32_LEAN_AND_MEAN)
    add_compile_options(/MP /bigobj)
endif()

if(NOT CHATTERBOX_CPP_ROOT)
    get_filename_component(CHATTERBOX_CPP_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../third_party/chatterbox.cpp" ABSOLUTE)
endif()
set(CHATTERBOX_HEADER "${CHATTERBOX_CPP_ROOT}/include/tts-cpp/chatterbox/engine.h")
set(HAVE_CHATTERBOX OFF)
if(EXISTS "${CHATTERBOX_HEADER}")
    set(HAVE_CHATTERBOX ON)
endif()

if(NOT HAVE_CHATTERBOX)
    message(FATAL_ERROR "chatterbox.cpp missing at ${CHATTERBOX_CPP_ROOT}")
endif()

find_package(Vulkan REQUIRED)
set(CHATTERBOX_BUILD "${CHATTERBOX_CPP_ROOT}/build")
set(GGML_BUILD "${CHATTERBOX_BUILD}/ggml/src")

set(TTS_COMMON
    src/audio.cpp
    src/cli.cpp
    src/synthesize.cpp
)

function(trident_tts_bin name src)
    add_executable(${name} ${src} ${TTS_COMMON})
    target_include_directories(${name} PRIVATE include "${CHATTERBOX_CPP_ROOT}/include" "${CHATTERBOX_CPP_ROOT}/ggml/include" ${Vulkan_INCLUDE_DIRS})
    target_link_libraries(${name} PRIVATE
        "${CHATTERBOX_BUILD}/Release/tts-cpp.lib"
        "${CHATTERBOX_BUILD}/Release/mtl_tokenizer.lib"
        "${GGML_BUILD}/Release/ggml.lib"
        "${GGML_BUILD}/Release/ggml-cpu.lib"
        "${GGML_BUILD}/ggml-vulkan/Release/ggml-vulkan.lib"
        "${GGML_BUILD}/Release/ggml-base.lib"
        ${Vulkan_LIBRARIES})
    target_compile_definitions(${name} PRIVATE GGML_USE_VULKAN _USE_MATH_DEFINES)
    if(WIN32)
        add_custom_command(TARGET ${name} POST_BUILD COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${CHATTERBOX_BUILD}/bin/Release/ggml.dll"
            "${CHATTERBOX_BUILD}/bin/Release/ggml-cpu.dll"
            "${CHATTERBOX_BUILD}/bin/Release/ggml-vulkan.dll"
            "${CHATTERBOX_BUILD}/bin/Release/ggml-base.dll"
            $<TARGET_FILE_DIR:${name}>)
    endif()
endfunction()

file(GLOB FAMILY_MAINS CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/src/main_*.cpp")
foreach(src ${FAMILY_MAINS})
    get_filename_component(stem ${src} NAME_WE)
    string(REGEX REPLACE "^main_" "trident-tts-" target "${stem}")
    trident_tts_bin(${target} ${src})
endforeach()
