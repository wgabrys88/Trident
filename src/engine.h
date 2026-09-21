#pragma once
#include <string>
#include <vector>

namespace trident {
struct Knobs {
    int gpu, seed, n_predict, cfm_steps, trim_fade, top_k;
    float temperature, top_p, repeat_penalty, min_p, cfg_weight, exaggeration, cfm_cfg;
};
struct TokenizerPaths {
    std::string python, script, source, tts_source, tokenizer_json, cangjie_json, dicta_model;
};
struct Synth {
    virtual ~Synth() = default;
    virtual std::vector<float> synthesize(const std::string& text, const std::string& language) = 0;
};
}
