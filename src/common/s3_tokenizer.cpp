#include "s3_tokenizer.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <numeric>

namespace trident {
S3Tokenizer::S3Tokenizer(const std::string& path, const VulkanBackend& backend)
    : backend_(backend), file_(path), weights_(file_, backend, false, "s3tokv2/"),
      mels_(file_.u32("s3tokv2.n_mels")), width_(file_.u32("s3tokv2.n_audio_state")),
      heads_(file_.u32("s3tokv2.n_audio_head")), layers_(file_.u32("s3tokv2.n_audio_layer")),
      head_dim_(file_.u32("s3tokv2.n_audio_state") / file_.u32("s3tokv2.n_audio_head")), kernel_(file_.u32("s3tokv2.fsmn_kernel")),
      stride_(file_.u32("s3tokv2.conv_stride")), fft_(file_.u32("s3tokv2.n_fft")), hop_(file_.u32("s3tokv2.hop")),
      dimensions_(file_.u32("s3tokv2.fsq_dim")), levels_(file_.u32("s3tokv2.fsq_levels")),
      max_position_(file_.u32("s3tokv2.rope_max_pos")), theta_(file_.f32("s3tokv2.rope_theta")) {}
ggml_tensor* S3Tokenizer::convolution(ggml_context* ctx, ggml_tensor* weight, ggml_tensor* input,
    int stride, int padding) const {
    auto* columns = ggml_im2col(ctx, weight, input, stride, 0, padding, 0, 1, 0, false, GGML_TYPE_F32);
    auto* result = ggml_mul_mat(ctx,
        ggml_reshape_2d(ctx, columns, columns->ne[0], columns->ne[2] * columns->ne[1]),
        ggml_reshape_2d(ctx, weight, weight->ne[0] * weight->ne[1], weight->ne[2]));
    return ggml_reshape_3d(ctx, result, columns->ne[1], weight->ne[2], columns->ne[2]);
}
ggml_tensor* S3Tokenizer::encoder(Graph& graph, int frames, ggml_tensor* positions) const {
    auto* ctx = graph.context();
    auto tensor = [&](const std::string& name) { return weights_.at("s3tokv2/encoder/" + name); };
    auto* signal = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, frames, mels_);
    ggml_set_name(signal, "mel"); ggml_set_input(signal);
    for (int i = 1; i <= 2; ++i) {
        std::string name = "conv" + std::to_string(i);
        signal = convolution(ctx, tensor(name + "/weight"), signal, stride_, 1);
        signal = ggml_gelu_erf(ctx, ggml_add(ctx, signal, ggml_reshape_2d(ctx, tensor(name + "/bias"), 1, width_)));
    }
    auto* hidden = ggml_cont(ctx, ggml_transpose(ctx, signal));
    for (int layer = 0; layer < layers_; ++layer) {
        std::string prefix = "blocks/" + std::to_string(layer) + "/";
        auto weight = [&](const std::string& name) { return tensor(prefix + name); };
        auto linear = [&](ggml_tensor* x, const std::string& name) {
            return ggml_add(ctx, ggml_mul_mat(ctx, weight(name + "/weight"), x), weight(name + "/bias"));
        };
        auto norm = [&](ggml_tensor* x, const std::string& name) {
            return ggml_add(ctx, ggml_mul(ctx, ggml_norm(ctx, x, 1e-5f), weight(name + "/weight")), weight(name + "/bias"));
        };
        auto* normalized = norm(hidden, "attn_ln");
        auto* query = linear(normalized, "attn/query");
        auto* key = ggml_mul_mat(ctx, weight("attn/key/weight"), normalized);
        auto* value = linear(normalized, "attn/value");
        int time = int(query->ne[1]);
        query = ggml_reshape_3d(ctx, query, head_dim_, heads_, time);
        key = ggml_reshape_3d(ctx, key, head_dim_, heads_, time);
        value = ggml_reshape_3d(ctx, value, head_dim_, heads_, time);
        query = ggml_rope_ext(ctx, query, positions, nullptr, head_dim_, GGML_ROPE_TYPE_NEOX,
            max_position_, theta_, 1.f, 0.f, 1.f, 32.f, 1.f);
        key = ggml_rope_ext(ctx, key, positions, nullptr, head_dim_, GGML_ROPE_TYPE_NEOX,
            max_position_, theta_, 1.f, 0.f, 1.f, 32.f, 1.f);
        auto* flat_value = ggml_reshape_2d(ctx, ggml_cont(ctx, value), width_, time);
        auto* transposed = ggml_cont(ctx, ggml_transpose(ctx, flat_value));
        auto* fsmn_weight = weight("attn/fsmn_block/weight");
        auto* reshaped = ggml_reshape_4d(ctx, transposed, transposed->ne[0], 1, transposed->ne[1], transposed->ne[2]);
        auto* columns = ggml_im2col(ctx, fsmn_weight, reshaped, 1, 0, (kernel_ - 1) / 2, 0, 1, 0, false, GGML_TYPE_F32);
        auto* memory = ggml_mul_mat(ctx, columns, fsmn_weight);
        memory = ggml_reshape_3d(ctx, memory, memory->ne[0], memory->ne[2], 1);
        memory = ggml_cont(ctx, ggml_transpose(ctx, ggml_add(ctx, memory, transposed)));
        query = ggml_cont(ctx, ggml_permute(ctx, query, 0, 2, 1, 3));
        key = ggml_cont(ctx, ggml_permute(ctx, key, 0, 2, 1, 3));
        value = ggml_cont(ctx, ggml_permute(ctx, value, 1, 2, 0, 3));
        auto* scores = ggml_soft_max(ctx, ggml_scale(ctx, ggml_mul_mat(ctx, key, query), 1.f / std::sqrt(float(head_dim_))));
        auto* attention = ggml_mul_mat(ctx, value, scores);
        attention = ggml_reshape_2d(ctx, ggml_cont(ctx, ggml_permute(ctx, attention, 0, 2, 1, 3)), width_, time);
        hidden = ggml_add(ctx, hidden, ggml_add(ctx, linear(attention, "attn/out"), memory));
        auto* mlp = ggml_gelu_erf(ctx, linear(norm(hidden, "mlp_ln"), "mlp/0"));
        hidden = ggml_add(ctx, hidden, linear(mlp, "mlp/2"));
    }
    return hidden;
}
std::vector<int32_t> S3Tokenizer::tokenize(const Audio& audio, int max_tokens) const {
    auto mel = audio.mel(file_.floats("s3tokv2/mel_fb"), backend_, fft_, hop_, mels_, true, 2.f, -1.f);
    int frames = int(mel.size() / mels_) - 1;
    std::vector<float> input(size_t(frames) * mels_);
    float maximum = -std::numeric_limits<float>::infinity(), inverse_log = 1.f / std::log(10.f);
    for (int t = 0; t < frames; ++t)
        for (int m = 0; m < mels_; ++m) {
            float value = std::log(std::max(mel[t * mels_ + m], 1e-10f)) * inverse_log;
            input[m * frames + t] = value;
            maximum = std::max(maximum, value);
        }
    for (auto& value : input) value = (std::max(value, maximum - 8.f) + 4.f) / 4.f;
    int time1 = (frames - 1) / 2 + 1, time2 = (time1 - 1) / 2 + 1;
    Graph graph(backend_, 4096);
    auto* positions = ggml_new_tensor_1d(graph.context(), GGML_TYPE_I32, time2);
    ggml_set_name(positions, "positions"); ggml_set_input(positions);
    auto* output = encoder(graph, frames, positions);
    ggml_set_name(output, "output"); ggml_set_output(output); ggml_build_forward_expand(graph.graph, output);
    graph.allocate();
    std::vector<int32_t> indices(time2);
    std::iota(indices.begin(), indices.end(), 0);
    graph.set("mel", input.data(), input.size() * sizeof(float));
    graph.set("positions", indices.data(), indices.size() * sizeof(int32_t));
    graph.compute();
    auto hidden = graph.read("output");
    auto projection = file_.floats("s3tokv2/quantizer/_codebook/project_down/weight");
    auto bias = file_.floats("s3tokv2/quantizer/_codebook/project_down/bias");
    std::vector<int32_t> tokens(time2);
    for (int t = 0; t < time2; ++t) {
        int32_t code = 0, power = 1;
        for (int o = 0; o < dimensions_; ++o) {
            float accumulator = bias[o];
            for (int d = 0; d < width_; ++d) accumulator += projection[o * width_ + d] * hidden[t * width_ + d];
            float quantized = std::tanh(accumulator) * 0.9990000128746033f;
            int32_t digit = std::clamp(int32_t(std::lround(quantized)) + 1, 0, levels_ - 1);
            code += digit * power; power *= levels_;
        }
        tokens[t] = code;
    }
    if (max_tokens > 0 && int(tokens.size()) > max_tokens) tokens.resize(max_tokens);
    return tokens;
}
}
