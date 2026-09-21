#pragma once
#include "vulkan_backend.h"
#include <string>
#include <vector>

namespace trident {
class Audio {
public:
    std::vector<float> samples;
    int sample_rate;
    explicit Audio(const std::string& path);
    Audio(std::vector<float> pcm, int rate);
    Audio resample(int rate) const;
    Audio& normalize(double target = -27.0);
    Audio& trim(float top_db = 20.f);
    Audio& take_seconds(int seconds);
    static void fade(std::vector<float>& pcm, size_t length);
    std::vector<float> mel(const std::vector<float>& filters, const VulkanBackend& backend,
                           int fft, int hop, int channels, bool centered, float power, float floor) const;
    std::vector<float> kaldi(const std::vector<float>& filters, const VulkanBackend& backend) const;
private:
    static std::vector<double> lowpass(int taps, double pass, double stop);
    static std::vector<float> spectrum(const std::vector<float>& frames, const std::vector<float>& filters,
        const VulkanBackend& backend, int count, int fft, int channels, float power, float floor);
};
}
