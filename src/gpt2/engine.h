#pragma once
#include "../engine.h"
#include "t3.h"
#include "common/s3.h"
#include "bpe.h"
#include "text_en.h"

namespace trident::gpt2 {
class Engine : public Synth {
    Knobs knobs{};
    VulkanBackend backend;
    Gpt2T3 t3;
    S3 s3;
    Gpt2Bpe bpe;
    EnglishText english;
public:
    Engine(const std::string& t3_path, const std::string& s3_path, Knobs knobs);
    std::vector<float> synthesize(const std::string& text, const std::string& language) override;
};
}
