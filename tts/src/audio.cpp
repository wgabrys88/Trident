#include "audio.hpp"
#include <algorithm>

namespace tts {

std::vector<std::int16_t> pcm16(const float* pcm, std::size_t count) {
    std::vector<std::int16_t> samples(count);
    for (std::size_t i = 0; i < count; ++i) {
        const float clipped = std::max(-1.0f, std::min(1.0f, pcm[i]));
        samples[i] = static_cast<std::int16_t>(clipped * 32767.0f);
    }
    return samples;
}

}
