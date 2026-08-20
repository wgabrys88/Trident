#include "family.hpp"

#include <algorithm>
#include <stdexcept>

namespace tts {

static const FamilyPolicy kV3 = {
    "v3", "CHATTERBOX TTS V3", 500, true, false, true, true,
    180, 2048, 768, 0, 1.0f, 0.05f, 0.8f, 1.2f, 7, 0.5f, 0.3f,
    {"en", "de", "pl", nullptr},
};
static const FamilyPolicy kTurbo = {
    "turbo", "CHATTERBOX TTS TURBO", 350, false, true, false, false,
    120, 2048, 768, 1000, 0.99f, 0.0f, 0.6f, 1.3f, 2, 0.0f, 0.0f,
    {"en", nullptr},
};
static const FamilyPolicy kNano = {
    "nano", "CHATTERBOX TTS NANO", 110, false, true, false, false,
    180, 2048, 768, 1000, 0.95f, 0.0f, 0.8f, 1.2f, 2, 0.0f, 0.0f,
    {"en", nullptr},
};

Family parse_family(const std::string& name) {
    if (name == "v3") return Family::V3;
    if (name == "turbo") return Family::Turbo;
    if (name == "nano") return Family::Nano;
    throw std::invalid_argument("unknown family: " + name);
}

const FamilyPolicy& policy(Family family) {
    switch (family) {
        case Family::V3: return kV3;
        case Family::Turbo: return kTurbo;
        case Family::Nano: return kNano;
    }
    return kTurbo;
}

const FamilyPolicy& policy(const std::string& name) {
    return policy(parse_family(name));
}

bool apply_family_policy(Family family, EngineKnobs& knobs) {
    const FamilyPolicy& p = policy(family);
    bool forced = false;
    if (!p.cfg_enabled && knobs.cfg_weight != 0) {
        knobs.cfg_weight = 0;
        forced = true;
    }
    if (!p.exaggeration_enabled && knobs.exaggeration != 0) {
        knobs.exaggeration = 0;
        forced = true;
    }
    if (family != Family::V3) knobs.language = "en";
    if (family == Family::V3) knobs.top_k = 0;
    return forced;
}

const char* language_name(Family family, const std::string& code) {
    if (code == "en") return "English";
    if (family == Family::V3 && code == "de") return "German";
    if (family == Family::V3 && code == "pl") return "Polish";
    return "English";
}

int language_id(const std::string& code) {
    if (code == "de") return 636;
    if (code == "pl") return 717;
    return 708;
}

} // namespace tts
