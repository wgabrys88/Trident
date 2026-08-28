#include "cli.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>

namespace tts {

thread_local std::uint64_t request_id = 0;

Args parse_args(int argc, char** argv, const std::vector<std::string>& required) {
    Args args;
    for (int i = 1; i < argc; ++i) {
        const std::string key = argv[i];
        if (std::find(required.begin(), required.end(), key) == required.end())
            throw std::invalid_argument("unknown argument: " + key);
        if (i + 1 >= argc) throw std::invalid_argument("missing value for " + key);
        if (!args.emplace(key, argv[++i]).second) throw std::invalid_argument("duplicate argument: " + key);
    }
    for (const auto& key : required)
        if (!args.count(key) || args.at(key).empty()) throw std::invalid_argument("missing value for " + key);
    return args;
}

int parse_int(const Args& args, const std::string& key) {
    const std::string& raw = args.at(key);
    size_t used = 0;
    long long value = 0;
    try { value = std::stoll(raw, &used); }
    catch (const std::exception&) { throw std::invalid_argument("invalid integer for " + key + ": " + raw); }
    if (used != raw.size() || value < std::numeric_limits<int>::min() || value > std::numeric_limits<int>::max())
        throw std::invalid_argument("invalid integer for " + key + ": " + raw);
    return static_cast<int>(value);
}

float parse_float(const Args& args, const std::string& key) {
    const std::string& raw = args.at(key);
    size_t used = 0;
    float value = 0;
    try { value = std::stof(raw, &used); }
    catch (const std::exception&) { throw std::invalid_argument("invalid number for " + key + ": " + raw); }
    if (used != raw.size() || !std::isfinite(value)) throw std::invalid_argument("invalid number for " + key + ": " + raw);
    return value;
}

void set_request_id(std::uint64_t id) { request_id = id; }

void log(const std::string& line) {
    const auto wall_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    std::cerr << "tts ts_unix_ns=" << wall_ns << " request=" << request_id << " " << line << std::endl;
}

Runtime runtime_from(const Args& args) {
    Runtime runtime;
    runtime.t3 = args.at("--model");
    runtime.s3 = args.at("--s3gen-gguf");
    runtime.gpu = parse_int(args, "--n-gpu-layers");
    runtime.context = parse_int(args, "--context");
    runtime.threads = parse_int(args, "--threads");
    runtime.fastconv = parse_int(args, "--fastconv") != 0;
    runtime.stream_chunk_tokens       = parse_optional_int(args, "--stream-chunk-tokens", 0);
    runtime.stream_first_chunk_tokens = parse_optional_int(args, "--stream-first-chunk-tokens", 0);
    runtime.stream_cfm_steps          = parse_optional_int(args, "--stream-cfm-steps", 0);
    if (runtime.gpu < 0 || runtime.context < 1 || runtime.threads < 1)
        throw std::invalid_argument("integer runtime values are out of range");
    if (runtime.stream_chunk_tokens < 0 || runtime.stream_first_chunk_tokens < 0 || runtime.stream_cfm_steps < 0)
        throw std::invalid_argument("streaming chunk values must be non-negative");
    return runtime;
}

int parse_optional_int(const Args& args, const std::string& key, int default_value) {
    auto it = args.find(key);
    if (it == args.end()) return default_value;
    return parse_int(args, key);
}


}
