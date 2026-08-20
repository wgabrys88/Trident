#pragma once

#include "audio.hpp"

#include <string>
#include <unordered_map>
#include <vector>

namespace tts {

enum class Family { V3, Turbo, Nano };

struct FamilyPolicy {
    const char* name;
    const char* label;
    int params_m;
    bool multilingual;
    bool paralinguistic;
    bool cfg_enabled;
    bool exaggeration_enabled;
    int chunk_chars;
    int context;
    int max_tokens;
    int top_k;
    float top_p;
    float min_p;
    float temperature;
    float repeat_penalty;
    int cfm_steps;
    float cfg_weight;
    float exaggeration;
    const char* languages[8];
};

Family parse_family(const std::string& name);
const FamilyPolicy& policy(Family family);
const FamilyPolicy& policy(const std::string& name);

// Turbo/Nano: CFG and exaggeration are no-ops. V3 keeps the caller's values
// after range checks. Returns true if a knob was forced off.
bool apply_family_policy(Family family, EngineKnobs& knobs);

const char* language_name(Family family, const std::string& code);
int language_id(const std::string& code);

} // namespace tts
