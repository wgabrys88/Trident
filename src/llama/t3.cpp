#include "t3.h"
#include <algorithm>
#include <cmath>
#include <numeric>
#include <set>

namespace trident::llama {
LlamaT3::LlamaT3(const std::string& path, const VulkanBackend& backend, Knobs knobs)
    : backend_(backend), knobs_(knobs), file_(path), weights_(file_, backend), penalty_(knobs.repeat_penalty),
      kv_context_(nullptr, ggml_free), kv_buffer_(nullptr, ggml_backend_buffer_free),
      width_(file_.u32("chatterbox.n_embd")), heads_(file_.u32("chatterbox.n_embd") / (2 * int(file_.tensor("model/rope_freq_factors")->ne[0]))),
      layers_(file_.u32("chatterbox.n_layer")), context_(1 + file_.u32("chatterbox.perceiver_len") + 1 + file_.u32("chatterbox.text_positions") + 2 + knobs.n_predict),
      vocabulary_(file_.u32("chatterbox.speech_vocab_size")), start_(file_.u32("chatterbox.start_speech_token")),
      stop_(file_.u32("chatterbox.stop_speech_token")), start_text_(file_.u32("chatterbox.start_text_token")),
      stop_text_(file_.u32("chatterbox.stop_text_token")), conditioning_(int(ggml_nelements(file_.tensor("chatterbox/builtin/cond_prompt_speech_tokens")))),
      perceiver_(file_.u32("chatterbox.perceiver_len")), original_(file_.u32("chatterbox.rope_orig_ctx")),
      epsilon_(file_.f32("chatterbox.layer_norm_eps")), theta_(file_.f32("chatterbox.rope_theta")) {}
ggml_tensor* LlamaT3::linear(ggml_context* ctx, ggml_tensor* matrix, ggml_tensor* x, ggml_tensor* bias) const {
    auto* y = ggml_mul_mat(ctx, matrix, x);
    return bias ? ggml_add(ctx, y, bias) : y;
}
ggml_tensor* LlamaT3::rms(ggml_context* ctx, ggml_tensor* x, ggml_tensor* scale) const {
    return ggml_mul(ctx, ggml_rms_norm(ctx, x, epsilon_), scale);
}
ggml_tensor* LlamaT3::perceiver(ggml_context* ctx, ggml_tensor* query, ggml_tensor* memory) const {
    int embedding = int(query->ne[0]), queries = int(query->ne[1]), keys = int(memory->ne[1]), heads = file_.u32("chatterbox.perceiver_heads"), head = embedding / heads;
    auto* gamma = weight("chatterbox/perceiver/attn/norm/g");
    auto* beta = weight("chatterbox/perceiver/attn/norm/b");
    auto normalize = [&](ggml_tensor* x) {
        return ggml_add(ctx, ggml_mul(ctx, ggml_norm(ctx, x, 1e-5f), gamma), beta);
    };
    auto project = [&](ggml_tensor* x, const char* name, int tokens) {
        auto* value = linear(ctx, weight(std::string("chatterbox/perceiver/attn/") + name + "/w"), x,
            weight(std::string("chatterbox/perceiver/attn/") + name + "/b"));
        return ggml_cont(ctx, ggml_permute(ctx, ggml_reshape_3d(ctx, value, head, heads, tokens), 0, 2, 1, 3));
    };
    auto* q = project(normalize(query), "to_q", queries);
    auto* k = project(normalize(memory), "to_k", keys);
    auto* v = project(normalize(memory), "to_v", keys);
    auto* attention = ggml_flash_attn_ext(ctx, q, k, v, nullptr, 1.f / std::sqrt(float(head)), 0.f, 0.f);
    return ggml_add(ctx, query, linear(ctx, weight("chatterbox/perceiver/attn/proj_out/w"),
        ggml_reshape_2d(ctx, attention, embedding, queries), weight("chatterbox/perceiver/attn/proj_out/b")));
}
ggml_tensor* LlamaT3::attend(ggml_context* ctx, ggml_tensor* hidden) const {
    auto* query = ggml_reshape_2d(ctx, weight("chatterbox/perceiver/pre_attention_query"), width_, perceiver_);
    auto* pre = perceiver(ctx, query, hidden);
    return perceiver(ctx, pre, pre);
}
ggml_tensor* LlamaT3::repeat(ggml_context* ctx, ggml_tensor* x, int tokens) const {
    return ggml_repeat(ctx, ggml_reshape_3d(ctx, x, width_, tokens, 1),
        ggml_new_tensor_3d(ctx, GGML_TYPE_F32, width_, tokens, batches_));
}
void LlamaT3::reserve(int prompt) {
    int rows = int(std::min<int64_t>(int64_t(prompt) + knobs_.n_predict + 1, context_));
    if (rows > rows_) {
        kv_buffer_.reset();
        kv_context_.reset(ggml_init({2 * ggml_tensor_overhead(), nullptr, true}));
        int64_t elements = int64_t(width_) * layers_ * rows * batches_;
        keys_ = ggml_new_tensor_1d(kv_context_.get(), GGML_TYPE_F32, elements);
        values_ = ggml_new_tensor_1d(kv_context_.get(), GGML_TYPE_F32, elements);
        kv_buffer_.reset(ggml_backend_alloc_ctx_tensors(kv_context_.get(), backend_.get()));
        rows_ = rows;
    }
    ggml_backend_buffer_clear(kv_buffer_.get(), 0);
}
void LlamaT3::transformer(Graph& graph, ggml_tensor* hidden, int past, int count) const {
    auto* ctx = graph.context();
    int head = width_ / heads_, length = past + count;
    size_t position_stride = size_t(head) * sizeof(float);
    size_t head_stride = position_stride * rows_;
    size_t batch_stride = head_stride * heads_;
    size_t layer_bytes = batch_stride * batches_;
    ggml_tensor* mask = nullptr;
    if (count > 1) {
        mask = ggml_new_tensor_2d(ctx, GGML_TYPE_F16, length, count);
        ggml_set_name(mask, "mask"); ggml_set_input(mask);
    }
    auto* position = ggml_new_tensor_1d(ctx, GGML_TYPE_I32, count);
    ggml_set_name(position, "position"); ggml_set_input(position);
    auto* factors = weight("model/rope_freq_factors");
    for (int layer = 0; layer < layers_; ++layer) {
        std::string prefix = "model/h" + std::to_string(layer);
        auto matrix = [&](const char* name) { return weight(prefix + name); };
        auto rope = [&](ggml_tensor* x) {
            return ggml_rope_ext(ctx, x, position, factors, head, GGML_ROPE_TYPE_NEOX,
                original_, theta_, 1.f, 0.f, 1.f, 0.f, 0.f);
        };
        auto* current = rms(ctx, hidden, matrix("/attn_norm/g"));
        auto* q = ggml_reshape_4d(ctx, linear(ctx, matrix("/attn/q/w"), current), head, heads_, count, batches_);
        auto* k = ggml_reshape_4d(ctx, linear(ctx, matrix("/attn/k/w"), current), head, heads_, count, batches_);
        auto* v = ggml_reshape_4d(ctx, linear(ctx, matrix("/attn/v/w"), current), head, heads_, count, batches_);
        q = ggml_cont(ctx, ggml_permute(ctx, rope(q), 0, 2, 1, 3));
        k = ggml_cont(ctx, ggml_permute(ctx, rope(k), 0, 2, 1, 3));
        v = ggml_cont(ctx, ggml_permute(ctx, v, 0, 2, 1, 3));
        size_t offset = size_t(layer) * layer_bytes + size_t(past) * position_stride;
        auto* key_destination = ggml_view_4d(ctx, keys_, head, count, heads_, batches_,
            position_stride, head_stride, batch_stride, offset);
        auto* value_destination = ggml_view_4d(ctx, values_, head, count, heads_, batches_,
            position_stride, head_stride, batch_stride, offset);
        ggml_build_forward_expand(graph.graph, ggml_cpy(ctx, k, key_destination));
        ggml_build_forward_expand(graph.graph, ggml_cpy(ctx, v, value_destination));
        size_t layer_offset = size_t(layer) * layer_bytes;
        auto* keys = ggml_view_4d(ctx, keys_, head, length, heads_, batches_,
            position_stride, head_stride, batch_stride, layer_offset);
        auto* values = ggml_view_4d(ctx, values_, head, length, heads_, batches_,
            position_stride, head_stride, batch_stride, layer_offset);
        auto* attention = ggml_flash_attn_ext(ctx, q, keys, values, mask, 1.f / std::sqrt(float(head)), 0.f, 0.f);
        auto* residual = ggml_add(ctx, linear(ctx, matrix("/attn/o/w"), ggml_reshape_3d(ctx, attention, width_, count, batches_)), hidden);
        current = rms(ctx, residual, matrix("/ffn_norm/g"));
        hidden = ggml_add(ctx, linear(ctx, matrix("/ffn/down/w"),
            ggml_swiglu_split(ctx, linear(ctx, matrix("/ffn/gate/w"), current), linear(ctx, matrix("/ffn/up/w"), current))), residual);
    }
    auto* output = ggml_mul_mat(ctx, weight("chatterbox/speech_head"), rms(ctx, hidden, weight("model/norm/g")));
    ggml_set_name(output, "logits"); ggml_set_output(output); ggml_build_forward_expand(graph.graph, output);
}
std::vector<float> LlamaT3::logits(Graph& graph, int count) const {
    auto* tensor = graph.tensor("logits");
    std::vector<float> conditioned(vocabulary_), unconditioned(vocabulary_), result(vocabulary_);
    ggml_backend_tensor_get(tensor, conditioned.data(), size_t(count - 1) * tensor->nb[1], vocabulary_ * sizeof(float));
    ggml_backend_tensor_get(tensor, unconditioned.data(), tensor->nb[2] + size_t(count - 1) * tensor->nb[1], vocabulary_ * sizeof(float));
    for (int i = 0; i < vocabulary_; ++i)
        result[i] = conditioned[i] + knobs_.cfg_weight * (conditioned[i] - unconditioned[i]);
    return result;
}
std::vector<float> LlamaT3::prompt(const std::vector<int32_t>& text) {
    int condition = 1 + perceiver_ + 1, count = condition + int(text.size()) + 2;
    Graph graph(backend_, 8192);
    auto* ctx = graph.context();
    auto* text_tokens = ggml_new_tensor_1d(ctx, GGML_TYPE_I32, text.size());
    auto* text_pos = ggml_new_tensor_1d(ctx, GGML_TYPE_I32, text.size());
    auto* cond_pos = ggml_new_tensor_1d(ctx, GGML_TYPE_I32, conditioning_);
    auto* bos_tok = ggml_new_tensor_1d(ctx, GGML_TYPE_I32, 1);
    auto* bos_pos = ggml_new_tensor_1d(ctx, GGML_TYPE_I32, 1);
    auto* emotion = ggml_new_tensor_1d(ctx, GGML_TYPE_F32, 1);
    ggml_set_name(text_tokens, "text"); ggml_set_input(text_tokens);
    ggml_set_name(text_pos, "text_pos"); ggml_set_input(text_pos);
    ggml_set_name(cond_pos, "cond_pos"); ggml_set_input(cond_pos);
    ggml_set_name(bos_tok, "speech"); ggml_set_input(bos_tok);
    ggml_set_name(bos_pos, "speech_pos"); ggml_set_input(bos_pos);
    ggml_set_name(emotion, "emotion"); ggml_set_input(emotion);
    auto* speaker = ggml_add(ctx, ggml_mul_mat(ctx, weight("chatterbox/cond_spkr/w"), weight("chatterbox/builtin/speaker_emb")),
        weight("chatterbox/cond_spkr/b"));
    auto* hidden = ggml_add(ctx, ggml_get_rows(ctx, weight("chatterbox/speech_emb"), weight("chatterbox/builtin/cond_prompt_speech_tokens")),
        ggml_get_rows(ctx, weight("chatterbox/speech_pos_emb"), cond_pos));
    auto* cond = ggml_concat(ctx, ggml_concat(ctx, speaker, attend(ctx, hidden), 1),
        ggml_mul_mat(ctx, weight("chatterbox/emotion_adv_fc/w"), emotion), 1);
    cond = repeat(ctx, cond, condition);
    auto* positions = ggml_get_rows(ctx, weight("chatterbox/text_pos_emb"), text_pos);
    auto* embedded = ggml_add(ctx, ggml_get_rows(ctx, weight("chatterbox/text_emb"), text_tokens), positions);
    auto* tokens = ggml_concat(ctx, ggml_reshape_3d(ctx, embedded, width_, int(text.size()), 1),
        ggml_reshape_3d(ctx, positions, width_, int(text.size()), 1), 2);
    auto* bos = repeat(ctx, ggml_add(ctx, ggml_get_rows(ctx, weight("chatterbox/speech_emb"), bos_tok),
        ggml_get_rows(ctx, weight("chatterbox/speech_pos_emb"), bos_pos)), 1);
    hidden = ggml_concat(ctx, ggml_concat(ctx, ggml_concat(ctx, cond, tokens, 1), bos, 1), bos, 1);
    transformer(graph, hidden, 0, count);
    graph.allocate();
    graph.set("text", text.data(), text.size() * sizeof(int32_t));
    std::vector<int32_t> tpos(text.size()), cpos(conditioning_), pos(count);
    std::iota(tpos.begin(), tpos.end(), 0);
    std::iota(cpos.begin(), cpos.end(), 0);
    std::iota(pos.begin(), pos.end(), 0);
    graph.set("text_pos", tpos.data(), tpos.size() * sizeof(int32_t));
    graph.set("cond_pos", cpos.data(), cpos.size() * sizeof(int32_t));
    graph.set("speech", &start_, sizeof(start_));
    int32_t zero = 0;
    graph.set("speech_pos", &zero, sizeof(zero));
    graph.set("emotion", &knobs_.exaggeration, sizeof(knobs_.exaggeration));
    graph.set("position", pos.data(), pos.size() * sizeof(int32_t));
    std::vector<ggml_fp16_t> mask(size_t(count) * count, ggml_fp32_to_fp16(0.f));
    for (int q = 0; q < count; ++q)
        for (int k = q + 1; k < count; ++k) mask[size_t(q) * count + k] = ggml_fp32_to_fp16(-INFINITY);
    graph.set("mask", mask.data(), mask.size() * sizeof(ggml_fp16_t));
    graph.compute();
    return logits(graph, count);
}
std::vector<float> LlamaT3::step(int past, int32_t token, int speech) {
    Graph graph(backend_, 8192);
    auto* ctx = graph.context();
    auto* speech_token = ggml_new_tensor_1d(ctx, GGML_TYPE_I32, 1);
    auto* speech_pos = ggml_new_tensor_1d(ctx, GGML_TYPE_I32, 1);
    ggml_set_name(speech_token, "speech"); ggml_set_input(speech_token);
    ggml_set_name(speech_pos, "speech_pos"); ggml_set_input(speech_pos);
    auto* hidden = repeat(ctx, ggml_add(ctx, ggml_get_rows(ctx, weight("chatterbox/speech_emb"), speech_token),
        ggml_get_rows(ctx, weight("chatterbox/speech_pos_emb"), speech_pos)), 1);
    transformer(graph, hidden, past, 1);
    graph.allocate();
    graph.set("speech", &token, sizeof(token));
    graph.set("speech_pos", &speech, sizeof(speech));
    graph.set("position", &past, sizeof(past));
    graph.compute();
    return logits(graph, 1);
}
int32_t LlamaT3::sample(const std::vector<float>& logits, const std::vector<int32_t>& generated, std::mt19937& rng) const {
    auto scores = logits;
    penalty_.apply(scores, generated);
    if (knobs_.temperature > 0.f && knobs_.temperature != 1.f) {
        float inverse = 1.f / knobs_.temperature;
        for (auto& score : scores) score *= inverse;
    }
    auto probabilities = [&](const std::vector<float>& values) {
        float maximum = -INFINITY, sum = 0;
        std::vector<float> result(values.size());
        for (float value : values) if (value != -INFINITY) maximum = std::max(maximum, value);
        for (size_t i = 0; i < values.size(); ++i) {
            result[i] = values[i] == -INFINITY ? 0.f : std::exp(values[i] - maximum);
            sum += result[i];
        }
        for (auto& value : result) value /= sum;
        return result;
    };
    {
        auto probs = probabilities(scores);
        float peak = 0;
        for (float p : probs) peak = std::max(peak, p);
        float limit = knobs_.min_p * peak;
        for (int i = 0; i < int(scores.size()); ++i) if (probs[i] < limit) scores[i] = -INFINITY;
    }
    if (knobs_.top_p < 1.f) {
        struct Item { int index; float score; };
        std::vector<Item> sorted;
        for (int i = 0; i < int(scores.size()); ++i) if (scores[i] != -INFINITY) sorted.push_back({i, scores[i]});
        std::sort(sorted.begin(), sorted.end(), [](const Item& a, const Item& b) { return a.score > b.score; });
        float maximum = sorted[0].score, sum = 0, cumulative = 0;
        std::vector<float> probs(sorted.size());
        for (size_t i = 0; i < sorted.size(); ++i) { probs[i] = std::exp(sorted[i].score - maximum); sum += probs[i]; }
        for (auto& p : probs) p /= sum;
        std::set<int> keep;
        for (size_t i = 0; i < sorted.size(); ++i) {
            cumulative += probs[i];
            keep.insert(sorted[i].index);
            if (cumulative >= knobs_.top_p) break;
        }
        for (int i = 0; i < int(scores.size()); ++i) if (!keep.count(i)) scores[i] = -INFINITY;
    }
    auto probs = probabilities(scores);
    return std::discrete_distribution<int32_t>(probs.begin(), probs.end())(rng);
}
std::vector<int32_t> LlamaT3::generate(const std::vector<int32_t>& text, SynthesizeStats& stats) {
    int past = 1 + perceiver_ + 1 + int(text.size()) + 2;
    reserve(past);
    std::mt19937 rng(knobs_.seed);
    auto scores = prompt(text);
    std::vector<int32_t> generated{start_}, predicted;
    for (int i = 0; i < knobs_.n_predict && past + 1 <= context_; ++i) {
        int32_t token = sample(scores, generated, rng);
        predicted.push_back(token);
        generated.push_back(token);
        if (token == stop_) break;
        scores = step(past++, token, i + 1);
    }
    size_t begin = 0, end = predicted.size();
    for (size_t i = 0; i < predicted.size(); ++i) if (predicted[i] == start_) { begin = i + 1; break; }
    for (size_t i = 0; i < predicted.size(); ++i) if (predicted[i] == stop_) { end = i; break; }
    stats.predicted_count = int(predicted.size()); stats.dropped_count = int(end - begin);
    stats.eos = predicted.back() == stop_; stats.n_past = past; stats.text_tokens = int(text.size());
    return std::vector<int32_t>(predicted.begin() + begin, predicted.begin() + end);
}
}
