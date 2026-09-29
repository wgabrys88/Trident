#include "s3_dsp.h"
#include <algorithm>
#include <cmath>

namespace trident {
float S3Dsp::positioned_noise(uint32_t seed, uint64_t position) {
    auto uniform = [](uint64_t x) {
        x += 0x9e3779b97f4a7c15ULL;
        x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
        x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
        return double((x ^ (x >> 31)) >> 11) * 0x1.0p-53;
    };
    uint64_t key = (uint64_t(seed) << 32) ^ (position * 2);
    return float(std::sqrt(-2.0 * std::log(std::max(1e-15, uniform(key)))) * std::cos(2.0 * M_PI * uniform(key + 1)));
}
std::vector<float> S3Dsp::hann(int length, bool periodic) {
    std::vector<float> window(length);
    double denominator = periodic ? double(length) : double(length - 1);
    for (int i = 0; i < length; ++i) window[i] = float(0.5 * (1.0 - std::cos(2.0 * M_PI * double(i) / denominator)));
    return window;
}
std::vector<float> S3Dsp::stft(int fft, const std::vector<float>& window) {
    int frequencies = fft / 2 + 1;
    std::vector<float> kernel(size_t(fft) * 2 * frequencies);
    for (int f = 0; f < frequencies; ++f)
        for (int n = 0; n < fft; ++n) {
            double angle = 2.0 * M_PI * f * n / fft;
            kernel[n + f * fft] = float(std::cos(angle) * window[n]);
            kernel[n + (frequencies + f) * fft] = float(-std::sin(angle) * window[n]);
        }
    return kernel;
}
std::vector<float> S3Dsp::istft(int fft, const std::vector<float>& window) {
    int frequencies = fft / 2 + 1;
    std::vector<float> kernel(size_t(fft) * 2 * frequencies);
    double inverse = 1.0 / double(fft);
    for (int f = 0; f < frequencies; ++f) {
        double real = (f == 0 || f == fft / 2) ? 1.0 : 2.0;
        double imaginary = (f == 0 || f == fft / 2) ? 0.0 : 2.0;
        for (int n = 0; n < fft; ++n) {
            double angle = 2.0 * M_PI * f * n / fft;
            kernel[n + f * fft] = float(real * std::cos(angle) * window[n] * inverse);
            kernel[n + (frequencies + f) * fft] = float(-imaginary * std::sin(angle) * window[n] * inverse);
        }
    }
    return kernel;
}
std::vector<float> S3Dsp::window_sum(int frames, int fft, int hop, const std::vector<float>& window) {
    std::vector<float> sum((frames - 1) * hop + fft, 0.f);
    for (int t = 0; t < frames; ++t)
        for (int n = 0; n < fft; ++n) sum[t * hop + n] += window[n] * window[n];
    return sum;
}
}
