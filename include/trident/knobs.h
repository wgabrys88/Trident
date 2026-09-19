#pragma once
namespace trident::gpt2 {
struct Knobs {
    int seed, n_predict, cfm_steps, trim_fade, top_k;
    float temperature, top_p, repeat_penalty;
};
}
namespace trident::llama {
struct Knobs {
    int seed, n_predict, cfm_steps, trim_fade;
    float temperature, top_p, repeat_penalty, min_p, cfg_weight, exaggeration, cfm_cfg;
};
}
