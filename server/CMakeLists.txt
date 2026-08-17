cmake_minimum_required(VERSION 3.16)
project(tts_server LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(WIN32)
    add_compile_definitions(_CRT_SECURE_NO_WARNINGS NOMINMAX WIN32_LEAN_AND_MEAN)
    add_compile_options(/MP /bigobj)
endif()
find_package(Vulkan REQUIRED)
if(NOT CHATTERBOX_CPP_ROOT)
    get_filename_component(CHATTERBOX_CPP_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../third_party/chatterbox.cpp" ABSOLUTE)
endif()
if(NOT EXISTS "${CHATTERBOX_CPP_ROOT}/include/tts-cpp/chatterbox/engine.h")
    message(FATAL_ERROR "chatterbox.cpp missing at ${CHATTERBOX_CPP_ROOT}")
endif()
include(FetchContent)
set(JSON_BuildTests OFF CACHE BOOL "" FORCE)
FetchContent_Declare(cpp_httplib GIT_REPOSITORY https://github.com/yhirose/cpp-httplib GIT_TAG f00e476f1b2d519343e960f77f57a06c8a24f046)
FetchContent_Declare(nlohmann_json GIT_REPOSITORY https://github.com/nlohmann/json.git GIT_TAG 55f93686c01528224f448c19128836e7df245f72)
FetchContent_MakeAvailable(cpp_httplib nlohmann_json)
set(CHATTERBOX_BUILD "${CHATTERBOX_CPP_ROOT}/build")
set(GGML_BUILD "${CHATTERBOX_BUILD}/ggml/src")
add_executable(tts-server src/server.cpp src/engine_wrapper.cpp src/main.cpp)
target_include_directories(tts-server PRIVATE include "${CHATTERBOX_CPP_ROOT}/include" "${CHATTERBOX_CPP_ROOT}/ggml/include" ${Vulkan_INCLUDE_DIRS})
target_link_libraries(tts-server PRIVATE
    "${CHATTERBOX_BUILD}/Release/tts-cpp.lib"
    "${CHATTERBOX_BUILD}/Release/mtl_tokenizer.lib"
    "${GGML_BUILD}/Release/ggml.lib"
    "${GGML_BUILD}/Release/ggml-cpu.lib"
    "${GGML_BUILD}/ggml-vulkan/Release/ggml-vulkan.lib"
    "${GGML_BUILD}/Release/ggml-base.lib"
    httplib::httplib nlohmann_json::nlohmann_json ${Vulkan_LIBRARIES} ws2_32)
target_compile_definitions(tts-server PRIVATE GGML_USE_VULKAN _USE_MATH_DEFINES)
if(WIN32)
    add_custom_command(TARGET tts-server POST_BUILD COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${CHATTERBOX_BUILD}/bin/Release/ggml.dll"
        "${CHATTERBOX_BUILD}/bin/Release/ggml-cpu.dll"
        "${CHATTERBOX_BUILD}/bin/Release/ggml-vulkan.dll"
        "${CHATTERBOX_BUILD}/bin/Release/ggml-base.dll"
        $<TARGET_FILE_DIR:tts-server>)
endif()
