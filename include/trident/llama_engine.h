#pragma once
#include "knobs.h"
#include "stats.h"
#include <memory>
#include <string>
#include <vector>

namespace trident::llama {
struct TokenizerPaths {
    std::string python, script, source, tts_source, tokenizer_json, cangjie_json, dicta_model, language_id;
};
class Engine {
    class Impl;
    std::unique_ptr<Impl> impl_;
public:
    Engine(std::string t3_gguf, std::string s3_gguf, Knobs, TokenizerPaths);
    ~Engine();
    void synthesize(const std::string& text, std::vector<float>& pcm, SynthesizeStats&);
};
class Baker {
    std::string t3_, s3_, reference_;
public:
    Baker(std::string t3_gguf, std::string s3_gguf, std::string reference_wav);
    void bake();
};
}
