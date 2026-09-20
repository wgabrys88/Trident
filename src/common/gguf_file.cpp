#include "gguf_file.h"
#include <cstring>
#include <memory>
#include <stdexcept>
#include <filesystem>
#include <windows.h>

namespace trident {
GgufFile::GgufFile(const std::string& path)
    : context_(nullptr, ggml_free), file_(nullptr, gguf_free) {
    ggml_context* context = nullptr;
    file_.reset(gguf_init_from_file(path.c_str(), {false, &context}));
    context_.reset(context);
    if (!file_) throw std::runtime_error("Cannot open GGUF: " + path);
}
std::string GgufFile::architecture(const std::string& path) {
    std::unique_ptr<gguf_context, decltype(&gguf_free)> file(gguf_init_from_file(path.c_str(), {true, nullptr}), gguf_free);
    if (!file) throw std::runtime_error("Cannot open GGUF: " + path);
    auto index = gguf_find_key(file.get(), "general.architecture");
    if (index < 0) throw std::runtime_error("Missing GGUF key: general.architecture");
    return gguf_get_val_str(file.get(), index);
}
int64_t GgufFile::key(const char* name) const {
    auto index = gguf_find_key(get(), name);
    if (index < 0) throw std::runtime_error(std::string("Missing GGUF key: ") + name);
    return index;
}
uint32_t GgufFile::u32(const char* name) const { return gguf_get_val_u32(get(), key(name)); }
float GgufFile::f32(const char* name) const { return gguf_get_val_f32(get(), key(name)); }
std::string GgufFile::string(const char* name) const { return gguf_get_val_str(get(), key(name)); }
std::vector<std::string> GgufFile::strings(const char* name) const {
    auto index = key(name);
    std::vector<std::string> result;
    for (size_t i = 0; i < gguf_get_arr_n(get(), index); ++i)
        result.emplace_back(gguf_get_arr_str(get(), index, i));
    return result;
}
std::vector<int32_t> GgufFile::ints(const char* name) const {
    auto index = key(name);
    size_t count = gguf_get_arr_n(get(), index);
    auto* data = static_cast<const int32_t*>(gguf_get_arr_data(get(), index));
    return {data, data + count};
}
ggml_tensor* GgufFile::tensor(const char* name) const {
    auto* result = ggml_get_tensor(context_.get(), name);
    if (!result) throw std::runtime_error(std::string("Missing GGUF tensor: ") + name);
    return result;
}
std::vector<float> GgufFile::floats(const char* name) const {
    auto* value = tensor(name);
    if (value->type != GGML_TYPE_F32) throw std::runtime_error(std::string("Expected F32 tensor: ") + name);
    std::vector<float> result(ggml_nelements(value));
    std::memcpy(result.data(), ggml_get_data(value), result.size() * sizeof(float));
    return result;
}
void GgufFile::rewrite(const std::string& path, const std::vector<TensorReplacement>& replacements,
    const std::vector<std::pair<std::string, uint32_t>>& metadata) const {
    Context context(ggml_init({ggml_tensor_overhead() * (replacements.size() + 4), nullptr, true}), ggml_free);
    std::unique_ptr<gguf_context, decltype(&gguf_free)> output(gguf_init_empty(), gguf_free);
    gguf_set_kv(output.get(), get());
    for (const auto& item : metadata) gguf_set_val_u32(output.get(), item.first.c_str(), item.second);
    std::unordered_map<std::string, const TensorReplacement*> by_name;
    for (const auto& replacement : replacements) {
        tensor(replacement.name.c_str());
        by_name.emplace(replacement.name, &replacement);
    }
    for (int64_t i = 0; i < gguf_get_n_tensors(get()); ++i) {
        const char* name = gguf_get_tensor_name(get(), i);
        auto entry = by_name.find(name);
        if (entry == by_name.end()) gguf_add_tensor(output.get(), tensor(name));
        else {
            const auto& replacement = *entry->second;
            auto* value = ggml_new_tensor(context.get(), replacement.type, int(replacement.shape.size()), replacement.shape.data());
            ggml_set_name(value, name); gguf_add_tensor(output.get(), value);
            gguf_set_tensor_data(output.get(), name, replacement.data);
        }
    }
    std::string temporary = path + ".tmp";
    if (!gguf_write_to_file(output.get(), temporary.c_str(), false)) throw std::runtime_error("GGUF write failed: " + path);
    if (!MoveFileExW(std::filesystem::u8path(temporary).c_str(), std::filesystem::u8path(path).c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
        throw std::runtime_error("GGUF replace failed: " + path);
}
int Weights::count(const std::string& prefix, const std::string& suffix) const {
    int result = 0;
    for (const auto& entry : tensors_) {
        const auto& name = entry.first;
        result += name.size() >= suffix.size() && name.compare(0, prefix.size(), prefix) == 0
            && name.compare(name.size() - suffix.size(), suffix.size(), suffix) == 0;
    }
    return result;
}
Weights::Weights(const GgufFile& file, const VulkanBackend& backend, bool expand_convolutions, const std::string& prefix)
    : context_(ggml_init({ggml_tensor_overhead() * size_t(gguf_get_n_tensors(file.get())), nullptr, true}), ggml_free),
      buffer_(nullptr, ggml_backend_buffer_free) {
    for (int64_t i = 0; i < gguf_get_n_tensors(file.get()); ++i) {
        const char* name = gguf_get_tensor_name(file.get(), i);
        if (std::string(name).compare(0, prefix.size(), prefix) != 0) continue;
        auto* source = file.tensor(name);
        auto* target = expand_convolutions && source->type == GGML_TYPE_F16 && ggml_is_3d(source)
            ? ggml_new_tensor(context_.get(), GGML_TYPE_F32, ggml_n_dims(source), source->ne)
            : ggml_dup_tensor(context_.get(), source);
        ggml_set_name(target, name);
        tensors_.emplace(name, target);
    }
    buffer_.reset(ggml_backend_alloc_ctx_tensors(context_.get(), backend.get()));
    for (const auto& entry : tensors_) {
        auto* source = file.tensor(entry.first.c_str());
        auto* target = entry.second;
        if (source->type == target->type) {
            ggml_backend_tensor_set(target, ggml_get_data(source), 0, ggml_nbytes(source));
        } else {
            std::vector<float> expanded(ggml_nelements(source));
            ggml_fp16_to_fp32_row(static_cast<const ggml_fp16_t*>(ggml_get_data(source)), expanded.data(), expanded.size());
            ggml_backend_tensor_set(target, expanded.data(), 0, expanded.size() * sizeof(float));
        }
    }
}
}
