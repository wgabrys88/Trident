#pragma once
#include "vulkan_backend.h"
#include "gguf.h"
#include <string>
#include <unordered_map>

namespace trident {
struct TensorReplacement {
    std::string name;
    ggml_type type;
    std::vector<int64_t> shape;
    const void* data;
};
class GgufFile {
    Context context_;
    std::unique_ptr<gguf_context, decltype(&gguf_free)> file_;
public:
    explicit GgufFile(const std::string& path);
    gguf_context* get() const { return file_.get(); }
    int64_t key(const char* name) const;
    uint32_t u32(const char* name) const;
    float f32(const char* name) const;
    std::string string(const char* name) const;
    std::vector<std::string> strings(const char* name) const;
    ggml_tensor* tensor(const char* name) const;
    std::vector<float> floats(const char* name) const;
    void rewrite(const std::string& path, const std::vector<TensorReplacement>&,
                 const std::vector<std::pair<std::string, uint32_t>>&) const;
};

class Weights {
    Context context_;
    Buffer buffer_;
    std::unordered_map<std::string, ggml_tensor*> tensors_;
public:
    Weights(const GgufFile&, const VulkanBackend&, bool expand_convolutions = false, const std::string& prefix = "");
    ggml_tensor* at(const std::string& name) const { return tensors_.at(name); }
};
}
