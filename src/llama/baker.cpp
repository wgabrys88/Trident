#include "trident/llama_engine.h"
#include "common/bake.h"

namespace trident::llama {
Baker::Baker(std::string t3, std::string s3, std::string reference)
    : t3_(std::move(t3)), s3_(std::move(s3)), reference_(std::move(reference)) {}
void Baker::bake() { bake_voice(t3_, s3_, reference_, 6); }
}
