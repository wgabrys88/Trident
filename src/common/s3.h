#pragma once
#include "gguf_file.h"
#include "../engine.h"
#include <map>

namespace trident {
class S3 {
    const VulkanBackend& backend_;
    Knobs knobs_;
    GgufFile file_;
    Weights weights_;
    bool meanflow_;
    int batches_;
    int width_, mels_, speaker_size_;
    std::unique_ptr<Graph> encoder_, time_, mixer_, estimator_;
    int encoder_frames_ = 0, estimator_frames_ = 0;
    std::vector<float> embeddings_, speaker_weight_, speaker_bias_, prompt_features_, speaker_, source_weight_;
    std::vector<int32_t> prompt_tokens_;
    float source_bias_;
    std::map<std::string, std::vector<float>> inverse_alpha_;
    ggml_tensor* weight(const std::string& name) const { return weights_.at(name); }
    ggml_tensor* conv(ggml_context*, ggml_tensor*, ggml_tensor*, int stride, int padding, int dilation = 1) const;
    ggml_tensor* pad(ggml_context*, ggml_tensor*, int front, int back) const;
    ggml_tensor* transpose(ggml_context*, ggml_tensor*) const;
    ggml_tensor* linear(ggml_context*, ggml_tensor*, const std::string&, const char* w = "/weight", const char* b = "/bias") const;
    ggml_tensor* norm(ggml_context*, ggml_tensor*, const std::string&, float epsilon = 1e-5f, const char* w = "/weight", const char* b = "/bias") const;
    ggml_tensor* convolution(ggml_context*, ggml_tensor*, const std::string&, int stride, int padding, int dilation = 1, const char* w = "/weight", const char* b = "/bias") const;
    ggml_tensor* conformer(ggml_context*, ggml_tensor*, ggml_tensor*, const std::string&, int frames) const;
    ggml_tensor* causal(ggml_context*, ggml_tensor*, const std::string&) const;
    ggml_tensor* resnet(ggml_context*, ggml_tensor*, ggml_tensor*, const std::string&) const;
    ggml_tensor* transformer(ggml_context*, ggml_tensor*, const std::string&, int frames) const;
    ggml_tensor* stack(ggml_context*, ggml_tensor*, const std::string&, int frames) const;
    void finish(Graph&, ggml_tensor*, const char* name = "out") const;
    void set(Graph&, const char*, const std::vector<float>&) const;
    std::vector<float> positions(int frames) const;
    std::vector<float> encode(const std::vector<float>&, int frames);
    std::vector<float> time(float value);
    std::vector<float> mix(const std::vector<float>&, const std::vector<float>&);
    std::vector<float> estimate(const std::vector<float>&, const std::vector<float>&,
        const std::vector<float>&, const std::vector<float>&, const std::vector<float>&, int frames);
    std::vector<float> pitch(const std::vector<float>&, int frames) const;
    std::vector<float> source(const std::vector<float>&) const;
    std::vector<float> stft(const std::vector<float>&) const;
    std::vector<float> hift(const std::vector<float>&, int frames, const std::vector<float>&, int stft_frames) const;
public:
    S3(const std::string&, const VulkanBackend&, Knobs);
    std::vector<float> synthesize(const std::vector<int32_t>&);
};
}
