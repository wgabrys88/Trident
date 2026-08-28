#pragma once

#include "audio.hpp"

#include <atomic>
#include <cstddef>
#include <functional>
#include <memory>
#include <string>

namespace tts {

using AudioSink = std::function<void(const float*, std::size_t)>;
using StreamingSink = std::function<void(const float*, std::size_t, int, bool)>;

class Session {
public:
    Session(const Runtime& runtime, const EngineKnobs& knobs, int chunk_chars, float quiet_amp2 = kQuietAmp2, int first_chunk_chars = 0);
    ~Session();
    Session(const Session&) = delete;
    Session& operator=(const Session&) = delete;
    Speech synthesize(const std::string& text, const AudioSink& sink);
    Speech synthesize_stream(const std::string& text, const StreamingSink& sink);
    void request_cancel();
    const Runtime& runtime() const noexcept;
    const EngineKnobs& knobs() const noexcept;
    int chunk_chars() const noexcept;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}
