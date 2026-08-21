#pragma once

#include "audio.hpp"

#include <memory>
#include <string>

namespace tts {

// A long-lived Chatterbox synthesis context. Construction loads T3 + S3Gen and
// computes all reference-dependent voice conditioning once. Every subsequent
// synthesize() call reuses the same Engine/model/backend allocations.
class Session {
public:
    Session(const Runtime& runtime, const EngineKnobs& knobs, int chunk_chars, float quiet_amp2 = kQuietAmp2, int first_chunk_chars = 0);
    ~Session();

    Session(const Session&) = delete;
    Session& operator=(const Session&) = delete;

    Speech synthesize(const std::string& text);

    const Runtime& runtime() const noexcept;
    const EngineKnobs& knobs() const noexcept;
    int chunk_chars() const noexcept;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace tts
