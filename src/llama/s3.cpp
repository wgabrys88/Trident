#include "s3.h"
#include "common/s3_dsp.h"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <random>

namespace trident::llama {
CfgS3::CfgS3(const std::string& path, const VulkanBackend& backend, Knobs knobs)
    : backend_(backend), knobs_(knobs), file_(path), weights_(file_, backend, true),
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
ggml_tensor* CfgS3::conv(ggml_context* ctx, ggml_tensor* kernel, ggml_tensor* input, int stride, int padding, int dilation) const {
    auto* columns = ggml_im2col(ctx, kernel, input, stride, 0, padding, 0, dilation, 0, false, GGML_TYPE_F32);
    auto* flattened = ggml_reshape_2d(ctx, kernel, kernel->ne[0] * kernel->ne[1], kernel->ne[2]);
    auto* result = ggml_mul_mat(ctx, flattened, columns);
    return ggml_cont(ctx, ggml_permute(ctx, result, 1, 0, 2, 3));
}
ggml_tensor* CfgS3::pad(ggml_context* ctx, ggml_tensor* input, int front, int back) const {
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
ggml_tensor* CfgS3::transpose(ggml_context* ctx, ggml_tensor* x) const { return ggml_cont(ctx, ggml_permute(ctx, x, 1, 0, 2, 3)); }
ggml_tensor* CfgS3::linear(ggml_context* ctx, ggml_tensor* x, const std::string& name, const char* w, const char* b) const {
    return ggml_add(ctx, ggml_mul_mat(ctx, weight(name + w), x), weight(name + b));
}
ggml_tensor* CfgS3::norm(ggml_context* ctx, ggml_tensor* x, const std::string& name, float epsilon, const char* w, const char* b) const {
    return ggml_add(ctx, ggml_mul(ctx, ggml_norm(ctx, x, epsilon), weight(name + w)), weight(name + b));
}
ggml_tensor* CfgS3::convolution(ggml_context* ctx, ggml_tensor* x, const std::string& name, int stride, int padding, int dilation, const char* w, const char* b) const {
    auto* kernel = weight(name + w);
    return ggml_add(ctx, conv(ctx, kernel, x, stride, padding, dilation), ggml_reshape_2d(ctx, weight(name + b), 1, kernel->ne[2]));
}
void CfgS3::finish(Graph& graph, ggml_tensor* output, const char* name) const {
    ggml_set_name(output, name); ggml_set_output(output); ggml_build_forward_expand(graph.graph, output); graph.allocate();
}
void CfgS3::set(Graph& graph, const char* name, const std::vector<float>& values) const { graph.set(name, values.data(), values.size() * sizeof(float)); }
ggml_tensor* CfgS3::conformer(ggml_context* ctx, ggml_tensor* x, ggml_tensor* positions, const std::string& name, int frames) const {
    constexpr int heads = 8, head = 64, width = 512;
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
std::vector<float> CfgS3::positions(int frames) const {
    constexpr int width = 512;
    std::vector<float> result(size_t(2 * frames - 1) * width, 0.f), divisors(width / 2);
    std::vector<std::vector<float>> positive(frames, std::vector<float>(width, 0.f));
    std::vector<std::vector<float>> negative(frames, std::vector<float>(width, 0.f));
    float logarithm = std::log(10000.f);
    for (int k = 0; k < width / 2; ++k) divisors[k] = std::exp(-(float(2 * k) * logarithm / float(width)));
    for (int t = 0; t < frames; ++t)
        for (int k = 0; k < width / 2; ++k) {
            positive[t][2 * k] = std::sin(float(t) * divisors[k]);
            positive[t][2 * k + 1] = std::cos(float(t) * divisors[k]);
            negative[t][2 * k] = std::sin(-float(t) * divisors[k]);
            negative[t][2 * k + 1] = std::cos(-float(t) * divisors[k]);
        }
    for (int t = 0; t < frames; ++t)
        for (int d = 0; d < width; ++d) result[t * width + d] = positive[frames - 1 - t][d];
    for (int t = 1; t < frames; ++t)
        for (int d = 0; d < width; ++d) result[(frames - 1 + t) * width + d] = negative[t][d];
    return result;
}
std::vector<float> CfgS3::encode(const std::vector<float>& input, int frames) {
    if (encoder_frames_ != frames) {
        encoder_ = std::make_unique<Graph>(backend_, 32768); encoder_frames_ = frames;
        auto* ctx = encoder_->context();
        auto* x = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, 512, frames);
        auto* pos1 = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, 512, 2 * frames - 1);
        auto* pos2 = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, 512, 4 * frames - 1);
        ggml_set_name(x, "input"); ggml_set_input(x);
        ggml_set_name(pos1, "pos1"); ggml_set_input(pos1); ggml_set_name(pos2, "pos2"); ggml_set_input(pos2);
        x = ggml_scale(ctx, norm(ctx, linear(ctx, x, "flow/encoder/embed/linear", "/w", "/b"), "flow/encoder/embed/norm", 1e-5f, "/w", "/b"), std::sqrt(512.f));
        auto* residual = x;
        auto* lookahead = convolution(ctx, pad(ctx, transpose(ctx, x), 0, 3), "flow/encoder/pre_lookahead/conv1", 1, 0, 1, "/w", "/b");
        lookahead = ggml_leaky_relu(ctx, lookahead, 0.01f, false);
        lookahead = convolution(ctx, pad(ctx, lookahead, 2, 0), "flow/encoder/pre_lookahead/conv2", 1, 0, 1, "/w", "/b");
        x = ggml_add(ctx, transpose(ctx, lookahead), residual);
        for (int i = 0; i < 6; ++i) x = conformer(ctx, x, pos1, "flow/encoder/block" + std::to_string(i), frames);
        auto* up = transpose(ctx, x);
        up = ggml_reshape_3d(ctx, up, 1, up->ne[0], up->ne[1]);
        auto* doubled = ggml_concat(ctx, up, up, 0);
        up = ggml_cont(ctx, ggml_reshape_2d(ctx, doubled, up->ne[1] * 2, up->ne[2]));
        up = convolution(ctx, pad(ctx, up, 4, 0), "flow/encoder/up_layer/conv", 1, 0, 1, "/w", "/b");
        x = linear(ctx, transpose(ctx, up), "flow/encoder/up_embed/linear", "/w", "/b");
        x = ggml_scale(ctx, norm(ctx, x, "flow/encoder/up_embed/norm", 1e-5f, "/w", "/b"), std::sqrt(512.f));
        for (int i = 0; i < 4; ++i) x = conformer(ctx, x, pos2, "flow/encoder/up_block" + std::to_string(i), 2 * frames);
        x = norm(ctx, x, "flow/encoder/after_norm", 1e-5f, "/w", "/b");
        finish(*encoder_, linear(ctx, x, "flow/encoder_proj", "/w", "/b"));
    }
    set(*encoder_, "pos1", positions(frames)); set(*encoder_, "pos2", positions(2 * frames));
    set(*encoder_, "input", input); encoder_->compute(); return encoder_->read("out");
}
ggml_tensor* CfgS3::causal(ggml_context* ctx, ggml_tensor* x, const std::string& name) const {
    auto* y = convolution(ctx, pad(ctx, x, 2, 0), name + "/block/0", 1, 0);
    y = transpose(ctx, norm(ctx, transpose(ctx, y), name + "/block/2"));
    return ggml_mul(ctx, y, ggml_tanh(ctx, ggml_unary(ctx, y, GGML_UNARY_OP_SOFTPLUS)));
}
ggml_tensor* CfgS3::resnet(ggml_context* ctx, ggml_tensor* x, ggml_tensor* time, const std::string& name) const {
    auto* hidden = causal(ctx, x, name + "/block1");
    auto* feature = ggml_mul(ctx, time, ggml_tanh(ctx, ggml_unary(ctx, time, GGML_UNARY_OP_SOFTPLUS)));
    auto* projected = linear(ctx, feature, name + "/mlp/1");
    hidden = ggml_add(ctx, hidden, ggml_reshape_2d(ctx, projected, 1, 256));
    hidden = causal(ctx, hidden, name + "/block2");
    return ggml_add(ctx, hidden, convolution(ctx, x, name + "/res_conv", 1, 0));
}
ggml_tensor* CfgS3::transformer(ggml_context* ctx, ggml_tensor* x, const std::string& name, int frames) const {
    auto* normalized = norm(ctx, x, name + "/norm1");
    auto projection = [&](const char* suffix) {
        auto* value = ggml_mul_mat(ctx, weight(name + suffix), normalized);
        return ggml_view_4d(ctx, value, 64, frames, 8, x->ne[2], 512 * sizeof(float), 64 * sizeof(float), size_t(512) * frames * sizeof(float), 0);
    };
    auto* query = projection("/attn1/to_q/weight"); auto* key = projection("/attn1/to_k/weight"); auto* value = projection("/attn1/to_v/weight");
    auto* attention = ggml_flash_attn_ext(ctx, query, key, value, nullptr, 1.f / std::sqrt(64.f), 0.f, 0.f);
    auto* flat = ggml_reshape_3d(ctx, attention, 512, frames, x->ne[2]);
    x = ggml_add(ctx, x, linear(ctx, flat, name + "/attn1/to_out/0"));
    auto* ff = ggml_gelu_erf(ctx, linear(ctx, norm(ctx, x, name + "/norm3"), name + "/ff/net/0/proj"));
    return ggml_add(ctx, x, linear(ctx, ff, name + "/ff/net/2"));
}
ggml_tensor* CfgS3::stack(ggml_context* ctx, ggml_tensor* x, const std::string& name, int frames) const {
    x = transpose(ctx, x);
    for (int i = 0; i < 4; ++i) x = transformer(ctx, x, name + "/" + std::to_string(i), frames);
    return transpose(ctx, x);
}
std::vector<float> CfgS3::time(float value) {
    if (!time_) {
        time_ = std::make_unique<Graph>(backend_, 128);
        auto* ctx = time_->context();
        auto* input = ggml_new_tensor_1d(ctx, GGML_TYPE_F32, 320);
        ggml_set_name(input, "input"); ggml_set_input(input);
        finish(*time_, linear(ctx, ggml_silu(ctx, linear(ctx, input, "cfm/time_mlp/linear_1")), "cfm/time_mlp/linear_2"));
    }
    std::vector<float> input(320);
    float factor = std::log(10000.f) / 159.f;
    for (int i = 0; i < 160; ++i) {
        float frequency = std::exp(-float(i) * factor), angle = 1000.f * value * frequency;
        input[i] = std::sin(angle); input[i + 160] = std::cos(angle);
    }
    set(*time_, "input", input); time_->compute(); return time_->read("out");
}
std::vector<float> CfgS3::estimate(const std::vector<float>& state, const std::vector<float>& mu,
    const std::vector<float>& time, const std::vector<float>& speaker, const std::vector<float>& condition, int frames) {
    if (estimator_frames_ != frames) {
        estimator_ = std::make_unique<Graph>(backend_, 65536); estimator_frames_ = frames;
        auto* ctx = estimator_->context();
        auto input = [&](const char* name, int width, int channels, int batches) {
            auto* value = ggml_new_tensor_3d(ctx, GGML_TYPE_F32, width, channels, batches);
            ggml_set_name(value, name); ggml_set_input(value); return value;
        };
        auto* x = input("x", frames, 80, 2); auto* mean = input("mu", frames, 80, 2);
        auto* spk = input("speaker", 80, 2, 1); auto* cond = input("condition", frames, 80, 2); auto* t = input("time", 1024, 1, 1);
        auto* repeated = ggml_repeat(ctx, ggml_reshape_3d(ctx, spk, 1, 80, 2), x);
        x = ggml_concat(ctx, ggml_concat(ctx, ggml_concat(ctx, x, mean, 1), repeated, 1), cond, 1);
        x = stack(ctx, resnet(ctx, x, t, "cfm/down_blocks/0/0"), "cfm/down_blocks/0/1", frames);
        auto* residual = x;
        x = convolution(ctx, pad(ctx, x, 2, 0), "cfm/down_blocks/0/2", 1, 0);
        for (int i = 0; i < 12; ++i) {
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
std::vector<float> CfgS3::pitch(const std::vector<float>& mel, int frames) const {
    Graph graph(backend_, 1024);
    auto* ctx = graph.context();
    auto* x = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, frames, 80);
    ggml_set_name(x, "mel"); ggml_set_input(x);
    for (int i = 0; i < 5; ++i)
        x = ggml_unary(ctx, convolution(ctx, x, "hift/f0_predictor/condnet/" + std::to_string(i * 2), 1, 1), GGML_UNARY_OP_ELU);
    x = ggml_reshape_1d(ctx, ggml_abs(ctx, linear(ctx, transpose(ctx, x), "hift/f0_predictor/classifier")), frames);
    finish(graph, x); set(graph, "mel", mel); graph.compute(); return graph.read("out");
}
std::vector<float> CfgS3::source(const std::vector<float>& pitch) const {
    int frames = int(pitch.size()) * 480;
    uint32_t seed = uint32_t(knobs_.seed + 1);
    std::mt19937 random(seed);
    std::uniform_real_distribution<float> uniform(-float(M_PI), float(M_PI));
    std::vector<float> phases(9, 0.f), waves(size_t(9) * frames, 0.f), result(frames);
    std::vector<double> accumulated(9, 0.0);
    for (int harmonic = 1; harmonic < 9; ++harmonic) phases[harmonic] = uniform(random);
    for (int t = 0; t < frames; ++t) {
        float frequency = pitch[t / 480]; bool voiced = frequency > 10.f;
        for (int h = 0; h < 9; ++h) {
            accumulated[h] += double(frequency) * (h + 1) / 24000.0;
            double angle = 2.0 * M_PI * (accumulated[h] - std::floor(accumulated[h]));
            float sine = 0.1f * std::sin(float(angle) + phases[h]);
            float amplitude = voiced ? 0.003f : 0.1f / 3.f;
            waves[size_t(h) * frames + t] = sine * (voiced ? 1.f : 0.f) + amplitude * S3Dsp::positioned_noise(seed, uint64_t(t) * 9 + h);
        }
    }
    for (int t = 0; t < frames; ++t) {
        float sum = source_bias_;
        for (int h = 0; h < 9; ++h) sum += source_weight_[h] * waves[size_t(h) * frames + t];
        result[t] = std::tanh(sum);
    }
    return result;
}
std::vector<float> CfgS3::stft(const std::vector<float>& signal) const {
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
std::vector<float> CfgS3::hift(const std::vector<float>& mel, int frames, const std::vector<float>& spectrum, int stft_frames) const {
    Graph graph(backend_, 131072);
    auto* ctx = graph.context();
    auto* input = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, frames, 80);
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
    auto residual = [&](ggml_tensor* x, const std::string& name, int kernel) {
        for (int i = 0; i < 3; ++i) {
            std::string index = std::to_string(i); int dilation = 2 * i + 1;
            auto* hidden = snake(x, name + "/activations1/" + index + "/alpha");
            hidden = convolution(ctx, hidden, name + "/convs1/" + index, 1, (kernel * dilation - dilation) / 2, dilation);
            hidden = snake(hidden, name + "/activations2/" + index + "/alpha");
            hidden = convolution(ctx, hidden, name + "/convs2/" + index, 1, (kernel - 1) / 2);
            x = ggml_add(ctx, x, hidden);
        }
        return x;
    };
    auto* x = convolution(ctx, input, "hift/conv_pre", 1, 3);
    const int rates[] = {8, 5, 3}, kernels[] = {16, 11, 7}, channels[] = {256, 128, 64};
    const int source_kernels[] = {7, 7, 11}, source_strides[] = {15, 3, 1}, source_padding[] = {7, 1, 0}, residual_kernels[] = {3, 7, 11};
    for (int i = 0; i < 3; ++i) {
        std::string index = std::to_string(i);
        x = ggml_leaky_relu(ctx, x, 0.1f, false);
        x = ggml_conv_transpose_1d(ctx, weight("hift/ups/" + index + "/weight"), x, rates[i], 0, 1);
        int padding = (kernels[i] - rates[i]) / 2;
        x = ggml_cont(ctx, ggml_view_3d(ctx, x, x->ne[0] - 2 * padding, x->ne[1], x->ne[2], x->nb[1], x->nb[2], padding * x->nb[0]));
        x = ggml_add(ctx, x, ggml_reshape_2d(ctx, weight("hift/ups/" + index + "/bias"), 1, channels[i]));
        if (i == 2) {
            auto* first = ggml_cont(ctx, ggml_view_3d(ctx, x, 1, x->ne[1], x->ne[2], x->nb[1], x->nb[2], x->nb[0]));
            x = ggml_concat(ctx, first, x, 0);
        }
        auto* injected = convolution(ctx, source, "hift/source_downs/" + index, source_strides[i], source_padding[i]);
        injected = residual(injected, "hift/source_resblocks/" + index, source_kernels[i]);
        x = ggml_add(ctx, x, injected);
        auto* combined = residual(x, "hift/resblocks/" + std::to_string(i * 3), residual_kernels[0]);
        for (int j = 1; j < 3; ++j) combined = ggml_add(ctx, combined, residual(x, "hift/resblocks/" + std::to_string(i * 3 + j), residual_kernels[j]));
        x = ggml_scale(ctx, combined, 1.f / 3.f);
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
std::vector<float> CfgS3::synthesize(const std::vector<int32_t>& speech) {
    auto tokens = prompt_tokens_;
    for (int32_t token : speech) if (token >= 0 && token < file_.u32("s3gen.speech_vocab_size")) tokens.push_back(token);
    std::vector<float> embedded(tokens.size() * 512);
    for (size_t i = 0; i < tokens.size(); ++i) std::memcpy(embedded.data() + i * 512, embeddings_.data() + size_t(tokens[i]) * 512, 512 * sizeof(float));
    auto encoded = encode(embedded, int(tokens.size()));
    int frames = 2 * int(tokens.size()), prompt_frames = int(prompt_features_.size() / 80);
    std::vector<float> mu(frames * 80), condition(frames * 80, 0.f), state(frames * 80), speaker(80);
    for (int m = 0; m < 80; ++m)
        for (int t = 0; t < frames; ++t) {
            mu[m * frames + t] = encoded[t * 80 + m];
            bool generated = t >= prompt_frames;
            int64_t frame = generated ? t - prompt_frames : t;
            state[m * frames + t] = S3Dsp::positioned_noise(knobs_.seed + (generated ? 2 : 0), frame * 80 + m);
        }
    float norm = 0.f;
    for (int i = 0; i < 192; ++i) norm += speaker_[i] * speaker_[i];
    norm = std::sqrt(norm + 1e-12f);
    std::vector<float> normalized(192);
    for (int i = 0; i < 192; ++i) normalized[i] = speaker_[i] / norm;
    for (int m = 0; m < 80; ++m) {
        float accumulator = speaker_bias_[m];
        for (int i = 0; i < 192; ++i) accumulator += speaker_weight_[m * 192 + i] * normalized[i];
        speaker[m] = accumulator;
        for (int t = 0; t < prompt_frames; ++t) condition[m * frames + t] = prompt_features_[t * 80 + m];
    }
    std::vector<float> times(knobs_.cfm_steps + 1);
    for (int i = 0; i <= knobs_.cfm_steps; ++i) {
        float fraction = float(i) / float(knobs_.cfm_steps);
        times[i] = 1.f - std::cos(fraction * 0.5f * float(M_PI));
    }
    std::vector<float> doubled(state.size() * 2), mu2(mu.size() * 2, 0.f);
    std::vector<float> condition2(condition.size() * 2, 0.f), speaker2(160, 0.f);
    for (int step = 0; step < knobs_.cfm_steps; ++step) {
        std::copy(state.begin(), state.end(), doubled.begin());
        std::copy(state.begin(), state.end(), doubled.begin() + state.size());
        std::copy(mu.begin(), mu.end(), mu2.begin());
        std::copy(condition.begin(), condition.end(), condition2.begin());
        std::copy(speaker.begin(), speaker.end(), speaker2.begin());
        auto delta = estimate(doubled, mu2, time(times[step]), speaker2, condition2, frames);
        float dt = times[step + 1] - times[step];
        for (size_t i = 0; i < state.size(); ++i)
            state[i] += dt * ((1.f + knobs_.cfm_cfg) * delta[i] - knobs_.cfm_cfg * delta[i + state.size()]);
    }
    int mel_frames = frames - prompt_frames;
    std::vector<float> mel(80 * mel_frames);
    for (int m = 0; m < 80; ++m)
        for (int t = 0; t < mel_frames; ++t) mel[m * mel_frames + t] = state[m * frames + t + prompt_frames];
    auto spectrum = stft(source(pitch(mel, mel_frames)));
    return hift(mel, mel_frames, spectrum, int(spectrum.size() / 18));
}
}
