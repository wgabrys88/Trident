#pragma once

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

namespace tts {

using Args = std::unordered_map<std::string, std::string>;

inline std::vector<std::string> split(const std::string& s, const std::string& sep) {
    std::vector<std::string> out;
    std::size_t pos = 0;
    while (pos < s.size()) {
        const std::size_t next = s.find(sep, pos);
        if (next == std::string::npos) {
            out.push_back(s.substr(pos));
            break;
        }
        out.push_back(s.substr(pos, next - pos));
        pos = next + sep.size();
    }
    return out;
}

inline std::string trim(const std::string& s) {
    const auto first = s.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return {};
    const auto last = s.find_last_not_of(" \t\r\n");
    return s.substr(first, last - first + 1);
}

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

inline int parse_optional_int(const Args& a, const std::string& k, int default_value) {
    auto it = a.find(k);
    if (it == a.end()) return default_value;
    return std::stoi(it->second);
}

inline float parse_optional_float(const Args& a, const std::string& k, float default_value) {
    auto it = a.find(k);
    if (it == a.end()) return default_value;
    return std::stof(it->second);
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
    int stream_chunk_tokens = 0;
    int stream_first_chunk_tokens = 0;
    int stream_cfm_steps = 0;
};

inline Runtime runtime_from(const Args& a) {
    Runtime r;
    r.n_gpu_layers = parse_optional_int(a, "--n-gpu-layers", r.n_gpu_layers);
    r.context = parse_optional_int(a, "--context", r.context);
    r.threads = parse_optional_int(a, "--threads", r.threads);
    r.fastconv = parse_optional_int(a, "--fastconv", r.fastconv);
    r.stream_chunk_tokens = parse_optional_int(a, "--stream-chunk-tokens", 0);
    r.stream_first_chunk_tokens = parse_optional_int(a, "--stream-first-chunk-tokens", 0);
    r.stream_cfm_steps = parse_optional_int(a, "--stream-cfm-steps", 0);
    return r;
}

inline void log(const std::string& line) {
    const auto ts = std::chrono::system_clock::now().time_since_epoch();
    const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(ts).count();
    std::cerr << "tts ts_unix_ns=" << ns << " " << line << std::endl;
}

inline void set_request_id(std::uint64_t id) {
    (void)id;
}

}
