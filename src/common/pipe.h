#pragma once
#include "../engine.h"
#include <map>
#include <stdexcept>
#include <string>

namespace trident {
class Flags {
    std::map<std::string, std::string> values_;
public:
    Flags(int argc, char** argv) {
        if (argc < 4 || (argc - 4) % 2) throw std::runtime_error("Expected T3.gguf S3.gguf and flag/value pairs");
        for (int i = 4; i < argc; i += 2)
            if (!values_.emplace(argv[i], argv[i + 1]).second) throw std::runtime_error("Duplicate flag");
    }
    std::string string(const char* name) {
        auto value = values_.at(name); values_.erase(name); return value;
    }
    int integer(const char* name) { return std::stoi(string(name)); }
    float real(const char* name) { return std::stof(string(name)); }
    void finish() const { if (!values_.empty()) throw std::runtime_error("Unknown flag: " + values_.begin()->first); }
};
inline Knobs knobs_from(Flags& flags, bool llama) {
    Knobs knobs{};
    knobs.gpu = flags.integer("--gpu");
    knobs.seed = flags.integer("--seed");
    knobs.n_predict = flags.integer("--n-predict");
    knobs.cfm_steps = flags.integer("--cfm-steps");
    knobs.trim_fade = flags.integer("--trim-fade-samples");
    knobs.temperature = flags.real("--temperature");
    knobs.top_p = flags.real("--top-p");
    knobs.repeat_penalty = flags.real("--repeat-penalty");
    if (llama) {
        knobs.min_p = flags.real("--min-p");
        knobs.cfg_weight = flags.real("--cfg-weight");
        knobs.exaggeration = flags.real("--exaggeration");
        knobs.cfm_cfg = flags.real("--cfm-cfg");
    } else knobs.top_k = flags.integer("--top-k");
    return knobs;
}
}
