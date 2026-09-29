#pragma once
#include "audio.h"
#include "gguf_file.h"

namespace trident {
class CampPlus {
    const VulkanBackend& backend_;
    GgufFile file_;
    Weights weights_;
    int features_, segment_;
    std::vector<float> conv1(const std::vector<float>&, int time, const std::string&,
        int kernel = 1, int stride = 1, int padding = 0, int dilation = 1, bool bias = false) const;
    std::vector<float> conv2(const std::vector<float>&, int height, int time, const std::string&,
        int stride = 1, int padding = 1) const;
    void norm(std::vector<float>&, int time, const std::string&, bool relu) const;
    std::vector<float> residual(const std::vector<float>&, int height, int time, const std::string&, int stride) const;
public:
    CampPlus(const std::string&, const VulkanBackend&);
    std::vector<float> embed(const Audio&) const;
};
}
