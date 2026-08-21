#pragma once

#include <cstddef>
#include <cstdint>

namespace tts {

const char* pcm_simd_backend();
void pcm_f32_to_i16(const float* input, int16_t* output, std::size_t count);

}
