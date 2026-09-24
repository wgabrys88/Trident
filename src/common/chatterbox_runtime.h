#pragma once
#include "../engine.h"
#include <filesystem>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace trident {

// Repo root: build/bin -> ../../
std::filesystem::path chatterbox_repo_root();

struct VoiceBundle {
    std::filesystem::path t3;
    std::filesystem::path s3;
};

VoiceBundle chatterbox_voice_bundle(const std::string& variant);

std::unique_ptr<Synth> chatterbox_make_engine(const std::string& variant, int gpu,
    const std::vector<std::pair<std::string, std::string>>& overrides = {});

std::unique_ptr<Synth> chatterbox_make_engine_paths(const std::filesystem::path& t3, const std::filesystem::path& s3,
                                                    int gpu,
    const std::vector<std::pair<std::string, std::string>>& overrides = {});

void chatterbox_write_wav(const std::filesystem::path& path, const std::vector<float>& pcm, int sample_rate = 24000);

std::vector<float> chatterbox_synthesize(const std::string& variant, int gpu, const std::string& text,
                                         const std::string& language);

} // namespace trident
