#pragma once
#include "common/gguf_file.h"
#include "trident/knobs.h"
#include "trident/stats.h"
#include "common/repeat_penalty.h"
#include <random>

namespace trident::llama {
class LlamaT3 {
    static constexpr int batches_ = 2;
    const VulkanBackend& backend_;
    Knobs knobs_;
    GgufFile file_;
    Weights weights_;
    RepeatPenalty penalty_;
    Context kv_context_;
    Buffer kv_buffer_;
    ggml_tensor *keys_ = nullptr, *values_ = nullptr;
    int width_, heads_, layers_, context_, vocabulary_, start_, stop_, start_text_, stop_text_;
    int conditioning_, perceiver_, original_, rows_ = 0;
    float epsilon_, theta_;
    ggml_tensor* weight(const std::string& name) const { return weights_.at(name); }
    ggml_tensor* linear(ggml_context*, ggml_tensor*, ggml_tensor*, ggml_tensor* = nullptr) const;
    ggml_tensor* rms(ggml_context*, ggml_tensor*, ggml_tensor*) const;
    ggml_tensor* perceiver(ggml_context*, ggml_tensor*, ggml_tensor*) const;
    ggml_tensor* attend(ggml_context*, ggml_tensor*) const;
    ggml_tensor* repeat(ggml_context*, ggml_tensor*, int) const;
    void reserve(int prompt);
    void transformer(Graph&, ggml_tensor*, int past, int count) const;
    std::vector<float> logits(Graph&, int count) const;
    std::vector<float> prompt(const std::vector<int32_t>&);
    std::vector<float> step(int past, int32_t token, int speech);
    int32_t sample(const std::vector<float>&, const std::vector<int32_t>&, std::mt19937&) const;
public:
    LlamaT3(const std::string&, const VulkanBackend&, Knobs);
    int32_t start_text() const { return start_text_; }
    int32_t stop_text() const { return stop_text_; }
    std::vector<int32_t> generate(const std::vector<int32_t>&, SynthesizeStats&);
};
}
