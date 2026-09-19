#pragma once
namespace trident {
struct SynthesizeStats {
    int predicted_count = 0, dropped_count = 0, eos = 0, n_past = 0;
    int units = 0, text_tokens = 0, max_unit_predicted = 0;
};
}
