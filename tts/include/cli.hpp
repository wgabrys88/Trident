#pragma once

#include <chrono>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

namespace tts {

using Args = std::unordered_map<std::string, std::string>;

inline Args parse_args(int argc, char** argv, const std::vector<std::string>& required_keys) {
    Args out;
    for (int i = 1; i + 1 < argc; i += 2) {
        out[argv[i]] = argv[i + 1];
    }
    for (const auto& key : required_keys) {
        if (!out.count(key)) throw std::invalid_argument("missing required " + key);
    }
    return out;
}

inline int parse_int(const Args& a, const std::string& k) {
    return std::stoi(a.at(k));
}

inline float parse_float(const Args& a, const std::string& k) {
    return std::stof(a.at(k));
}

struct EngineKnobs {
    std::string reference;
    std::string language;
    int seed = 42;
    int max_tokens = 768;
    int top_k = 1000;
    float top_p = 0.95f;
    float min_p = 0.0f;
    float temperature = 0.8f;
    float repeat_penalty = 1.2f;
    float cfg_weight = 0.0f;
    float exaggeration = 0.0f;
    int cfm_steps = 2;
};

struct Runtime {
    int n_gpu_layers = 99;
    int context = 2048;
    int threads = 4;
    int fastconv = 1;
};

inline Runtime runtime_from(const Args& a) {
    Runtime r;
    r.n_gpu_layers = parse_int(a, "--n-gpu-layers");
    r.context = parse_int(a, "--context");
    r.threads = parse_int(a, "--threads");
    r.fastconv = parse_int(a, "--fastconv");
    return r;
}

inline void log(const std::string& line) {
    const auto ts = std::chrono::system_clock::now().time_since_epoch();
    const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(ts).count();
    std::cerr << "tts ts_unix_ns=" << ns << " " << line << std::endl;
}

}
