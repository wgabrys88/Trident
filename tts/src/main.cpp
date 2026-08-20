#include "engine_wrapper.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <cctype>
#include <iterator>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

namespace {

using Args = std::unordered_map<std::string, std::string>;

const std::vector<std::string> kRequired = {
    "--model", "--s3gen-gguf", "--reference", "--text-file", "--output", "--language",
    "--n-gpu-layers", "--context", "--threads", "--seed", "--max-tokens", "--top-k",
    "--top-p", "--min-p", "--temperature", "--repeat-penalty", "--cfg-weight",
    "--exaggeration", "--cfm-steps", "--chunk-chars",
};

Args parse_args(int argc, char** argv) {
    Args args;
    for (int i = 1; i < argc; ++i) {
        const std::string key = argv[i];
        if (std::find(kRequired.begin(), kRequired.end(), key) == kRequired.end())
            throw std::invalid_argument("unknown argument: " + key);
        if (i + 1 >= argc) throw std::invalid_argument("missing value for " + key);
        if (!args.emplace(key, argv[++i]).second) throw std::invalid_argument("duplicate argument: " + key);
    }
    for (const auto& key : kRequired)
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

bool valid_utf8(const std::string& text) {
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

std::string read_text(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("cannot open text file: " + path);
    std::string text((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    if (!valid_utf8(text)) throw std::runtime_error("text file is not valid UTF-8: " + path);
    if (std::all_of(text.begin(), text.end(), [](unsigned char c) { return std::isspace(c) != 0; }))
        throw std::runtime_error("text file is empty: " + path);
    return text;
}

void write_wav(const std::string& path, const std::vector<float>& pcm) {
    if (pcm.empty()) throw std::runtime_error("cannot write empty WAV");
    if (pcm.size() > (std::numeric_limits<uint32_t>::max() - 36u) / 2u)
        throw std::runtime_error("WAV output is too large");
    std::vector<int16_t> samples(pcm.size());
    for (size_t i = 0; i < pcm.size(); ++i) {
        const float clipped = std::max(-1.0f, std::min(1.0f, pcm[i]));
        samples[i] = static_cast<int16_t>(clipped * 32767.0f);
    }
    const std::filesystem::path target(path);
    if (!target.parent_path().empty()) std::filesystem::create_directories(target.parent_path());
    std::ofstream out(target, std::ios::binary | std::ios::trunc);
    if (!out) throw std::runtime_error("cannot open WAV output: " + path);
    const uint32_t rate = 24000, data_size = static_cast<uint32_t>(samples.size() * 2u);
    const uint32_t riff_size = 36u + data_size, byte_rate = rate * 2u, fmt_size = 16u;
    const uint16_t format = 1u, channels = 1u, block_align = 2u, bits = 16u;
    auto put = [&](const auto& value) { out.write(reinterpret_cast<const char*>(&value), sizeof(value)); };
    out.write("RIFF", 4); put(riff_size); out.write("WAVEfmt ", 8);
    put(fmt_size); put(format); put(channels); put(rate); put(byte_rate); put(block_align); put(bits);
    out.write("data", 4); put(data_size);
    out.write(reinterpret_cast<const char*>(samples.data()), static_cast<std::streamsize>(data_size));
    if (!out) throw std::runtime_error("failed while writing WAV output: " + path);
}

void require_file(const Args& args, const std::string& key) {
    if (!std::filesystem::is_regular_file(args.at(key))) throw std::invalid_argument("file not found for " + key + ": " + args.at(key));
}

void validate_values(int gpu, int context, int threads, const tts::Voice& voice) {
    if (gpu < 0 || context < 1 || threads < 1 || voice.max_tokens < 1 || voice.top_k < 0 || voice.cfm_steps < 1 || voice.chunk_chars < 1)
        throw std::invalid_argument("integer runtime values are out of range");
    if (voice.top_p < 0 || voice.top_p > 1 || voice.min_p < 0 || voice.min_p > 1 || voice.temperature < 0 ||
        voice.repeat_penalty <= 0 || voice.cfg_weight < 0 || voice.exaggeration < 0)
        throw std::invalid_argument("sampling or voice values are out of range");
}

} // namespace

int main(int argc, char** argv) {
    try {
        const Args args = parse_args(argc, argv);
        for (const auto& key : {"--model", "--s3gen-gguf", "--reference", "--text-file"}) require_file(args, key);
        const std::string text = read_text(args.at("--text-file"));
        const int gpu = parse_int(args, "--n-gpu-layers");
        const int context = parse_int(args, "--context");
        const int threads = parse_int(args, "--threads");
        tts::Voice voice;
        voice.reference = args.at("--reference");
        voice.language = args.at("--language");
        voice.seed = parse_int(args, "--seed");
        voice.max_tokens = parse_int(args, "--max-tokens");
        voice.top_k = parse_int(args, "--top-k");
        voice.top_p = parse_float(args, "--top-p");
        voice.min_p = parse_float(args, "--min-p");
        voice.temperature = parse_float(args, "--temperature");
        voice.repeat_penalty = parse_float(args, "--repeat-penalty");
        voice.cfg_weight = parse_float(args, "--cfg-weight");
        voice.exaggeration = parse_float(args, "--exaggeration");
        voice.cfm_steps = parse_int(args, "--cfm-steps");
        voice.chunk_chars = parse_int(args, "--chunk-chars");
        validate_values(gpu, context, threads, voice);

        const auto started = std::chrono::steady_clock::now();
        tts::EngineWrapper engine(args.at("--model"), args.at("--s3gen-gguf"), gpu, threads, context);
        const tts::Speech speech = engine.synthesize(voice, text);
        write_wav(args.at("--output"), speech.pcm);
        const double total_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
        std::cerr << "tts done samples=" << speech.pcm.size()
                  << " seconds=" << speech.pcm.size() / 24000.0 << " chunks=" << speech.chunks
                  << " total_ms=" << total_ms << " t3_ms=" << speech.t3_ms << " s3gen_ms=" << speech.s3gen_ms
                  << " gpu=" << gpu << " threads=" << threads << " ctx=" << context << " lang=" << voice.language
                  << " seed=" << voice.seed << " max_tokens=" << voice.max_tokens << " top_k=" << voice.top_k
                  << " top_p=" << voice.top_p << " min_p=" << voice.min_p << " temp=" << voice.temperature
                  << " repeat=" << voice.repeat_penalty << " cfg=" << voice.cfg_weight << " exag=" << voice.exaggeration
                  << " cfm=" << voice.cfm_steps << " chunk=" << voice.chunk_chars << std::endl;
        return 0;
    } catch (const std::invalid_argument& error) {
        std::cerr << "argument error: " << error.what() << std::endl;
        return 2;
    } catch (const std::exception& error) {
        std::cerr << "tts error: " << error.what() << std::endl;
        return 1;
    }
}
