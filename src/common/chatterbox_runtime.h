#pragma once
#include "../engine.h"
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

namespace trident {

std::unique_ptr<Synth> chatterbox_make_engine(const std::filesystem::path& t3, const std::filesystem::path& s3, const Knobs& knobs);

void chatterbox_write_wav(const std::filesystem::path& path, const std::vector<float>& pcm, int sample_rate);

} // namespace trident
