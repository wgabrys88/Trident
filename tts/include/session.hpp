#pragma once

#include "audio.hpp"
#include "cli.hpp"

#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace tts {

class Session {
public:
    struct Speech {
        std::vector<float> pcm;
        double t3_ms = 0.0;
        double s3gen_ms = 0.0;
        int t3_tokens = 0;
        int sample_rate = 24000;
    };

    Session(const std::string& t3_gguf, const std::string& s3gen_gguf, const Runtime& runtime, const EngineKnobs& knobs, int chunk_chars, float quiet_amp2, int first_chunk_chars);
    ~Session();

    std::vector<Speech> synthesize_pieces(const std::vector<std::string>& texts, const StreamingSink& sink);
    void request_cancel();

private:
    struct Impl;
    Impl* pimpl_;
};

}
