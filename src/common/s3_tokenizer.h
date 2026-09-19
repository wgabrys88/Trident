#pragma once
#include "audio.h"
#include "gguf_file.h"

namespace trident {
class S3Tokenizer {
    const VulkanBackend& backend_;
    GgufFile file_;
    Weights weights_;
    int mels_, width_, heads_, layers_, head_dim_, kernel_, stride_, fft_, hop_, dimensions_, levels_, max_position_;
    float theta_;
    ggml_tensor* convolution(ggml_context*, ggml_tensor*, ggml_tensor*, int, int) const;
    ggml_tensor* encoder(Graph&, int frames, ggml_tensor* positions) const;
public:
    S3Tokenizer(const std::string&, const VulkanBackend&);
    std::vector<int32_t> tokenize(const Audio&, int max_tokens) const;
};
}
