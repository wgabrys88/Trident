#pragma once

#include <cstddef>
#include <memory>
#include <string>

namespace tts {

// Single-producer/single-consumer lock-free transport from synthesis to disk.
// It intentionally streams only audio that the overlap/glue stage has finalized.
class StreamingWavWriter {
public:
    explicit StreamingWavWriter(const std::string& path, std::size_t capacity_samples = 262144);
    ~StreamingWavWriter();

    StreamingWavWriter(const StreamingWavWriter&) = delete;
    StreamingWavWriter& operator=(const StreamingWavWriter&) = delete;

    void push(const float* samples, std::size_t count);
    void finish();

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}
