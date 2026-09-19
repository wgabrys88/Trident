#pragma once
#include "audio.h"
#include "gguf_file.h"

namespace trident {
class VoiceEncoder {
    const VulkanBackend& backend_;
    GgufFile file_;
    Weights weights_;
    int layers_, mels_, hidden_, embedding_, partial_, rate_;
    float windows_per_second_, coverage_;
    std::vector<float> filters_;
public:
    VoiceEncoder(const std::string& path, const VulkanBackend& backend);
    std::vector<float> embed(const Audio& audio) const;
};
}
