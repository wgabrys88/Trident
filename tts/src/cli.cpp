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
    catch (...) { throw std::invalid_argument("invalid integer for " + key + ": " + raw); }
    if (used != raw.size() || value < std::numeric_limits<int>::min() || value > std::numeric_limits<int>::max())
        throw std::invalid_argument("invalid integer for " + key + ": " + raw);
    return static_cast<int>(value);
}

float parse_float(const Args& args, const std::string& key) {
    const std::string& raw = args.at(key);
    size_t used = 0;
    float value = 0;
    try { value = std::stof(raw, &used); }
    catch (...) { throw std::invalid_argument("invalid number for " + key + ": " + raw); }
    if (used != raw.size() || !std::isfinite(value)) throw std::invalid_argument("invalid number for " + key + ": " + raw);
    return value;
}

double log_ms() {
    static const auto epoch = std::chrono::steady_clock::now();
    return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - epoch).count();
}

void set_request_id(std::uint64_t id) { request_id = id; }

void log(const std::string& line) {
    const auto wall_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    std::cerr << "tts ts_unix_ns=" << wall_ns << " t_ms=" << log_ms()
              << " request=" << request_id << " " << line << std::endl;
}

Runtime runtime_from(const Args& args) {
    Runtime runtime;
    runtime.t3 = args.at("--model");
    runtime.s3 = args.at("--s3gen-gguf");
    runtime.gpu = parse_int(args, "--n-gpu-layers");
    runtime.context = parse_int(args, "--context");
    runtime.threads = parse_int(args, "--threads");
    runtime.fastconv = parse_int(args, "--fastconv") != 0;
    if (runtime.gpu < 0 || runtime.context < 1 || runtime.threads < 1)
        throw std::invalid_argument("integer runtime values are out of range");
    return runtime;
}

void print_done(const Speech& speech, double total_ms, const Runtime& runtime, const EngineKnobs& knobs, int chunk_chars) {
    const double audio_ms = speech.pcm.size() * 1000.0 / kRate;
    const double compute_ms = speech.t3_ms + speech.s3gen_ms;
    const double rtf = audio_ms > 0 ? compute_ms / audio_ms : 0;
    const double wall_rtf = audio_ms > 0 ? total_ms / audio_ms : 0;
    const double overhead_ms = std::max(0.0, total_ms - compute_ms);
    const char* bottleneck = speech.s3gen_ms >= speech.t3_ms && speech.s3gen_ms >= overhead_ms ? "s3gen-cfm" :
                             (speech.t3_ms >= overhead_ms ? "t3-decode" : "host-overhead");
    log("event=done samples=" + std::to_string(speech.pcm.size()) +
        " seconds=" + std::to_string(speech.pcm.size() / static_cast<double>(kRate)) +
        " chunks=" + std::to_string(speech.chunks) +
        " t3_tokens=" + std::to_string(speech.t3_tokens) +
        " total_ms=" + std::to_string(total_ms) +
        " t3_ms=" + std::to_string(speech.t3_ms) +
        " s3gen_ms=" + std::to_string(speech.s3gen_ms) +
        " ttfa_ms=" + std::to_string(speech.ttfa_ms) +
        " rtf=" + std::to_string(rtf) +
        " wall_rtf=" + std::to_string(wall_rtf) +
        " overhead_ms=" + std::to_string(overhead_ms) +
        " bottleneck=" + bottleneck +
        " gpu=" + std::to_string(runtime.gpu) +
        " threads=" + std::to_string(runtime.threads) +
        " ctx=" + std::to_string(runtime.context) +
        " lang=" + knobs.language +
        " seed=" + std::to_string(knobs.seed) +
        " max_tokens=" + std::to_string(knobs.max_tokens) +
        " top_k=" + std::to_string(knobs.top_k) +
        " top_p=" + std::to_string(knobs.top_p) +
        " min_p=" + std::to_string(knobs.min_p) +
        " temp=" + std::to_string(knobs.temperature) +
        " repeat=" + std::to_string(knobs.repeat_penalty) +
        " cfg=" + std::to_string(knobs.cfg_weight) +
        " exag=" + std::to_string(knobs.exaggeration) +
        " cfm=" + std::to_string(knobs.cfm_steps) +
        " chunk=" + std::to_string(chunk_chars));
}

}
