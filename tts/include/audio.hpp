#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace tts {

inline constexpr int kRate = 24000;

std::vector<std::int16_t> pcm16(const float* pcm, std::size_t count);

}
