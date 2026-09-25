#pragma once
#include <string>
#include <vector>

namespace trident {
struct Knobs {
    int gpu = 0, seed = 0, n_predict = 0, cfm_steps = 0, trim_fade = 0, top_k = 0, graph_nodes = 0, end_trim = 0;
    float temperature = 0.f, top_p = 0.f, repeat_penalty = 0.f;
    float min_p = 0.f, cfg_weight = 0.f, exaggeration = 0.f, cfm_cfg = 0.f;
};
struct Synth {
    virtual ~Synth() = default;
    virtual std::vector<float> synthesize(const std::string& text, const std::string& language) = 0;
};
}
