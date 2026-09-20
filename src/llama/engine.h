#pragma once
#include "../engine.h"
#include "common/pipe.h"
#include "t3.h"
#include "common/s3.h"
#include "mtl_tokenizer.h"
#include "mtl_numbers.h"

namespace trident::llama {
class Engine : public Synth {
    Knobs knobs{};
    VulkanBackend backend;
    LlamaT3 t3;
    S3 s3;
    TokenizerPaths paths;
    MtlTokenizer tokenizer;
    MtlNumbers numbers;
public:
    Engine(const std::string& t3_path, const std::string& s3_path, Flags& flags);
    std::vector<float> synthesize(const std::string& text) override;
};
}
