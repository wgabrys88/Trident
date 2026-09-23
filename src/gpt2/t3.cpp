#include "t3.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <iostream>
#include <numeric>

namespace trident::gpt2 {
Gpt2T3::Gpt2T3(const std::string& path, const VulkanBackend& backend, Knobs knobs)
    : backend_(backend), knobs_(knobs), file_(path), weights_(file_, backend), penalty_(knobs.repeat_penalty),
      kv_context_(nullptr, ggml_free), kv_buffer_(nullptr, ggml_backend_buffer_free),
      width_(file_.u32("chatterbox.n_embd")), heads_(file_.u32("chatterbox.n_head")),
      layers_(file_.u32("chatterbox.n_layer")), context_(int(file_.tensor("model/wpe")->ne[1])),
      vocabulary_(file_.u32("chatterbox.speech_vocab_size")), start_(file_.u32("chatterbox.start_speech_token")),
      stop_(file_.u32("chatterbox.stop_speech_token")), pad_token_(file_.u32("chatterbox.speech_pad_token")),
      pad_count_(file_.u32("chatterbox.speech_pad_count")), conditioning_(int(ggml_nelements(file_.tensor("chatterbox/builtin/cond_prompt_speech_tokens")))),
      epsilon_(file_.f32("chatterbox.layer_norm_eps")) {}
void Gpt2T3::reserve(int prompt) {
    int rows = int(std::min<int64_t>(int64_t(prompt) + knobs_.n_predict + 1, context_));
    if (rows > rows_) {
        kv_buffer_.reset();
        kv_context_.reset(ggml_init({2 * ggml_tensor_overhead(), nullptr, true}));
        int64_t elements = int64_t(width_) * layers_ * rows;
        keys_ = ggml_new_tensor_1d(kv_context_.get(), GGML_TYPE_F32, elements);
        values_ = ggml_new_tensor_1d(kv_context_.get(), GGML_TYPE_F32, elements);
        kv_buffer_.reset(ggml_backend_alloc_ctx_tensors(kv_context_.get(), backend_.get()));
        rows_ = rows;
    }
    ggml_backend_buffer_clear(kv_buffer_.get(), 0);
}
void Gpt2T3::transformer(Graph& graph, ggml_tensor* hidden, int past, int count) const {
    auto* ctx = graph.context();
    int head = width_ / heads_, length = past + count;
    size_t position_stride = size_t(head) * sizeof(float), head_stride = position_stride * rows_;
    ggml_tensor* mask = nullptr;
    if (count > 1) {
        mask = ggml_new_tensor_2d(ctx, GGML_TYPE_F16, length, count);
        ggml_set_name(mask, "mask"); ggml_set_input(mask);
    }
    for (int layer = 0; layer < layers_; ++layer) {
        std::string prefix = "model/h" + std::to_string(layer) + "/";
        auto weight = [&](const std::string& name) { return weights_.at(prefix + name); };
        auto norm = [&](ggml_tensor* x, const std::string& name) {
            return ggml_add(ctx, ggml_mul(ctx, ggml_norm(ctx, x, epsilon_), weight(name + "/g")), weight(name + "/b"));
        };
        auto linear = [&](ggml_tensor* x, const std::string& name) {
            return ggml_add(ctx, ggml_mul_mat(ctx, weight(name + "/w"), x), weight(name + "/b"));
        };
        auto* qkv = linear(norm(hidden, "ln_1"), "attn/c_attn");
        auto* query = ggml_view_3d(ctx, qkv, head, count, heads_, qkv->nb[1], position_stride, 0);
        auto* current_key = ggml_view_3d(ctx, qkv, head, count, heads_, qkv->nb[1], position_stride, size_t(width_) * sizeof(float));
        auto* current_value = ggml_view_3d(ctx, qkv, head, count, heads_, qkv->nb[1], position_stride, size_t(2 * width_) * sizeof(float));
        size_t offset = size_t(layer) * head_stride * heads_;
        auto* key_destination = ggml_view_3d(ctx, keys_, head, count, heads_, position_stride, head_stride, offset + past * position_stride);
        auto* value_destination = ggml_view_3d(ctx, values_, head, count, heads_, position_stride, head_stride, offset + past * position_stride);
        ggml_build_forward_expand(graph.graph, ggml_cpy(ctx, current_key, key_destination));
        ggml_build_forward_expand(graph.graph, ggml_cpy(ctx, current_value, value_destination));
        auto* keys = ggml_view_3d(ctx, keys_, head, length, heads_, position_stride, head_stride, offset);
        auto* values = ggml_view_3d(ctx, values_, head, length, heads_, position_stride, head_stride, offset);
        auto* attention = ggml_flash_attn_ext(ctx, ggml_cont(ctx, query), keys, values, mask, 1.f / std::sqrt(float(head)), 0.f, 0.f);
        auto* residual = ggml_add(ctx, linear(ggml_reshape_2d(ctx, attention, width_, count), "attn/c_proj"), hidden);
        auto* feedforward = ggml_gelu(ctx, linear(norm(residual, "ln_2"), "mlp/c_fc"));
        hidden = ggml_add(ctx, linear(feedforward, "mlp/c_proj"), residual);
    }
    hidden = ggml_add(ctx, ggml_mul(ctx, ggml_norm(ctx, hidden, epsilon_), weights_.at("model/ln_f/g")), weights_.at("model/ln_f/b"));
    auto* logits = ggml_add(ctx, ggml_mul_mat(ctx, weights_.at("chatterbox/speech_head"), hidden), weights_.at("chatterbox/speech_head_bias"));
    ggml_set_name(logits, "logits"); ggml_set_output(logits); ggml_build_forward_expand(graph.graph, logits);
}
std::vector<float> Gpt2T3::evaluate(const std::vector<int32_t>& text, int past, int32_t speech, bool prompt) {
    int count = prompt ? 1 + conditioning_ + int(text.size()) + 1 : 1;
    Graph graph(backend_, 8192);
    auto* ctx = graph.context();
    auto* speech_token = ggml_new_tensor_1d(ctx, GGML_TYPE_I32, 1);
    auto* position = ggml_new_tensor_1d(ctx, GGML_TYPE_I32, count);
    ggml_set_name(speech_token, "speech"); ggml_set_input(speech_token);
    ggml_set_name(position, "position"); ggml_set_input(position);
    auto* hidden = ggml_get_rows(ctx, weights_.at("chatterbox/speech_emb"), speech_token);
    if (prompt) {
        auto* text_tokens = ggml_new_tensor_1d(ctx, GGML_TYPE_I32, text.size());
        ggml_set_name(text_tokens, "text"); ggml_set_input(text_tokens);
        auto* speaker = ggml_add(ctx, ggml_mul_mat(ctx, weights_.at("chatterbox/cond_spkr/w"), weights_.at("chatterbox/builtin/speaker_emb")), weights_.at("chatterbox/cond_spkr/b"));
        auto* condition = ggml_get_rows(ctx, weights_.at("chatterbox/speech_emb"), weights_.at("chatterbox/builtin/cond_prompt_speech_tokens"));
        auto* text_embedding = ggml_get_rows(ctx, weights_.at("chatterbox/text_emb"), text_tokens);
        hidden = ggml_concat(ctx, ggml_concat(ctx, ggml_concat(ctx, speaker, condition, 1), text_embedding, 1), hidden, 1);
    }
    hidden = ggml_add(ctx, hidden, ggml_get_rows(ctx, weights_.at("model/wpe"), position));
    transformer(graph, hidden, past, count);
    graph.allocate();
    graph.set("speech", &speech, sizeof(speech));
    std::vector<int32_t> positions(count);
    std::iota(positions.begin(), positions.end(), past);
    graph.set("position", positions.data(), positions.size() * sizeof(int32_t));
    if (prompt) {
        graph.set("text", text.data(), text.size() * sizeof(int32_t));
        std::vector<ggml_fp16_t> mask(size_t(count) * count, ggml_fp32_to_fp16(0.f));
        for (int q = 0; q < count; ++q)
            for (int k = q + 1; k < count; ++k) mask[size_t(q) * count + k] = ggml_fp32_to_fp16(-INFINITY);
        graph.set("mask", mask.data(), mask.size() * sizeof(ggml_fp16_t));
    }
    graph.compute();
    std::vector<float> logits(vocabulary_);
    ggml_backend_tensor_get(graph.tensor("logits"), logits.data(), size_t(vocabulary_) * (count - 1) * sizeof(float), logits.size() * sizeof(float));
    return logits;
}
int32_t Gpt2T3::sample(const std::vector<float>& logits, const std::vector<int32_t>& generated, std::mt19937& rng) const {
    auto scores = logits;
    if (knobs_.temperature > 0.f && knobs_.temperature != 1.f) {
        float inverse = 1.f / knobs_.temperature;
        for (auto& score : scores) score *= inverse;
    }
    if (knobs_.top_k > 0 && knobs_.top_k < int(scores.size())) {
        auto sorted = scores;
        std::nth_element(sorted.begin(), sorted.begin() + knobs_.top_k - 1, sorted.end(), std::greater<float>());
        for (auto& score : scores) if (score < sorted[knobs_.top_k - 1]) score = -INFINITY;
    }
    if (knobs_.top_p < 1.f) {
        std::vector<int> sorted;
        for (int i = 0; i < int(scores.size()); ++i) if (scores[i] != -INFINITY) sorted.push_back(i);
        std::sort(sorted.begin(), sorted.end(), [&](int a, int b) { return scores[a] < scores[b]; });
        float maximum = scores[sorted.back()], sum = 0, cumulative = 0;
        std::vector<float> probabilities(sorted.size());
        for (size_t i = 0; i < sorted.size(); ++i) { probabilities[i] = std::exp(scores[sorted[i]] - maximum); sum += probabilities[i]; }
        for (auto& probability : probabilities) probability /= sum;
        for (size_t i = 0; i + 1 < sorted.size(); ++i) {
            cumulative += probabilities[i];
            if (cumulative <= 1.f - knobs_.top_p) scores[sorted[i]] = -INFINITY;
        }
    }
    penalty_.apply(scores, generated);
    float maximum = -INFINITY, sum = 0;
    for (float score : scores) if (score != -INFINITY) maximum = std::max(maximum, score);
    for (auto& score : scores) { score = score == -INFINITY ? 0.f : std::exp(score - maximum); sum += score; }
    for (auto& score : scores) score /= sum;
    return std::discrete_distribution<int32_t>(scores.begin(), scores.end())(rng);
}
std::vector<int32_t> Gpt2T3::generate(const std::vector<int32_t>& text) {
    int past = 1 + conditioning_ + int(text.size()) + 1;
    reserve(past);
    std::mt19937 rng(knobs_.seed);
    auto clock = std::chrono::steady_clock::now();
    auto logits = evaluate(text, 0, start_, true);
    int32_t token = sample(logits, {start_}, rng);
    std::vector<int32_t> predicted{token};
    int steps = 1;
    for (int step = 1; step < knobs_.n_predict && token != stop_ && past + 1 <= context_; ++step) {
        logits = evaluate({}, past++, token, false);
        token = sample(logits, predicted, rng); predicted.push_back(token);
        steps++;
    }
    double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - clock).count();
    std::cerr << (ms / steps) << " ms/token\n";
    std::vector<int32_t> speech;
    for (int32_t value : predicted) if (value >= 0 && value < start_) speech.push_back(value);
    speech.insert(speech.end(), pad_count_, pad_token_);
    return speech;
}
}
