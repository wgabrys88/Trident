#include "campplus.h"
#include <algorithm>
#include <cmath>

namespace trident {
CampPlus::CampPlus(const std::string& path, const VulkanBackend& backend)
    : backend_(backend), file_(path), weights_(file_, backend, false, "campplus/"),
      features_(int(file_.tensor("campplus/mel_fb_kaldi_80")->ne[1])), segment_(file_.u32("campplus.seg_pool_len")) {}
std::vector<float> CampPlus::conv1(const std::vector<float>& values, int time, const std::string& name,
    int kernel, int stride, int padding, int dilation, bool bias) const {
    Graph graph(backend_, 32);
    auto* ctx = graph.context();
    int channels = int(values.size() / time);
    auto* weight = weights_.at("campplus/" + name + "/weight");
    int output_channels = int(ggml_nelements(weight) / (kernel * channels));
    weight = ggml_reshape_3d(ctx, weight, kernel, channels, output_channels);
    auto* input = ggml_new_tensor_3d(ctx, GGML_TYPE_F32, time, channels, 1);
    ggml_set_name(input, "input"); ggml_set_input(input);
    auto* columns = ggml_im2col(ctx, weight, input, stride, 0, padding, 0, dilation, 0, false, GGML_TYPE_F32);
    auto* output = ggml_mul_mat(ctx,
        ggml_reshape_2d(ctx, columns, columns->ne[0], columns->ne[2] * columns->ne[1]),
        ggml_reshape_2d(ctx, weight, weight->ne[0] * weight->ne[1], weight->ne[2]));
    output = ggml_reshape_3d(ctx, output, columns->ne[1], weight->ne[2], columns->ne[2]);
    if (bias) output = ggml_add(ctx, output, ggml_reshape_2d(ctx, weights_.at("campplus/" + name + "/bias"), 1, output_channels));
    ggml_set_name(output, "output"); ggml_set_output(output); ggml_build_forward_expand(graph.graph, output);
    graph.allocate(); graph.set("input", values.data(), values.size() * sizeof(float)); graph.compute();
    return graph.read("output");
}
std::vector<float> CampPlus::conv2(const std::vector<float>& values, int height, int time,
    const std::string& name, int stride, int padding) const {
    Graph graph(backend_, 48);
    auto* ctx = graph.context();
    auto* weight = weights_.at("campplus/" + name + "/weight");
    auto* input = ggml_new_tensor_4d(ctx, GGML_TYPE_F32, time, height, int(values.size() / (height * time)), 1);
    ggml_set_name(input, "input"); ggml_set_input(input);
    auto* columns = ggml_im2col(ctx, weight, input, 1, stride, padding, padding, 1, 1, true, GGML_TYPE_F32);
    auto* output = ggml_mul_mat(ctx,
        ggml_reshape_2d(ctx, columns, columns->ne[0], columns->ne[3] * columns->ne[2] * columns->ne[1]),
        ggml_reshape_2d(ctx, weight, weight->ne[0] * weight->ne[1] * weight->ne[2], weight->ne[3]));
    output = ggml_reshape_4d(ctx, output, columns->ne[1], columns->ne[2], columns->ne[3], weight->ne[3]);
    output = ggml_cont(ctx, ggml_permute(ctx, output, 0, 1, 3, 2));
    ggml_set_name(output, "output"); ggml_set_output(output); ggml_build_forward_expand(graph.graph, output);
    graph.allocate(); graph.set("input", values.data(), values.size() * sizeof(float)); graph.compute();
    return graph.read("output");
}
void CampPlus::norm(std::vector<float>& values, int time, const std::string& name, bool relu) const {
    auto scale = file_.floats(("campplus/" + name + "/s").c_str());
    auto shift = file_.floats(("campplus/" + name + "/b").c_str());
    for (size_t channel = 0; channel < scale.size(); ++channel)
        for (int t = 0; t < time; ++t) {
            auto& value = values[channel * time + t];
            value = value * scale[channel] + shift[channel];
            if (relu && value < 0.f) value = 0.f;
        }
}
std::vector<float> CampPlus::residual(const std::vector<float>& input, int height, int time,
    const std::string& name, int stride) const {
    int output_height = (height + 2 - 3) / stride + 1;
    auto first = conv2(input, height, time, name + "/conv1", stride);
    norm(first, output_height * time, name + "/bn1", true);
    auto result = conv2(first, output_height, time, name + "/conv2");
    norm(result, output_height * time, name + "/bn2", false);
    auto shortcut = input;
    if (stride == 2) {
        shortcut = conv2(input, height, time, name + "/shortcut/0", stride, 0);
        norm(shortcut, output_height * time, name + "/shortcut/1", false);
    }
    for (size_t i = 0; i < result.size(); ++i) result[i] = std::max(result[i] + shortcut[i], 0.f);
    return result;
}
std::vector<float> CampPlus::embed(const Audio& audio) const {
    auto features = audio.kaldi(file_.floats("campplus/mel_fb_kaldi_80"), backend_);
    int time = int(features.size() / features_);
    std::vector<float> means(features_, 0), values(features.size());
    for (int t = 0; t < time; ++t)
        for (int c = 0; c < features_; ++c) means[c] += features[t * features_ + c];
    for (auto& mean : means) mean /= float(time);
    for (int t = 0; t < time; ++t)
        for (int c = 0; c < features_; ++c) values[c * time + t] = features[t * features_ + c] - means[c];
    int height = features_;
    values = conv2(values, height, time, "head/conv1");
    norm(values, height * time, "head/bn1", true);
    for (int layer = 1;; ++layer) {
        int blocks = weights_.count("campplus/head/layer" + std::to_string(layer) + "/", "/conv1/weight");
        if (blocks == 0) break;
        for (int block = 0; block < blocks; ++block) {
            int stride = block == 0 ? 2 : 1;
            values = residual(values, height, time, "head/layer" + std::to_string(layer) + "/" + std::to_string(block), stride);
            height = (height - 1) / stride + 1;
        }
    }
    values = conv2(values, height, time, "head/conv2", 2);
    height = (height - 1) / 2 + 1;
    norm(values, height * time, "head/bn2", true);
    values = conv1(values, time, "xvector/tdnn/linear", 5, 2, 2);
    time = (time - 1) / 2 + 1;
    norm(values, time, "xvector/tdnn/nonlinear/batchnorm", true);
    int kernel = int(weights_.at("campplus/xvector/block1/tdnnd1/cam_layer/linear_local/weight")->ne[0]);
    for (int block = 1;; ++block) {
        std::string number = std::to_string(block), base = "xvector/block" + number;
        int layers = weights_.count("campplus/" + base + "/", "/cam_layer/linear_local/weight");
        if (layers == 0) break;
        int dilation = file_.u32(("campplus.block" + number + "_dilation").c_str());
        for (int layer = 1; layer <= layers; ++layer) {
            std::string name = base + "/tdnnd" + std::to_string(layer);
            auto hidden = values;
            norm(hidden, time, name + "/nonlinear1/batchnorm", true);
            hidden = conv1(hidden, time, name + "/linear1");
            norm(hidden, time, name + "/nonlinear2/batchnorm", true);
            auto local = conv1(hidden, time, name + "/cam_layer/linear_local", kernel, 1, (kernel - 1) / 2 * dilation, dilation);
            std::vector<float> context(hidden.size());
            for (size_t channel = 0; channel < hidden.size() / time; ++channel) {
                double sum = 0;
                for (int t = 0; t < time; ++t) sum += hidden[channel * time + t];
                float mean = float(sum / time);
                for (int start = 0; start < time; start += segment_) {
                    int end = std::min(time, start + segment_);
                    float segment_sum = 0;
                    for (int t = start; t < end; ++t) segment_sum += hidden[channel * time + t];
                    float segment_mean = segment_sum / (end - start);
                    for (int t = start; t < end; ++t) context[channel * time + t] = segment_mean + mean;
                }
            }
            context = conv1(context, time, name + "/cam_layer/linear1", 1, 1, 0, 1, true);
            for (auto& value : context) if (value < 0.f) value = 0.f;
            auto gate = conv1(context, time, name + "/cam_layer/linear2", 1, 1, 0, 1, true);
            for (size_t i = 0; i < local.size(); ++i) local[i] *= 1.f / (1.f + std::exp(-gate[i]));
            values.insert(values.end(), local.begin(), local.end());
        }
        norm(values, time, "xvector/transit" + number + "/nonlinear/batchnorm", true);
        values = conv1(values, time, "xvector/transit" + number + "/linear");
    }
    norm(values, time, "xvector/out_nonlinear/batchnorm", true);
    int channels = int(values.size() / time);
    std::vector<float> stats(2 * channels);
    for (int c = 0; c < channels; ++c) {
        double sum = 0, square = 0;
        for (int t = 0; t < time; ++t) sum += values[c * time + t];
        double mean = sum / time;
        for (int t = 0; t < time; ++t) { double delta = values[c * time + t] - mean; square += delta * delta; }
        stats[c] = float(mean); stats[channels + c] = float(std::sqrt(square / std::max(1, time - 1)));
    }
    auto result = conv1(stats, 1, "xvector/dense/linear");
    norm(result, 1, "xvector/dense/nonlinear/batchnorm", false);
    return result;
}
}
