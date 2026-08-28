#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace tts {

constexpr int kRate = 24000;
constexpr int kGlue = 2880;
constexpr float kQuietAmp2 = 0.0004f;

inline int utf8_chars(const std::string& s) {
    int n = 0;
    for (unsigned char c : s)
        if ((c & 0xc0) != 0x80) ++n;
    return n;
}

struct Runtime {
    std::string t3, s3;
    int gpu = 0, threads = 0, context = 0;
    int stream_chunk_tokens = 0;
    int stream_first_chunk_tokens = 0;
    int stream_cfm_steps = 0;
    bool fastconv = false;
};

struct Speech {
    std::vector<float> pcm;
    double t3_ms = 0, s3gen_ms = 0, ttfa_ms = 0;
    int chunks = 0, t3_tokens = 0;
};

std::vector<std::string> pack_text(const std::string& text, int limit);
std::vector<std::string> pack_text_staged(const std::string& text, int first_limit, int later_limit);
void glue(std::vector<float>& dst, const std::vector<float>& src, float quiet_amp2);
std::vector<std::int16_t> pcm16(const float* pcm, std::size_t count);
void write_wav(const std::string& path, const std::vector<float>& pcm);

struct EngineKnobs {
    std::string reference, language;
    int seed = 0, max_tokens = 0, top_k = 0, cfm_steps = 0;
    float exaggeration = 0, cfg_weight = 0, temperature = 0, repeat_penalty = 0, min_p = 0, top_p = 0;
};


}
