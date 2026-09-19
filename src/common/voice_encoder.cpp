#include "voice_encoder.h"
#include <algorithm>
#include <cmath>
#include <cstring>

namespace trident {
VoiceEncoder::VoiceEncoder(const std::string& path, const VulkanBackend& backend)
    : backend_(backend), file_(path), weights_(file_, backend, false, "voice_encoder/"),
      layers_(file_.u32("voice_encoder.num_layers")), mels_(file_.u32("voice_encoder.n_mels")),
      hidden_(file_.u32("voice_encoder.hidden_size")), embedding_(file_.u32("voice_encoder.embedding_size")),
      partial_(file_.u32("voice_encoder.partial_frames")), rate_(file_.u32("voice_encoder.sample_rate")),
      windows_per_second_(file_.f32("voice_encoder.rate")), coverage_(file_.f32("voice_encoder.min_coverage")),
      filters_(file_.floats("voice_encoder/mel_fb")) {}

std::vector<float> VoiceEncoder::embed(const Audio& audio) const {
    auto mel = audio.mel(filters_, backend_, 400, 160, mels_, true, 2.f, -1.f);
    int frames = int(mel.size() / mels_);
    int step = int(std::lround((double(rate_) / double(windows_per_second_)) / double(partial_)));
    int extent = std::max(frames - partial_ + step, 0), windows = extent / step;
    int remainder = extent - windows * step;
    if (windows == 0 || double(remainder + partial_ - step) / partial_ >= double(coverage_)) ++windows;
    mel.resize(size_t(partial_ + step * (windows - 1)) * mels_, 0.f);
    Graph graph(backend_, 32 * partial_ * layers_ + 256);
    auto* ctx = graph.context();
    auto* input = ggml_new_tensor_3d(ctx, GGML_TYPE_F32, mels_, partial_, windows);
    auto* h0 = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, hidden_, windows);
    auto* c0 = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, hidden_, windows);
    ggml_set_name(input, "x"); ggml_set_input(input);
    ggml_set_name(h0, "h0"); ggml_set_input(h0);
    ggml_set_name(c0, "c0"); ggml_set_input(c0);
    auto* sequence = input;
    for (int layer = 0; layer < layers_; ++layer) {
        auto weight = [&](const char* name) { return weights_.at("voice_encoder/lstm/" + std::string(name) + "_l" + std::to_string(layer)); };
        auto* gates_sequence = ggml_add(ctx, ggml_mul_mat(ctx, weight("weight_ih"), sequence), weight("bias_ih"));
        bool keep_sequence = layer + 1 < layers_;
        auto* output_sequence = keep_sequence ? ggml_new_tensor_3d(ctx, GGML_TYPE_F32, hidden_, partial_, windows) : nullptr;
        auto* previous_hidden = h0;
        auto* previous_cell = c0;
        for (int time = 0; time < partial_; ++time) {
            auto* input_gates = ggml_view_2d(ctx, gates_sequence, 4 * hidden_, windows,
                size_t(4 * hidden_) * partial_ * sizeof(float), size_t(4 * hidden_) * time * sizeof(float));
            auto* recurrent_gates = ggml_add(ctx, ggml_mul_mat(ctx, weight("weight_hh"), previous_hidden), weight("bias_hh"));
            auto* gates = ggml_add(ctx, input_gates, recurrent_gates);
            auto gate = [&](int index) { return ggml_view_2d(ctx, gates, hidden_, windows,
                size_t(4 * hidden_) * sizeof(float), size_t(index) * hidden_ * sizeof(float)); };
            auto* input_gate = ggml_sigmoid(ctx, gate(0));
            auto* forget_gate = ggml_sigmoid(ctx, gate(1));
            auto* candidate = ggml_tanh(ctx, gate(2));
            auto* output_gate = ggml_sigmoid(ctx, gate(3));
            auto* cell = ggml_add(ctx, ggml_mul(ctx, forget_gate, previous_cell), ggml_mul(ctx, input_gate, candidate));
            auto* hidden = ggml_mul(ctx, output_gate, ggml_tanh(ctx, cell));
            if (keep_sequence) {
                auto* destination = ggml_view_2d(ctx, output_sequence, hidden_, windows,
                    size_t(hidden_) * partial_ * sizeof(float), size_t(hidden_) * time * sizeof(float));
                ggml_build_forward_expand(graph.graph, ggml_cpy(ctx, hidden, destination));
            }
            previous_hidden = hidden; previous_cell = cell;
        }
        sequence = keep_sequence ? output_sequence : previous_hidden;
    }
    auto* embedding = ggml_relu(ctx, ggml_add(ctx, ggml_mul_mat(ctx, weights_.at("voice_encoder/proj/weight"), sequence),
                                             weights_.at("voice_encoder/proj/bias")));
    ggml_set_name(embedding, "embedding"); ggml_set_output(embedding);
    ggml_build_forward_expand(graph.graph, embedding);
    graph.allocate();
    std::vector<float> input_data(size_t(mels_) * partial_ * windows), zeros(size_t(hidden_) * windows, 0);
    for (int i = 0; i < windows; ++i)
        std::memcpy(input_data.data() + size_t(i) * partial_ * mels_, mel.data() + size_t(i) * step * mels_,
                    size_t(partial_) * mels_ * sizeof(float));
    graph.set("x", input_data.data(), input_data.size() * sizeof(float));
    graph.set("h0", zeros.data(), zeros.size() * sizeof(float));
    graph.set("c0", zeros.data(), zeros.size() * sizeof(float));
    graph.compute();
    auto embeddings = graph.read("embedding");
    std::vector<float> result(embedding_, 0);
    for (int i = 0; i < windows; ++i) {
        float* values = embeddings.data() + size_t(i) * embedding_;
        double square = 0;
        for (int j = 0; j < embedding_; ++j) square += double(values[j]) * double(values[j]);
        double norm = std::sqrt(square);
        if (norm > 1e-12) {
            float scale = float(1.0 / norm);
            for (int j = 0; j < embedding_; ++j) values[j] *= scale;
        }
        for (int j = 0; j < embedding_; ++j) result[j] += values[j];
    }
    float inverse_windows = 1.f / windows;
    double square = 0;
    for (auto& value : result) { value *= inverse_windows; square += double(value) * double(value); }
    double norm = std::sqrt(square);
    if (norm > 1e-12) {
        float scale = float(1.0 / norm);
        for (auto& value : result) value *= scale;
    }
    return result;
}
}
