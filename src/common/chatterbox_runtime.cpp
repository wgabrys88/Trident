#include "chatterbox_runtime.h"
#include "gguf_file.h"
#include "pipe.h"
#include "../gpt2/engine.h"
#include "../llama/engine.h"
#include <algorithm>
#include <fstream>
#include <stdexcept>
#include <vector>
#include <windows.h>

namespace trident {
namespace {

std::filesystem::path exe_dir() {
    wchar_t buf[MAX_PATH];
    DWORD n = GetModuleFileNameW(nullptr, buf, MAX_PATH);
    return std::filesystem::path(buf, buf + n).parent_path();
}

std::vector<char*> argv_with_flags(const std::string& t3, const std::string& s3, const std::vector<std::string>& flags) {
    static thread_local std::vector<std::string> storage;
    static thread_local std::vector<char*> ptrs;
    storage.clear();
    ptrs.clear();
    storage.push_back("chatterbox");
    storage.push_back(t3);
    storage.push_back(s3);
    storage.push_back("-");
    for (const auto& f : flags) storage.push_back(f);
    for (auto& s : storage) ptrs.push_back(s.data());
    return ptrs;
}

std::vector<std::string> gpt2_flags(int gpu) {
    return {"--seed",       "42",
            "--temperature", "0.8",
            "--top-k",       "1000",
            "--top-p",       "0.95",
            "--repeat-penalty", "1.2",
            "--n-predict",   "1000",
            "--cfm-steps",   "2",
            "--trim-fade-samples", "480",
            "--gpu",         std::to_string(gpu)};
}

std::vector<std::string> v3_flags(int gpu) {
    return {"--seed",           "42",
            "--temperature",    "0.8",
            "--top-p",          "1.0",
            "--repeat-penalty", "1.2",
            "--n-predict",      "1000",
            "--cfm-steps",      "10",
            "--trim-fade-samples", "480",
            "--min-p",          "0.05",
            "--cfg-weight",     "0.5",
            "--exaggeration",   "0.5",
            "--cfm-cfg",        "0.7",
            "--gpu",            std::to_string(gpu)};
}

} // namespace

std::filesystem::path chatterbox_repo_root() { return std::filesystem::weakly_canonical(exe_dir() / ".." / ".."); }

VoiceBundle chatterbox_voice_bundle(const std::string& variant) {
    if (variant != "nano" && variant != "turbo" && variant != "v3")
        throw std::runtime_error("variant must be nano, turbo, or v3");
    auto dir = chatterbox_repo_root() / "models" / "voices" / variant;
    VoiceBundle b{dir / "t3.gguf", dir / "s3.gguf"};
    for (const auto& p : {b.t3, b.s3})
        if (!std::filesystem::is_regular_file(p)) throw std::runtime_error("missing " + p.string());
    return b;
}

void apply_overrides(std::vector<std::string>& flags, const std::vector<std::pair<std::string, std::string>>& overrides) {
    for (const auto& [name, value] : overrides) {
        bool found = false;
        for (size_t i = 0; i + 1 < flags.size(); i += 2) {
            if (flags[i] == name) {
                flags[i + 1] = value;
                found = true;
                break;
            }
        }
        if (!found) throw std::runtime_error("unknown flag for this architecture: " + name);
    }
}

std::unique_ptr<Synth> chatterbox_make_engine_paths(const std::filesystem::path& t3, const std::filesystem::path& s3,
                                                    int gpu,
                                                    const std::vector<std::pair<std::string, std::string>>& overrides) {
    if (!std::filesystem::is_regular_file(t3) || !std::filesystem::is_regular_file(s3))
        throw std::runtime_error("missing t3 or s3 gguf");
    auto family = GgufFile::architecture(t3.string());
    auto flags_vec = (family == "chatterbox-llama") ? v3_flags(gpu) : gpt2_flags(gpu);
    apply_overrides(flags_vec, overrides);
    auto ptrs = argv_with_flags(t3.string(), s3.string(), flags_vec);
    Flags flags(int(ptrs.size()), ptrs.data());
    std::unique_ptr<Synth> engine;
    if (family == "chatterbox-gpt2")
        engine = std::make_unique<gpt2::Engine>(t3.string(), s3.string(), flags);
    else if (family == "chatterbox-llama")
        engine = std::make_unique<llama::Engine>(t3.string(), s3.string(), flags);
    else
        throw std::runtime_error("unsupported architecture: " + family);
    flags.finish();
    return engine;
}

std::unique_ptr<Synth> chatterbox_make_engine(const std::string& variant, int gpu,
                                            const std::vector<std::pair<std::string, std::string>>& overrides) {
    auto bundle = chatterbox_voice_bundle(variant);
    return chatterbox_make_engine_paths(bundle.t3, bundle.s3, gpu, overrides);
}

void chatterbox_write_wav(const std::filesystem::path& path, const std::vector<float>& pcm, int sample_rate) {
    std::filesystem::create_directories(path.parent_path());
    std::vector<int16_t> samples(pcm.size());
    for (size_t i = 0; i < pcm.size(); ++i)
        samples[i] = int16_t(std::clamp(pcm[i], -1.f, 1.f) * 32767.f);
    std::ofstream out(path, std::ios::binary);
    if (!out) throw std::runtime_error("cannot open " + path.string());
    const uint32_t data_size = uint32_t(samples.size() * sizeof(int16_t));
    const uint32_t riff_size = 36 + data_size;
    out.write("RIFF", 4);
    out.write(reinterpret_cast<const char*>(&riff_size), 4);
    out.write("WAVEfmt ", 8);
    uint32_t fmt_size = 16;
    uint16_t audio_format = 1;
    uint16_t channels = 1;
    uint32_t rate = uint32_t(sample_rate);
    uint16_t bits = 16;
    uint32_t byte_rate = rate * channels * bits / 8;
    uint16_t block_align = channels * bits / 8;
    out.write(reinterpret_cast<const char*>(&fmt_size), 4);
    out.write(reinterpret_cast<const char*>(&audio_format), 2);
    out.write(reinterpret_cast<const char*>(&channels), 2);
    out.write(reinterpret_cast<const char*>(&rate), 4);
    out.write(reinterpret_cast<const char*>(&byte_rate), 4);
    out.write(reinterpret_cast<const char*>(&block_align), 2);
    out.write(reinterpret_cast<const char*>(&bits), 2);
    out.write("data", 4);
    out.write(reinterpret_cast<const char*>(&data_size), 4);
    out.write(reinterpret_cast<const char*>(samples.data()), data_size);
}

} // namespace trident
