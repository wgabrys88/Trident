#pragma once
#include "common/gguf_file.h"
#include "../engine.h"
#include "common/repeat_penalty.h"
#include <random>

namespace trident::gpt2 {
class Gpt2T3 {
    const VulkanBackend& backend_;
    Knobs knobs_;
    GgufFile file_;
    Weights weights_;
    RepeatPenalty penalty_;
    Context kv_context_;
    Buffer kv_buffer_;
    ggml_tensor *keys_ = nullptr, *values_ = nullptr;
    int width_, heads_, layers_, context_, vocabulary_, start_, stop_, pad_token_, pad_count_, conditioning_, rows_ = 0;
    float epsilon_;
    void reserve(int prompt);
    void transformer(Graph&, ggml_tensor*, int past, int count) const;
    std::vector<float> evaluate(const std::vector<int32_t>& text, int past, int32_t speech, bool prompt);
    int32_t sample(const std::vector<float>&, const std::vector<int32_t>&, std::mt19937&) const;
public:
    Gpt2T3(const std::string&, const VulkanBackend&, Knobs);
    std::vector<std::string> vocabulary() const { return file_.strings("tokenizer.ggml.tokens"); }
    std::vector<std::string> merges() const { return file_.strings("tokenizer.ggml.merges"); }
    std::vector<int32_t> types() const { return file_.ints("tokenizer.ggml.token_type"); }
    std::vector<int32_t> generate(const std::vector<int32_t>&);
};
}
