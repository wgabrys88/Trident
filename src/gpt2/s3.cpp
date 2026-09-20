#include "s3.h"
#include "common/s3_dsp.h"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <random>

namespace trident::gpt2 {
MeanflowS3::MeanflowS3(const std::string& path, const VulkanBackend& backend, Knobs knobs)
    : backend_(backend), knobs_(knobs), file_(path), weights_(file_, backend, true),
      width_(int(file_.tensor("flow/input_embedding")->ne[0])), mels_(int(file_.tensor("flow/spk_embed_affine/b")->ne[0])),
      speaker_size_(int(file_.tensor("flow/spk_embed_affine/w")->ne[0])),
      embeddings_(file_.floats("flow/input_embedding")), speaker_weight_(file_.floats("flow/spk_embed_affine/w")),
      speaker_bias_(file_.floats("flow/spk_embed_affine/b")), prompt_features_(file_.floats("s3gen/builtin/prompt_feat")),
      speaker_(file_.floats("s3gen/builtin/embedding")), source_weight_(file_.floats("hift/m_source/l_linear/weight")),
      source_bias_(file_.floats("hift/m_source/l_linear/bias").at(0)) {
    auto* tokens = file_.tensor("s3gen/builtin/prompt_token");
    prompt_tokens_.resize(ggml_nelements(tokens));
    std::memcpy(prompt_tokens_.data(), ggml_get_data(tokens), ggml_nbytes(tokens));
    for (int64_t i = 0; i < gguf_get_n_tensors(file_.get()); ++i) {
        std::string name = gguf_get_tensor_name(file_.get(), i);
        if (name.rfind("hift/", 0) == 0 && name.size() >= 6 && name.compare(name.size() - 6, 6, "/alpha") == 0) {
            auto values = file_.floats(name.c_str());
            for (auto& value : values) value = 1.f / (value + 1e-9f);
            inverse_alpha_.emplace(name, std::move(values));
        }
    }
}
ggml_tensor* MeanflowS3::conv(ggml_context* ctx, ggml_tensor* kernel, ggml_tensor* input, int stride, int padding, int dilation) const {
    auto* columns = ggml_im2col(ctx, kernel, input, stride, 0, padding, 0, dilation, 0, false, GGML_TYPE_F32);
    auto* result = ggml_mul_mat(ctx,
        ggml_reshape_2d(ctx, columns, columns->ne[0], columns->ne[2] * columns->ne[1]),
        ggml_reshape_2d(ctx, kernel, kernel->ne[0] * kernel->ne[1], kernel->ne[2]));
    return ggml_reshape_3d(ctx, result, columns->ne[1], kernel->ne[2], columns->ne[2]);
}
ggml_tensor* MeanflowS3::pad(ggml_context* ctx, ggml_tensor* input, int front, int back) const {
    auto* result = input;
    if (front > 0) {
        auto* head = ggml_view_4d(ctx, input, front, input->ne[1], input->ne[2], input->ne[3], input->nb[1], input->nb[2], input->nb[3], 0);
        result = ggml_concat(ctx, ggml_scale(ctx, ggml_cont(ctx, head), 0.f), result, 0);
    }
    if (back > 0) {
        auto* tail = ggml_view_4d(ctx, input, back, input->ne[1], input->ne[2], input->ne[3], input->nb[1], input->nb[2], input->nb[3], size_t(input->ne[0] - back) * input->nb[0]);
        result = ggml_concat(ctx, result, ggml_scale(ctx, ggml_cont(ctx, tail), 0.f), 0);
    }
    return result;
}
ggml_tensor* MeanflowS3::transpose(ggml_context* ctx, ggml_tensor* x) const { return ggml_cont(ctx, ggml_permute(ctx, x, 1, 0, 2, 3)); }
ggml_tensor* MeanflowS3::linear(ggml_context* ctx, ggml_tensor* x, const std::string& name, const char* w, const char* b) const {
    return ggml_add(ctx, ggml_mul_mat(ctx, weight(name + w), x), weight(name + b));
}
ggml_tensor* MeanflowS3::norm(ggml_context* ctx, ggml_tensor* x, const std::string& name, float epsilon, const char* w, const char* b) const {
    return ggml_add(ctx, ggml_mul(ctx, ggml_norm(ctx, x, epsilon), weight(name + w)), weight(name + b));
}
ggml_tensor* MeanflowS3::convolution(ggml_context* ctx, ggml_tensor* x, const std::string& name, int stride, int padding, int dilation, const char* w, const char* b) const {
    auto* kernel = weight(name + w);
    return ggml_add(ctx, conv(ctx, kernel, x, stride, padding, dilation), ggml_reshape_2d(ctx, weight(name + b), 1, kernel->ne[2]));
}
void MeanflowS3::finish(Graph& graph, ggml_tensor* output, const char* name) const {
    ggml_set_name(output, name); ggml_set_output(output); ggml_build_forward_expand(graph.graph, output); graph.allocate();
}
void MeanflowS3::set(Graph& graph, const char* name, const std::vector<float>& values) const { graph.set(name, values.data(), values.size() * sizeof(float)); }
ggml_tensor* MeanflowS3::conformer(ggml_context* ctx, ggml_tensor* x, ggml_tensor* positions, const std::string& name, int frames) const {
    int head = int(weight(name + "/attn/pos_bias_u")->ne[0]), heads = int(weight(name + "/attn/pos_bias_u")->ne[1]), width = head * heads;
    auto* normalized = norm(ctx, x, name + "/norm_mha", 1e-12f, "/w", "/b");
    auto projection = [&](const char* suffix) {
        auto* value = linear(ctx, normalized, name + suffix, "/w", "/b");
        return ggml_cont(ctx, ggml_permute(ctx, ggml_reshape_3d(ctx, value, head, heads, frames), 0, 2, 1, 3));
    };
    auto* query = projection("/attn/q"); auto* key = projection("/attn/k"); auto* value = projection("/attn/v");
    auto* positional = ggml_mul_mat(ctx, weight(name + "/attn/pos/w"), positions);
    positional = ggml_cont(ctx, ggml_permute(ctx, ggml_reshape_3d(ctx, positional, head, heads, positions->ne[1]), 0, 2, 1, 3));
    auto* content = ggml_mul_mat(ctx, key, ggml_add(ctx, query, ggml_reshape_3d(ctx, weight(name + "/attn/pos_bias_u"), head, 1, heads)));
    auto* relative = ggml_mul_mat(ctx, positional, ggml_add(ctx, query, ggml_reshape_3d(ctx, weight(name + "/attn/pos_bias_v"), head, 1, heads)));
    relative = ggml_reshape_3d(ctx, pad(ctx, relative, 1, 0), frames, 2 * frames, heads);
    relative = ggml_view_3d(ctx, relative, frames, 2 * frames - 1, heads, relative->nb[1], relative->nb[2], relative->nb[1]);
    relative = ggml_reshape_3d(ctx, ggml_cont(ctx, relative), 2 * frames - 1, frames, heads);
    relative = ggml_cont(ctx, ggml_view_3d(ctx, relative, frames, frames, heads, relative->nb[1], relative->nb[2], 0));
    auto* attention = ggml_soft_max(ctx, ggml_scale(ctx, ggml_add(ctx, content, relative), 1.f / std::sqrt(float(head))));
    auto* attended = ggml_mul_mat(ctx, transpose(ctx, value), attention);
    attended = ggml_reshape_2d(ctx, ggml_cont(ctx, ggml_permute(ctx, attended, 0, 2, 1, 3)), width, frames);
    x = ggml_add(ctx, x, linear(ctx, attended, name + "/attn/o", "/w", "/b"));
    auto* ff = linear(ctx, norm(ctx, x, name + "/norm_ff", 1e-12f, "/w", "/b"), name + "/ff/w1", "/w", "/b");
    return ggml_add(ctx, x, linear(ctx, ggml_silu(ctx, ff), name + "/ff/w2", "/w", "/b"));
}
std::vector<float> MeanflowS3::positions(int frames) const {
    int width = width_;
    std::vector<float> result(size_t(2 * frames - 1) * width), divisors(width / 2);
    float logarithm = std::log(10000.f);
    for (int k = 0; k < width / 2; ++k) divisors[k] = std::exp(-(float(2 * k) * logarithm / float(width)));
    for (int t = 0; t < frames; ++t)
        for (int k = 0; k < width / 2; ++k) {
            float position = float(frames - 1 - t);
            result[t * width + 2 * k] = std::sin(position * divisors[k]); result[t * width + 2 * k + 1] = std::cos(position * divisors[k]);
        }
    for (int t = 1; t < frames; ++t)
        for (int k = 0; k < width / 2; ++k) {
            size_t row = size_t(frames - 1 + t) * width;
            result[row + 2 * k] = std::sin(-float(t) * divisors[k]); result[row + 2 * k + 1] = std::cos(-float(t) * divisors[k]);
        }
    return result;
}
std::vector<float> MeanflowS3::encode(const std::vector<float>& input, int frames) {
    if (encoder_frames_ != frames) {
        encoder_ = std::make_unique<Graph>(backend_, 32768); encoder_frames_ = frames;
        auto* ctx = encoder_->context();
        auto* x = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, width_, frames);
        auto* pos1 = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, width_, 2 * frames - 1);
        auto* pos2 = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, width_, 4 * frames - 1);
        ggml_set_name(x, "input"); ggml_set_input(x);
        ggml_set_name(pos1, "pos1"); ggml_set_input(pos1); ggml_set_name(pos2, "pos2"); ggml_set_input(pos2);
        x = ggml_scale(ctx, norm(ctx, linear(ctx, x, "flow/encoder/embed/linear", "/w", "/b"), "flow/encoder/embed/norm", 1e-5f, "/w", "/b"), std::sqrt(float(width_)));
        auto* residual = x;
        auto* lookahead = convolution(ctx, pad(ctx, transpose(ctx, x), 0, 3), "flow/encoder/pre_lookahead/conv1", 1, 0, 1, "/w", "/b");
        lookahead = ggml_leaky_relu(ctx, lookahead, 0.01f, false);
        lookahead = convolution(ctx, pad(ctx, lookahead, 2, 0), "flow/encoder/pre_lookahead/conv2", 1, 0, 1, "/w", "/b");
        x = ggml_add(ctx, transpose(ctx, lookahead), residual);
        for (int i = 0; i < weights_.count("flow/encoder/block", "/attn/pos_bias_u"); ++i) x = conformer(ctx, x, pos1, "flow/encoder/block" + std::to_string(i), frames);
        auto* up = transpose(ctx, x);
        up = ggml_reshape_3d(ctx, up, 1, up->ne[0], up->ne[1]);
        auto* doubled = ggml_concat(ctx, up, up, 0);
        up = ggml_cont(ctx, ggml_reshape_2d(ctx, doubled, up->ne[1] * 2, up->ne[2]));
        up = convolution(ctx, pad(ctx, up, 4, 0), "flow/encoder/up_layer/conv", 1, 0, 1, "/w", "/b");
        x = linear(ctx, transpose(ctx, up), "flow/encoder/up_embed/linear", "/w", "/b");
        x = ggml_scale(ctx, norm(ctx, x, "flow/encoder/up_embed/norm", 1e-5f, "/w", "/b"), std::sqrt(float(width_)));
        for (int i = 0; i < weights_.count("flow/encoder/up_block", "/attn/pos_bias_u"); ++i) x = conformer(ctx, x, pos2, "flow/encoder/up_block" + std::to_string(i), 2 * frames);
        x = norm(ctx, x, "flow/encoder/after_norm", 1e-5f, "/w", "/b");
        finish(*encoder_, linear(ctx, x, "flow/encoder_proj", "/w", "/b"));
        set(*encoder_, "pos1", positions(frames)); set(*encoder_, "pos2", positions(2 * frames));
    }
    set(*encoder_, "input", input); encoder_->compute(); return encoder_->read("out");
}
ggml_tensor* MeanflowS3::causal(ggml_context* ctx, ggml_tensor* x, const std::string& name) const {
    auto* y = convolution(ctx, pad(ctx, x, 2, 0), name + "/block/0", 1, 0);
    y = transpose(ctx, norm(ctx, transpose(ctx, y), name + "/block/2"));
    return ggml_mul(ctx, y, ggml_tanh(ctx, ggml_unary(ctx, y, GGML_UNARY_OP_SOFTPLUS)));
}
ggml_tensor* MeanflowS3::resnet(ggml_context* ctx, ggml_tensor* x, ggml_tensor* time, const std::string& name) const {
    auto* hidden = causal(ctx, x, name + "/block1");
    auto* feature = ggml_mul(ctx, time, ggml_tanh(ctx, ggml_unary(ctx, time, GGML_UNARY_OP_SOFTPLUS)));
    auto* projected = linear(ctx, feature, name + "/mlp/1");
    hidden = ggml_add(ctx, hidden, ggml_reshape_2d(ctx, projected, 1, projected->ne[0]));
    hidden = causal(ctx, hidden, name + "/block2");
    return ggml_add(ctx, hidden, convolution(ctx, x, name + "/res_conv", 1, 0));
}
ggml_tensor* MeanflowS3::transformer(ggml_context* ctx, ggml_tensor* x, const std::string& name, int frames) const {
    int head = int(weight("flow/encoder/block0/attn/pos_bias_u")->ne[0]), width = int(weight(name + "/attn1/to_q/weight")->ne[1]), heads = width / head;
    auto* normalized = norm(ctx, x, name + "/norm1");
    auto projection = [&](const char* suffix) {
        auto* value = ggml_mul_mat(ctx, weight(name + suffix), normalized);
        return ggml_view_3d(ctx, value, head, frames, heads, width * sizeof(float), head * sizeof(float), 0);
    };
    auto* query = projection("/attn1/to_q/weight"); auto* key = projection("/attn1/to_k/weight"); auto* value = projection("/attn1/to_v/weight");
    auto* attention = ggml_flash_attn_ext(ctx, query, key, value, nullptr, 1.f / std::sqrt(float(head)), 0.f, 0.f);
    auto* flat = ggml_reshape_2d(ctx, attention, width, frames);
    x = ggml_add(ctx, x, linear(ctx, flat, name + "/attn1/to_out/0"));
    auto* ff = ggml_gelu_erf(ctx, linear(ctx, norm(ctx, x, name + "/norm3"), name + "/ff/net/0/proj"));
    return ggml_add(ctx, x, linear(ctx, ff, name + "/ff/net/2"));
}
ggml_tensor* MeanflowS3::stack(ggml_context* ctx, ggml_tensor* x, const std::string& name, int frames) const {
    x = transpose(ctx, x);
    for (int i = 0; i < weights_.count(name + "/", "/attn1/to_q/weight"); ++i) x = transformer(ctx, x, name + "/" + std::to_string(i), frames);
    return transpose(ctx, x);
}
std::vector<float> MeanflowS3::time(float value) {
    if (!time_) {
        time_ = std::make_unique<Graph>(backend_, 128);
        auto* ctx = time_->context();
        auto* input = ggml_new_tensor_1d(ctx, GGML_TYPE_F32, weight("cfm/time_mlp/linear_1/weight")->ne[0]);
        ggml_set_name(input, "input"); ggml_set_input(input);
        finish(*time_, linear(ctx, ggml_silu(ctx, linear(ctx, input, "cfm/time_mlp/linear_1")), "cfm/time_mlp/linear_2"));
    }
    std::vector<float> input(weight("cfm/time_mlp/linear_1/weight")->ne[0]);
    int half = int(input.size() / 2);
    float factor = std::log(10000.f) / float(half - 1);
    for (int i = 0; i < half; ++i) {
        float frequency = std::exp(-float(i) * factor), angle = 1000.f * value * frequency;
        input[i] = std::sin(angle); input[i + half] = std::cos(angle);
    }
    set(*time_, "input", input); time_->compute(); return time_->read("out");
}
std::vector<float> MeanflowS3::mix(const std::vector<float>& first, const std::vector<float>& second) {
    if (!mixer_) {
        mixer_ = std::make_unique<Graph>(backend_, 128);
        auto* ctx = mixer_->context();
        auto* t = ggml_new_tensor_1d(ctx, GGML_TYPE_F32, first.size());
        auto* r = ggml_new_tensor_1d(ctx, GGML_TYPE_F32, second.size());
        ggml_set_name(t, "t"); ggml_set_input(t); ggml_set_name(r, "r"); ggml_set_input(r);
        finish(*mixer_, ggml_mul_mat(ctx, weight("cfm/time_embed_mixer/weight"), ggml_concat(ctx, t, r, 0)));
    }
    set(*mixer_, "t", first); set(*mixer_, "r", second); mixer_->compute(); return mixer_->read("out");
}
std::vector<float> MeanflowS3::estimate(const std::vector<float>& state, const std::vector<float>& mu,
    const std::vector<float>& time, const std::vector<float>& speaker, const std::vector<float>& condition, int frames) {
    if (estimator_frames_ != frames) {
        estimator_ = std::make_unique<Graph>(backend_, 65536); estimator_frames_ = frames;
        auto* ctx = estimator_->context();
        auto input = [&](const char* name, int width, int channels) {
            auto* value = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, width, channels);
            ggml_set_name(value, name); ggml_set_input(value); return value;
        };
        auto* x = input("x", frames, mels_); auto* mean = input("mu", frames, mels_);
        auto* spk = input("speaker", mels_, 1); auto* cond = input("condition", frames, mels_); auto* t = input("time", int(time.size()), 1);
        auto* repeated = ggml_repeat(ctx, ggml_reshape_2d(ctx, spk, 1, mels_), x);
        x = ggml_concat(ctx, ggml_concat(ctx, ggml_concat(ctx, x, mean, 1), repeated, 1), cond, 1);
        x = stack(ctx, resnet(ctx, x, t, "cfm/down_blocks/0/0"), "cfm/down_blocks/0/1", frames);
        auto* residual = x;
        x = convolution(ctx, pad(ctx, x, 2, 0), "cfm/down_blocks/0/2", 1, 0);
        for (int i = 0; i < weights_.count("cfm/mid_blocks/", "/0/res_conv/weight"); ++i) {
            std::string name = "cfm/mid_blocks/" + std::to_string(i);
            x = stack(ctx, resnet(ctx, x, t, name + "/0"), name + "/1", frames);
        }
        x = ggml_concat(ctx, x, residual, 1);
        x = stack(ctx, resnet(ctx, x, t, "cfm/up_blocks/0/0"), "cfm/up_blocks/0/1", frames);
        x = convolution(ctx, pad(ctx, x, 2, 0), "cfm/up_blocks/0/2", 1, 0);
        x = causal(ctx, x, "cfm/final_block");
        finish(*estimator_, convolution(ctx, x, "cfm/final_proj", 1, 0));
    }
    set(*estimator_, "x", state); set(*estimator_, "mu", mu); set(*estimator_, "time", time);
    set(*estimator_, "speaker", speaker); set(*estimator_, "condition", condition);
    estimator_->compute(); return estimator_->read("out");
}
std::vector<float> MeanflowS3::pitch(const std::vector<float>& mel, int frames) const {
    Graph graph(backend_, 1024);
    auto* ctx = graph.context();
    auto* x = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, frames, mels_);
    ggml_set_name(x, "mel"); ggml_set_input(x);
    for (int i = 0; i < weights_.count("hift/f0_predictor/condnet/", "/weight"); ++i)
        x = ggml_unary(ctx, convolution(ctx, x, "hift/f0_predictor/condnet/" + std::to_string(i * 2), 1, 1), GGML_UNARY_OP_ELU);
    x = ggml_reshape_1d(ctx, ggml_abs(ctx, linear(ctx, transpose(ctx, x), "hift/f0_predictor/classifier")), frames);
    finish(graph, x); set(graph, "mel", mel); graph.compute(); return graph.read("out");
}
std::vector<float> MeanflowS3::source(const std::vector<float>& pitch) const {
    int frames = int(pitch.size()) * 480, harmonics = int(source_weight_.size());
    uint32_t seed = uint32_t(knobs_.seed + 1);
    std::mt19937 random(seed);
    std::uniform_real_distribution<float> uniform(-float(M_PI), float(M_PI));
    std::vector<float> phases(harmonics, 0.f), waves(size_t(harmonics) * frames, 0.f), result(frames);
    std::vector<double> accumulated(harmonics, 0.0);
    for (int harmonic = 1; harmonic < harmonics; ++harmonic) phases[harmonic] = uniform(random);
    for (int t = 0; t < frames; ++t) {
        float frequency = pitch[t / 480]; bool voiced = frequency > 10.f;
        for (int h = 0; h < harmonics; ++h) {
            accumulated[h] += double(frequency) * (h + 1) / 24000.0;
            double angle = 2.0 * M_PI * (accumulated[h] - std::floor(accumulated[h]));
            float sine = 0.1f * std::sin(float(angle) + phases[h]);
            float amplitude = voiced ? 0.003f : 0.1f / 3.f;
            waves[size_t(h) * frames + t] = sine * (voiced ? 1.f : 0.f) + amplitude * S3Dsp::positioned_noise(seed, uint64_t(t) * harmonics + h);
        }
    }
    for (int t = 0; t < frames; ++t) {
        float sum = source_bias_;
        for (int h = 0; h < harmonics; ++h) sum += source_weight_[h] * waves[size_t(h) * frames + t];
        result[t] = std::tanh(sum);
    }
    return result;
}
std::vector<float> MeanflowS3::stft(const std::vector<float>& signal) const {
    Graph graph(backend_, 8192);
    auto* ctx = graph.context();
    auto* input = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, signal.size(), 1);
    auto* kernel = ggml_new_tensor_3d(ctx, GGML_TYPE_F32, 16, 1, 18);
    ggml_set_name(input, "input"); ggml_set_input(input); ggml_set_name(kernel, "kernel"); ggml_set_input(kernel);
    auto* padded = input;
    for (int i = 0; i < 8; ++i) {
        auto* value = ggml_view_3d(ctx, input, 1, 1, 1, input->nb[1], input->nb[2], size_t(8 - i) * input->nb[0]);
        padded = ggml_concat(ctx, ggml_cont(ctx, value), padded, 0);
    }
    for (int i = 0; i < 8; ++i) {
        auto* value = ggml_view_3d(ctx, input, 1, 1, 1, input->nb[1], input->nb[2], (signal.size() - 2 - i) * input->nb[0]);
        padded = ggml_concat(ctx, padded, ggml_cont(ctx, value), 0);
    }
    finish(graph, conv(ctx, kernel, padded, 4, 0));
    set(graph, "input", signal); set(graph, "kernel", S3Dsp::stft(16, S3Dsp::hann(16)));
    graph.compute(); return graph.read("out");
}
std::vector<float> MeanflowS3::hift(const std::vector<float>& mel, int frames, const std::vector<float>& spectrum, int stft_frames) const {
    Graph graph(backend_, 131072);
    auto* ctx = graph.context();
    auto* input = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, frames, mels_);
    auto* source = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, stft_frames, 18);
    ggml_set_name(input, "mel"); ggml_set_input(input); ggml_set_name(source, "source"); ggml_set_input(source);
    std::vector<std::string> inverses;
    auto snake = [&](ggml_tensor* x, const std::string& name) {
        auto* alpha = weight(name);
        auto* inverse = ggml_new_tensor_1d(ctx, GGML_TYPE_F32, alpha->ne[0]);
        ggml_set_name(inverse, ("inv_" + name).c_str()); ggml_set_input(inverse); inverses.push_back(name);
        auto* multiplied = ggml_mul(ctx, x, ggml_reshape_2d(ctx, alpha, 1, alpha->ne[0]));
        auto* sine = ggml_sin(ctx, multiplied);
        return ggml_add(ctx, x, ggml_mul(ctx, ggml_mul(ctx, sine, sine), ggml_reshape_2d(ctx, inverse, 1, inverse->ne[0])));
    };
    auto residual = [&](ggml_tensor* x, const std::string& name) {
        for (int i = 0; i < weights_.count(name + "/convs1/", "/weight"); ++i) {
            std::string index = std::to_string(i); int dilation = 2 * i + 1;
            int kernel = int(weight(name + "/convs1/" + index + "/weight")->ne[0]);
            auto* hidden = snake(x, name + "/activations1/" + index + "/alpha");
            hidden = convolution(ctx, hidden, name + "/convs1/" + index, 1, (kernel * dilation - dilation) / 2, dilation);
            hidden = snake(hidden, name + "/activations2/" + index + "/alpha");
            hidden = convolution(ctx, hidden, name + "/convs2/" + index, 1, (int(weight(name + "/convs2/" + index + "/weight")->ne[0]) - 1) / 2);
            x = ggml_add(ctx, x, hidden);
        }
        return x;
    };
    auto* x = convolution(ctx, input, "hift/conv_pre", 1, 3);
    int ups = weights_.count("hift/ups/", "/weight"), groups = weights_.count("hift/resblocks/", "/convs1/0/weight") / ups;
    for (int i = 0; i < ups; ++i) {
        std::string index = std::to_string(i);
        auto* upsample = weight("hift/ups/" + index + "/weight");
        int rate = int(upsample->ne[0]) / 2;
        x = ggml_leaky_relu(ctx, x, 0.1f, false);
        x = ggml_conv_transpose_1d(ctx, upsample, x, rate, 0, 1);
        int padding = (int(upsample->ne[0]) - rate) / 2;
        x = ggml_cont(ctx, ggml_view_3d(ctx, x, x->ne[0] - 2 * padding, x->ne[1], x->ne[2], x->nb[1], x->nb[2], padding * x->nb[0]));
        x = ggml_add(ctx, x, ggml_reshape_2d(ctx, weight("hift/ups/" + index + "/bias"), 1, weight("hift/ups/" + index + "/bias")->ne[0]));
        if (i + 1 == ups) {
            auto* first = ggml_cont(ctx, ggml_view_3d(ctx, x, 1, x->ne[1], x->ne[2], x->nb[1], x->nb[2], x->nb[0]));
            x = ggml_concat(ctx, first, x, 0);
        }
        int stride = std::max(int(weight("hift/source_downs/" + index + "/weight")->ne[0]) / 2, 1);
        auto* injected = convolution(ctx, source, "hift/source_downs/" + index, stride, (stride - 1) / 2);
        injected = residual(injected, "hift/source_resblocks/" + index);
        x = ggml_add(ctx, x, injected);
        auto* combined = residual(x, "hift/resblocks/" + std::to_string(i * groups));
        for (int j = 1; j < groups; ++j) combined = ggml_add(ctx, combined, residual(x, "hift/resblocks/" + std::to_string(i * groups + j)));
        x = ggml_scale(ctx, combined, 1.f / float(groups));
    }
    x = convolution(ctx, ggml_leaky_relu(ctx, x, 0.01f, false), "hift/conv_post", 1, 3);
    auto* magnitude = ggml_cont(ctx, ggml_view_2d(ctx, x, stft_frames, 9, x->nb[1], 0));
    magnitude = ggml_exp(ctx, ggml_clamp(ctx, magnitude, -1e6f, 1e2f));
    auto* phase = ggml_sin(ctx, ggml_cont(ctx, ggml_view_2d(ctx, x, stft_frames, 9, x->nb[1], 9 * x->nb[1])));
    auto* spec = ggml_concat(ctx, ggml_mul(ctx, magnitude, ggml_cos(ctx, phase)), ggml_mul(ctx, magnitude, ggml_sin(ctx, phase)), 1);
    auto window = S3Dsp::hann(16), kernel_data = S3Dsp::istft(16, window), sums = S3Dsp::window_sum(stft_frames, 16, 4, window);
    auto* kernel = ggml_new_tensor_3d(ctx, GGML_TYPE_F32, 16, 1, 18);
    auto* sum = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, sums.size(), 1);
    ggml_set_name(kernel, "kernel"); ggml_set_input(kernel); ggml_set_name(sum, "sum"); ggml_set_input(sum);
    auto* wave = ggml_div(ctx, ggml_conv_transpose_1d(ctx, kernel, spec, 4, 0, 1), sum);
    wave = ggml_cont(ctx, ggml_view_2d(ctx, wave, sums.size() - 16, wave->ne[1], wave->nb[1], 8 * wave->nb[0]));
    finish(graph, ggml_clamp(ctx, wave, -0.99f, 0.99f));
    set(graph, "mel", mel); set(graph, "source", spectrum); set(graph, "kernel", kernel_data); set(graph, "sum", sums);
    for (const auto& name : inverses) set(graph, ("inv_" + name).c_str(), inverse_alpha_.at(name));
    graph.compute(); return graph.read("out");
}
std::vector<float> MeanflowS3::synthesize(const std::vector<int32_t>& speech) {
    auto tokens = prompt_tokens_;
    for (int32_t token : speech) if (token >= 0 && token < int(embeddings_.size() / size_t(width_))) tokens.push_back(token);
    std::vector<float> embedded(tokens.size() * width_);
    for (size_t i = 0; i < tokens.size(); ++i) std::memcpy(embedded.data() + i * width_, embeddings_.data() + size_t(tokens[i]) * width_, width_ * sizeof(float));
    auto encoded = encode(embedded, int(tokens.size()));
    int frames = 2 * int(tokens.size()), prompt_frames = int(prompt_features_.size() / mels_);
    std::vector<float> mu(frames * mels_), condition(frames * mels_, 0.f), state(frames * mels_), speaker(mels_);
    for (int m = 0; m < mels_; ++m)
        for (int t = 0; t < frames; ++t) {
            mu[m * frames + t] = encoded[t * mels_ + m];
            bool generated = t >= prompt_frames;
            int64_t frame = generated ? t - prompt_frames : t;
            state[m * frames + t] = S3Dsp::positioned_noise(knobs_.seed + (generated ? 2 : 0), frame * mels_ + m);
        }
    float norm = 0.f;
    for (int i = 0; i < speaker_size_; ++i) norm += speaker_[i] * speaker_[i];
    norm = std::sqrt(norm + 1e-12f);
    std::vector<float> normalized(speaker_size_);
    for (int i = 0; i < speaker_size_; ++i) normalized[i] = speaker_[i] / norm;
    for (int m = 0; m < mels_; ++m) {
        float accumulator = speaker_bias_[m];
        for (int i = 0; i < speaker_size_; ++i) accumulator += speaker_weight_[m * speaker_size_ + i] * normalized[i];
        speaker[m] = accumulator;
        for (int t = 0; t < prompt_frames; ++t) condition[m * frames + t] = prompt_features_[t * mels_ + m];
    }
    for (int step = 0; step < knobs_.cfm_steps; ++step) {
        float t = float(step) / float(knobs_.cfm_steps), r = float(step + 1) / float(knobs_.cfm_steps);
        auto t_embedding = time(t), r_embedding = time(r);
        auto delta = estimate(state, mu, mix(t_embedding, r_embedding), speaker, condition, frames);
        for (size_t i = 0; i < state.size(); ++i) state[i] += (r - t) * delta[i];
    }
    int mel_frames = frames - prompt_frames;
    std::vector<float> mel(mels_ * mel_frames);
    for (int m = 0; m < mels_; ++m)
        for (int t = 0; t < mel_frames; ++t) mel[m * mel_frames + t] = state[m * frames + t + prompt_frames];
    auto spectrum = stft(source(pitch(mel, mel_frames)));
    return hift(mel, mel_frames, spectrum, int(spectrum.size() / 18));
}
}
