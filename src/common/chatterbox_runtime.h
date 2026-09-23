#pragma once
#include "../engine.h"
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

namespace trident {

// Repo root: build/bin -> ../../
std::filesystem::path chatterbox_repo_root();

struct VoiceBundle {
    std::filesystem::path t3;
    std::filesystem::path s3;
};

VoiceBundle chatterbox_voice_bundle(const std::string& variant);

std::unique_ptr<Synth> chatterbox_make_engine(const std::string& variant, int gpu);

std::unique_ptr<Synth> chatterbox_make_engine_paths(const std::filesystem::path& t3, const std::filesystem::path& s3,
                                                    int gpu);

void chatterbox_write_wav(const std::filesystem::path& path, const std::vector<float>& pcm, int sample_rate = 24000);

std::vector<float> chatterbox_synthesize(const std::string& variant, int gpu, const std::string& text,
                                         const std::string& language);

} // namespace trident
