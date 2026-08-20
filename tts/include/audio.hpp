#pragma once

#include <string>
#include <vector>

namespace tts {

constexpr int kGlue = 2880;

struct Runtime {
    std::string t3, s3;
    int gpu = 0, threads = 0, context = 0;
};

struct Speech {
    std::vector<float> pcm;
    double t3_ms = 0, s3gen_ms = 0;
    int chunks = 0, t3_tokens = 0;
};

std::vector<std::string> pack_text(const std::string& text, int limit);
void glue(std::vector<float>& dst, const std::vector<float>& src, float quiet_amp2);
void write_wav(const std::string& path, const std::vector<float>& pcm);

struct EngineKnobs {
    std::string reference, language;
    int seed = 0, max_tokens = 0, top_k = 0, cfm_steps = 0;
    float exaggeration = 0, cfg_weight = 0, temperature = 0, repeat_penalty = 0, min_p = 0, top_p = 0;
};

Speech run(const Runtime& runtime, const EngineKnobs& knobs, const std::string& text, int chunk_chars, float quiet_amp2);

}
