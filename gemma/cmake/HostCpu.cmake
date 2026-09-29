if(NOT DEFINED GEMMA_MSVC_ARCH_FLAG)
    message(FATAL_ERROR "GEMMA_MSVC_ARCH_FLAG is required")
endif()

message(STATUS "Host CPU arch=${GEMMA_MSVC_ARCH_FLAG}")
