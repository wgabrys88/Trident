#pragma once
#include <cmath>
#include <cstdint>
#include <vector>

namespace trident::llama {
class RepeatPenalty {
    float penalty_;
public:
    explicit RepeatPenalty(float penalty) : penalty_(penalty) {}
    void apply(std::vector<float>& scores, const std::vector<int32_t>& generated) const {
        std::vector<bool> seen(scores.size(), false);
        for (int32_t token : generated) {
            if (seen[token]) continue;
            seen[token] = true;
            auto& score = scores[token];
            if (score != -INFINITY) score = score > 0.f ? score / penalty_ : score * penalty_;
        }
    }
};
}
