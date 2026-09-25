#include "chatterbox_runtime.h"
#include "config.h"
#include "gguf_file.h"
#include "../gpt2/engine.h"
#include "../llama/engine.h"
#include <algorithm>
#include <fstream>
#include <stdexcept>
#include <vector>

namespace trident {

std::unique_ptr<Synth> chatterbox_make_engine(const std::filesystem::path& t3, const std::filesystem::path& s3, const Knobs& knobs) {
    const auto t3_path = path_u8(t3);
    const auto s3_path = path_u8(s3);
    if (!std::filesystem::is_regular_file(t3) || !std::filesystem::is_regular_file(s3))
        throw std::runtime_error("missing " + t3_path + " or " + s3_path);
    auto family = GgufFile::architecture(t3_path);
    if (family == "chatterbox-gpt2") return std::make_unique<gpt2::Engine>(t3_path, s3_path, knobs);
    if (family == "chatterbox-llama") return std::make_unique<llama::Engine>(t3_path, s3_path, knobs);
    throw std::runtime_error("unsupported architecture: " + family);
}

void chatterbox_write_wav(const std::filesystem::path& path, const std::vector<float>& pcm, int sample_rate) {
    std::filesystem::create_directories(path.parent_path());
    std::vector<int16_t> samples(pcm.size());
    for (size_t i = 0; i < pcm.size(); ++i)
        samples[i] = int16_t(std::clamp(pcm[i], -1.f, 1.f) * 32767.f);
    std::ofstream out(path, std::ios::binary);
    if (!out) throw std::runtime_error("cannot open " + path.u8string());
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
