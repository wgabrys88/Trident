#include "cli.hpp"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <limits>
#include <stdexcept>

namespace tts {

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

static bool valid_utf8(const std::string& text) {
    size_t i = 0;
    while (i < text.size()) {
        const unsigned char c = static_cast<unsigned char>(text[i]);
        if (c <= 0x7f) { ++i; continue; }
        int extra = 0;
        uint32_t code = 0;
        if ((c & 0xe0) == 0xc0) { extra = 1; code = c & 0x1f; if (code < 2) return false; }
        else if ((c & 0xf0) == 0xe0) { extra = 2; code = c & 0x0f; }
        else if ((c & 0xf8) == 0xf0) { extra = 3; code = c & 0x07; }
        else return false;
        if (i + extra >= text.size()) return false;
        for (int j = 1; j <= extra; ++j) {
            const unsigned char next = static_cast<unsigned char>(text[i + j]);
            if ((next & 0xc0) != 0x80) return false;
            code = (code << 6) | (next & 0x3f);
        }
        if ((extra == 2 && code < 0x800) || (extra == 3 && code < 0x10000) || code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff))
            return false;
        i += static_cast<size_t>(extra + 1);
    }
    return true;
}

void log(const std::string& line) {
    std::cerr << "tts " << line << std::endl;
}

std::string read_text(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("cannot open text file: " + path);
    std::string text((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    const bool bom = text.size() >= 3 && static_cast<unsigned char>(text[0]) == 0xef &&
                     static_cast<unsigned char>(text[1]) == 0xbb && static_cast<unsigned char>(text[2]) == 0xbf;
    if (bom) text.erase(0, 3);
    if (!valid_utf8(text)) throw std::runtime_error("text file is not valid UTF-8: " + path);
    if (std::all_of(text.begin(), text.end(), [](unsigned char c) { return std::isspace(c) != 0; }))
        throw std::runtime_error("text file is empty: " + path);
    int chars = 0;
    for (unsigned char c : text)
        if ((c & 0xc0) != 0x80) ++chars;
    log("text path=" + path + " bytes=" + std::to_string(text.size()) + " chars=" + std::to_string(chars) +
        " bom=" + (bom ? "1" : "0"));
    return text;
}

void require_file(const Args& args, const std::string& key) {
    if (!std::filesystem::is_regular_file(args.at(key))) throw std::invalid_argument("file not found for " + key + ": " + args.at(key));
}

Runtime runtime_from(const Args& args) {
    Runtime runtime;
    runtime.t3 = args.at("--model");
    runtime.s3 = args.at("--s3gen-gguf");
    runtime.gpu = parse_int(args, "--n-gpu-layers");
    runtime.context = parse_int(args, "--context");
    runtime.threads = parse_int(args, "--threads");
    if (runtime.gpu < 0 || runtime.context < 1 || runtime.threads < 1)
        throw std::invalid_argument("integer runtime values are out of range");
    return runtime;
}

void print_done(const Speech& speech, double total_ms, const Runtime& runtime, const EngineKnobs& knobs, int chunk_chars) {
    const double audio_ms = speech.pcm.size() / 24.0;
    const double rtf = audio_ms > 0 ? (speech.t3_ms + speech.s3gen_ms) / audio_ms : 0;
    std::cerr << "tts done samples=" << speech.pcm.size()
              << " seconds=" << speech.pcm.size() / 24000.0 << " chunks=" << speech.chunks
              << " t3_tokens=" << speech.t3_tokens
              << " total_ms=" << total_ms << " t3_ms=" << speech.t3_ms << " s3gen_ms=" << speech.s3gen_ms
              << " rtf=" << rtf
              << " gpu=" << runtime.gpu << " threads=" << runtime.threads << " ctx=" << runtime.context
              << " lang=" << knobs.language
              << " seed=" << knobs.seed << " max_tokens=" << knobs.max_tokens << " top_k=" << knobs.top_k
              << " top_p=" << knobs.top_p << " min_p=" << knobs.min_p << " temp=" << knobs.temperature
              << " repeat=" << knobs.repeat_penalty << " cfg=" << knobs.cfg_weight << " exag=" << knobs.exaggeration
              << " cfm=" << knobs.cfm_steps << " chunk=" << chunk_chars << std::endl;
}

}
