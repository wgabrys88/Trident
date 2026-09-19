#include "vulkan_backend.h"
#include "ggml-vulkan.h"
#include <stdexcept>

namespace trident {
VulkanBackend::VulkanBackend() : backend_(nullptr, ggml_backend_free) {
    ggml_log_set([](ggml_log_level, const char*, void*) {}, nullptr);
    backend_.reset(ggml_backend_vk_init(0));
    if (!backend_) throw std::runtime_error("Vulkan device 0 initialization failed");
}
void VulkanBackend::compute(ggml_cgraph* graph) const {
    if (ggml_backend_graph_compute(get(), graph) != GGML_STATUS_SUCCESS)
        throw std::runtime_error("Vulkan graph computation failed");
}
Graph::Graph(const VulkanBackend& backend, size_t nodes)
    : context_(ggml_init({ggml_tensor_overhead() * nodes + ggml_graph_overhead_custom(nodes, false), nullptr, true}), ggml_free),
      allocator_(ggml_gallocr_new(ggml_backend_get_default_buffer_type(backend.get())), ggml_gallocr_free),
      backend_(backend), graph(ggml_new_graph_custom(context_.get(), nodes, false)) {}
void Graph::allocate() {
    if (!ggml_gallocr_alloc_graph(allocator_.get(), graph))
        throw std::runtime_error("Vulkan graph allocation failed");
}
void Graph::compute() const { backend_.compute(graph); }
ggml_tensor* Graph::tensor(const char* name) const {
    auto* value = ggml_graph_get_tensor(graph, name);
    if (!value) throw std::runtime_error(std::string("Missing graph tensor: ") + name);
    return value;
}
void Graph::set(const char* name, const void* data, size_t bytes) {
    ggml_backend_tensor_set(tensor(name), data, 0, bytes);
}
std::vector<float> Graph::read(const char* name) const {
    auto* value = tensor(name);
    std::vector<float> result(ggml_nelements(value));
    ggml_backend_tensor_get(value, result.data(), 0, result.size() * sizeof(float));
    return result;
}
}
