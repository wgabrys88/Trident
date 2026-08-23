#pragma once

#include "audio.hpp"

#include <memory>
#include <string>

namespace tts {


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

}
