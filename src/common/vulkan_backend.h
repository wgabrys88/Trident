#pragma once
#include "ggml.h"
#include "ggml-backend.h"
#include "ggml-alloc.h"
#include <memory>
#include <vector>

namespace trident {
using Context = std::unique_ptr<ggml_context, decltype(&ggml_free)>;
using Buffer = std::unique_ptr<ggml_backend_buffer, decltype(&ggml_backend_buffer_free)>;
using Allocator = std::unique_ptr<ggml_gallocr, decltype(&ggml_gallocr_free)>;

class VulkanBackend {
    std::unique_ptr<ggml_backend, decltype(&ggml_backend_free)> backend_;
public:
    explicit VulkanBackend(int device);
    ggml_backend_t get() const { return backend_.get(); }
    void compute(ggml_cgraph* graph) const;
};

class Graph {
    Context context_;
    Allocator allocator_;
    const VulkanBackend& backend_;
public:
    ggml_cgraph* graph;
    Graph(const VulkanBackend&, size_t nodes);
    ggml_context* context() const { return context_.get(); }
    void allocate();
    void compute() const;
    ggml_tensor* tensor(const char* name) const;
    void set(const char* name, const void* data, size_t bytes);
    std::vector<float> read(const char* name) const;
};
}
