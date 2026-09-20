#include "audio.h"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <limits>
#include <numeric>
#include <stdexcept>

namespace trident {
void Audio::fade(std::vector<float>& pcm, size_t length) {
    std::fill_n(pcm.begin(), std::min(pcm.size(), length), 0.f);
    for (size_t i = length; i < std::min(pcm.size(), 2 * length); ++i)
        pcm[i] *= length > 1 ? 0.5f * (1.f - std::cos(float(M_PI) * float(i - length) / float(length - 1))) : 1.f;
}

namespace {
class Bytes {
public:
    static uint16_t u16(const unsigned char* p) { return uint16_t(p[0] | p[1] << 8); }
    static uint32_t u32(const unsigned char* p) { return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24; }
};
class Biquad {
    double b0, b1, b2, a1, a2, x1 = 0, x2 = 0, y1 = 0, y2 = 0;
public:
    Biquad(int rate, bool shelf) {
        double frequency = shelf ? 1681.97453899761 : 38.13547087602444;
        double quality = shelf ? 0.7071752369554196 : 0.5003270373238773;
        double k = std::tan(M_PI * frequency / rate), a0 = 1 + k / quality + k * k;
        double vh = std::pow(10.0, 3.999843853973347 / 20.0), vb = std::pow(vh, 0.499666774155719);
        b0 = (shelf ? vh + vb * k / quality + k * k : 1) / a0;
        b1 = (shelf ? 2 * (k * k - vh) : -2) / a0;
        b2 = (shelf ? vh - vb * k / quality + k * k : 1) / a0;
        a1 = 2 * (k * k - 1) / a0;
        a2 = (1 - k / quality + k * k) / a0;
    }
    double process(double x) {
        double y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2;
        x2 = x1; x1 = x; y2 = y1; y1 = y;
        return y;
    }
};
}
Audio::Audio(std::vector<float> pcm, int rate) : samples(std::move(pcm)), sample_rate(rate) {}
Audio::Audio(const std::string& path) {
    std::ifstream input(std::filesystem::u8path(path), std::ios::binary);
    if (!input) throw std::runtime_error("Cannot open WAV: " + path);
    std::vector<unsigned char> bytes((std::istreambuf_iterator<char>(input)), {});
    uint16_t format = 0, channels = 0, bits = 0, block = 0;
    const unsigned char* data = nullptr;
    size_t length = 0;
    for (size_t offset = 12; offset + 8 <= bytes.size();) {
        auto count = Bytes::u32(bytes.data() + offset + 4);
        auto* chunk = bytes.data() + offset + 8;
        if (std::memcmp(bytes.data() + offset, "fmt ", 4) == 0) {
            format = Bytes::u16(chunk); channels = Bytes::u16(chunk + 2);
            sample_rate = int(Bytes::u32(chunk + 4)); block = Bytes::u16(chunk + 12); bits = Bytes::u16(chunk + 14);
            if (format == 0xfffe) format = Bytes::u16(chunk + 24);
        }
        if (std::memcmp(bytes.data() + offset, "data", 4) == 0) { data = chunk; length = count; }
        offset += 8 + count + (count & 1u);
    }
    samples.resize(length / block);
    for (size_t i = 0; i < samples.size(); ++i) {
        float sum = 0;
        for (uint16_t channel = 0; channel < channels; ++channel) {
            auto* value = data + i * block + channel * (bits / 8);
            float decoded;
            if (format == 1 && bits == 8) decoded = (int(value[0]) - 128) / 128.f;
            else if (format == 1 && bits == 16) decoded = int16_t(Bytes::u16(value)) / 32768.f;
            else if (format == 1 && bits == 24) {
                int32_t integer = value[0] | value[1] << 8 | value[2] << 16;
                if (integer & 0x800000) integer |= ~0xffffff;
                decoded = integer / 8388608.f;
            } else if (format == 1 && bits == 32) decoded = int32_t(Bytes::u32(value)) / 2147483648.f;
            else if (format == 3 && bits == 32) std::memcpy(&decoded, value, 4);
            else throw std::runtime_error("Unsupported WAV encoding");
            sum += decoded;
        }
        samples[i] = sum / channels;
    }
}
std::vector<double> Audio::lowpass(int taps, double pass, double stop) {
    const int middle = (taps - 1) / 2, size = middle + 1;
    auto sinc = [](double x) { return std::fabs(x) < 1e-20 ? 1.0 : std::sin(M_PI * x) / (M_PI * x); };
    std::vector<double> q(taps), lower(size * size, 0), rhs(size), solution(size), intermediate(size);
    for (int i = 0; i < taps; ++i) q[i] = pass * sinc(pass * i) + sinc(i) - stop * sinc(stop * i);
    auto at = [&](int i, int j) -> double& { return lower[i * size + j]; };
    for (int k = 0; k < size; ++k) {
        double diagonal = q[0] + q[2 * k];
        for (int p = 0; p < k; ++p) diagonal -= at(k, p) * at(k, p);
        if (!(diagonal > 1e-18)) throw std::runtime_error("Resampling filter factorization failed");
        at(k, k) = std::sqrt(diagonal);
        for (int i = k + 1; i < size; ++i) {
            double value = q[std::abs(i - k)] + q[i + k];
            for (int p = 0; p < k; ++p) value -= at(i, p) * at(k, p);
            at(i, k) = value / at(k, k);
        }
    }
    for (int i = 0; i < size; ++i) {
        double value = pass * sinc(pass * i);
        for (int p = 0; p < i; ++p) value -= at(i, p) * intermediate[p];
        intermediate[i] = value / at(i, i);
    }
    for (int i = size - 1; i >= 0; --i) {
        double value = intermediate[i];
        for (int p = i + 1; p < size; ++p) value -= at(p, i) * solution[p];
        solution[i] = value / at(i, i);
    }
    std::vector<double> filter(taps);
    filter[middle] = 2 * solution[0];
    for (int i = 1; i <= middle; ++i) filter[middle - i] = filter[middle + i] = solution[i];
    return filter;
}
Audio Audio::resample(int rate) const {
    if (rate == sample_rate) return *this;
    int divisor = std::gcd(rate, sample_rate), up = rate / divisor, down = sample_rate / divisor;
    auto filter = lowpass(641, 0.915 / down, 1.0 / down);
    for (auto& value : filter) value *= up;
    int before = down - (320 % down), after = 0;
    size_t count = (samples.size() * up + down - 1) / down, remove = (320 + before) / down;
    while (((samples.size() - 1) * up + filter.size() + before + after - 1) / down + 1 < count + remove) ++after;
    std::vector<double> padded(before + filter.size() + after, 0);
    std::copy(filter.begin(), filter.end(), padded.begin() + before);
    std::vector<float> result(count);
    for (size_t i = 0; i < count; ++i) {
        int64_t time = int64_t(i + remove) * down;
        double sum = 0;
        for (size_t k = 0; k < padded.size(); ++k) {
            int64_t index = time - int64_t(k);
            if (index < 0 || index % up || index / up >= int64_t(samples.size())) continue;
            sum += padded[k] * double(samples[size_t(index / up)]);
        }
        result[i] = float(sum);
    }
    return Audio(std::move(result), rate);
}
Audio& Audio::normalize(double target) {
    Biquad shelf(sample_rate, true), highpass(sample_rate, false);
    std::vector<double> filtered(samples.size());
    for (size_t i = 0; i < samples.size(); ++i) filtered[i] = highpass.process(shelf.process(samples[i]));
    int block = int(std::round(0.4 * sample_rate)), hop = int(std::round(0.1 * sample_rate));
    int count = (int(samples.size()) - block) / hop + 1;
    std::vector<double> energy(count), loudness(count);
    double absolute = 0;
    int absolute_count = 0;
    for (int i = 0; i < count; ++i) {
        double sum = 0;
        for (int j = 0; j < block; ++j) { double value = filtered[i * hop + j]; sum += value * value; }
        energy[i] = sum / block;
        loudness[i] = -0.691 + 10 * std::log10(std::max(energy[i], 1e-30));
        if (loudness[i] >= -70) { absolute += energy[i]; ++absolute_count; }
    }
    double threshold = -0.691 + 10 * std::log10(std::max(absolute / absolute_count, 1e-30)) - 10;
    double relative = 0;
    int relative_count = 0;
    for (int i = 0; i < count; ++i)
        if (loudness[i] >= -70 && loudness[i] >= threshold) { relative += energy[i]; ++relative_count; }
    double measured = -0.691 + 10 * std::log10(std::max(relative / relative_count, 1e-30));
    double gain = std::pow(10.0, (target - measured) / 20);
    for (auto& value : samples) value = float(double(value) * gain);
    return *this;
}
Audio& Audio::trim(float top_db) {
    constexpr int frame = 2048, hop = 512, pad = frame / 2;
    int count = 1 + int(samples.size()) / hop;
    std::vector<float> padded(samples.size() + 2 * pad, 0), rms(count);
    std::copy(samples.begin(), samples.end(), padded.begin() + pad);
    float reference = 0;
    for (int i = 0; i < count; ++i) {
        float sum = 0;
        for (int j = 0; j < frame; ++j) { float value = padded[i * hop + j]; sum += value * value; }
        rms[i] = std::sqrt(sum * (1.f / frame));
        reference = std::max(reference, rms[i]);
    }
    float log_reference = 10.f * std::log10(std::max(1e-5f * 1e-5f, reference * reference));
    int first = -1, last = -1;
    for (int i = 0; i < count; ++i)
        if (10.f * std::log10(std::max(1e-5f * 1e-5f, rms[i] * rms[i])) - log_reference > -top_db) {
            if (first < 0) first = i;
            last = i;
        }
    samples = std::vector<float>(samples.begin() + first * hop,
                                 samples.begin() + std::min(int(samples.size()), (last + 1) * hop));
    return *this;
}
Audio& Audio::take_seconds(int seconds) {
    samples.resize(std::min(samples.size(), size_t(seconds) * sample_rate));
    return *this;
}
void Audio::write(const std::string& path) const {
    std::vector<int16_t> pcm(samples.size());
    for (size_t i = 0; i < samples.size(); ++i) pcm[i] = int16_t(std::clamp(samples[i], -1.f, 1.f) * 32767.f);
    std::ofstream output(std::filesystem::u8path(path), std::ios::binary);
    output.exceptions(std::ios::badbit | std::ios::failbit);
    auto u16 = [&](uint16_t value) { output.write(reinterpret_cast<const char*>(&value), 2); };
    auto u32 = [&](uint32_t value) { output.write(reinterpret_cast<const char*>(&value), 4); };
    uint32_t bytes = uint32_t(pcm.size() * 2);
    output.write("RIFF", 4); u32(36 + bytes); output.write("WAVEfmt ", 8);
    u32(16); u16(1); u16(1); u32(24000); u32(48000); u16(2); u16(16);
    output.write("data", 4); u32(bytes); output.write(reinterpret_cast<const char*>(pcm.data()), bytes);
}
std::vector<float> Audio::spectrum(const std::vector<float>& frames, const std::vector<float>& filters,
    const VulkanBackend& backend, int count, int fft, int channels, float power, float floor) {
    int frequencies = fft / 2 + 1;
    std::vector<float> cosine(frequencies * fft), sine(frequencies * fft);
    for (int k = 0; k < frequencies; ++k)
        for (int n = 0; n < fft; ++n) {
            double angle = 2.0 * M_PI * k * n / fft;
            cosine[k * fft + n] = float(std::cos(angle)); sine[k * fft + n] = -float(std::sin(angle));
        }
    Graph graph(backend, 64);
    auto* ctx = graph.context();
    auto* input = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, fft, count);
    auto* cos = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, fft, frequencies);
    auto* sin = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, fft, frequencies);
    auto* mel = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, frequencies, channels);
    ggml_set_name(input, "frames"); ggml_set_name(cos, "cos"); ggml_set_name(sin, "sin"); ggml_set_name(mel, "mel");
    for (auto* tensor : {input, cos, sin, mel}) ggml_set_input(tensor);
    auto* real = ggml_mul_mat(ctx, cos, input);
    auto* imaginary = ggml_mul_mat(ctx, sin, input);
    auto* magnitude = ggml_add(ctx, ggml_sqr(ctx, real), ggml_sqr(ctx, imaginary));
    if (power == 1.f) magnitude = ggml_sqrt(ctx, magnitude);
    auto* result = ggml_mul_mat(ctx, mel, magnitude);
    if (floor > 0) result = ggml_log(ctx, ggml_clamp(ctx, result, floor, 1e30f));
    ggml_set_name(result, "out"); ggml_set_output(result); ggml_build_forward_expand(graph.graph, result);
    graph.allocate();
    graph.set("frames", frames.data(), frames.size() * sizeof(float));
    graph.set("cos", cosine.data(), cosine.size() * sizeof(float));
    graph.set("sin", sine.data(), sine.size() * sizeof(float));
    graph.set("mel", filters.data(), filters.size() * sizeof(float));
    graph.compute();
    return graph.read("out");
}
std::vector<float> Audio::mel(const std::vector<float>& filters, const VulkanBackend& backend,
    int fft, int hop, int channels, bool centered, float power, float floor) const {
    int pad = centered ? fft / 2 : (fft - hop) / 2;
    int length = int(samples.size()), count = centered ? 1 + length / hop : (length + 2 * pad - fft) / hop + 1;
    std::vector<float> padded(length + 2 * pad), window(fft), frames(count * fft);
    std::copy(samples.begin(), samples.end(), padded.begin() + pad);
    for (int i = 0; i < pad; ++i) { padded[i] = samples[pad - i]; padded[length + pad + i] = samples[length - 2 - i]; }
    for (int i = 0; i < fft; ++i) window[i] = 0.5f * (1.f - std::cos(2.f * float(M_PI) * float(i) / float(fft)));
    for (int t = 0; t < count; ++t)
        for (int i = 0; i < fft; ++i) frames[t * fft + i] = padded[t * hop + i] * window[i];
    return spectrum(frames, filters, backend, count, fft, channels, power, floor);
}
std::vector<float> Audio::kaldi(const std::vector<float>& filters, const VulkanBackend& backend) const {
    int count = (int(samples.size()) - 400) / 160 + 1;
    std::vector<float> window(400), frames(count * 512, 0);
    for (int i = 0; i < 400; ++i) window[i] = float(std::pow(0.5 - 0.5 * std::cos(2.0 * M_PI * i / 399), 0.85));
    for (int t = 0; t < count; ++t) {
        float* frame = frames.data() + t * 512;
        std::copy_n(samples.data() + t * 160, 400, frame);
        double sum = 0;
        for (int i = 0; i < 400; ++i) sum += frame[i];
        float dc = float(sum / 400);
        for (int i = 0; i < 400; ++i) frame[i] -= dc;
        for (int i = 399; i > 0; --i) frame[i] -= 0.97f * frame[i - 1];
        frame[0] *= 1.f - 0.97f;
        for (int i = 0; i < 400; ++i) frame[i] *= window[i];
    }
    return spectrum(frames, filters, backend, count, 512, int(filters.size() / 257), 2.f, std::numeric_limits<float>::epsilon());
}
}
