#pragma once
#include <cstdint>
#include <vector>

namespace trident {
class S3Dsp {
public:
    static float positioned_noise(uint32_t seed, uint64_t position);
    static std::vector<float> hann(int length, bool periodic = true);
    static std::vector<float> stft(int fft, const std::vector<float>& window);
    static std::vector<float> istft(int fft, const std::vector<float>& window);
    static std::vector<float> window_sum(int frames, int fft, int hop, const std::vector<float>& window);
};
}
