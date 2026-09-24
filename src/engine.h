#pragma once
#include <stdexcept>
#include <string>
#include <vector>

namespace trident {
struct Knobs {
    int gpu = 0, seed = 42, n_predict = 1000, cfm_steps = 2, trim_fade = 480, top_k = 1000;
    float temperature = 0.8f, top_p = 0.95f, repeat_penalty = 1.2f;
    float min_p = 0.05f, cfg_weight = 0.5f, exaggeration = 0.5f, cfm_cfg = 0.7f;

    static Knobs gpt2() { return {}; }
    static Knobs v3() {
        Knobs knobs;
        knobs.top_p = 1.f;
        knobs.cfm_steps = 10;
        return knobs;
    }

    void set(const std::string& name, const std::string& value, bool llama) {
        auto integer = [&] { return std::stoi(value); };
        auto real = [&] { return std::stof(value); };
        if (name == "--seed") seed = integer();
        else if (name == "--temperature") temperature = real();
        else if (name == "--top-p") top_p = real();
        else if (name == "--repeat-penalty") repeat_penalty = real();
        else if (name == "--n-predict") n_predict = integer();
        else if (name == "--cfm-steps") cfm_steps = integer();
        else if (name == "--trim-fade-samples") trim_fade = integer();
        else if (!llama && name == "--top-k") top_k = integer();
        else if (llama && name == "--min-p") min_p = real();
        else if (llama && name == "--cfg-weight") cfg_weight = real();
        else if (llama && name == "--exaggeration") exaggeration = real();
        else if (llama && name == "--cfm-cfg") cfm_cfg = real();
        else throw std::runtime_error("unknown flag for this architecture: " + name);
    }
};
struct Synth {
    virtual ~Synth() = default;
    virtual std::vector<float> synthesize(const std::string& text, const std::string& language) = 0;
};
}
