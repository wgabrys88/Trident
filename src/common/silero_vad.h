#pragma once
#include <filesystem>
#include <string>
#include <vector>

namespace trident {
struct VadEvent {
    bool start = false;
    bool end = false;
};

class SileroVad {
public:
    SileroVad(const std::filesystem::path& onnx, int rate, int window, float threshold, int min_silence_ms);
    ~SileroVad();
    SileroVad(const SileroVad&) = delete;
    SileroVad& operator=(const SileroVad&) = delete;
    VadEvent feed(const float* samples, int count);
    void reset();
private:
    struct Impl;
    Impl* impl = nullptr;
};
}
