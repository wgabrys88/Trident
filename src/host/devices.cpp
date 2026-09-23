#include "devices.hpp"
#include "ggml-vulkan.h"
#include <cstdlib>
#include <stdexcept>
#include <string>

namespace trident::host {
int pick_vulkan_device() {
    if (const char* forced = std::getenv("TRIDENT_VULKAN_DEVICE"))
        return std::stoi(forced);
    int count = ggml_backend_vk_get_device_count();
    if (count <= 0) throw std::runtime_error("no vulkan device");
    int best = 0;
    size_t best_total = 0;
    for (int i = 0; i < count; ++i) {
        size_t free = 0, total = 0;
        ggml_backend_vk_get_device_memory(i, &free, &total);
        if (total > best_total) {
            best_total = total;
            best = i;
        }
    }
    return best;
}
}
