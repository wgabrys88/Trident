#include "trident/gpt2_engine.h"
#include "bpe.h"
#include "text_en.h"
#include "t3.h"
#include "s3.h"
#include "common/audio.h"

namespace trident::gpt2 {
class Engine::Impl {
public:
    Knobs knobs;
    VulkanBackend backend;
    Gpt2T3 t3;
    MeanflowS3 s3;
    Gpt2Bpe bpe;
    EnglishText english;
    Impl(const std::string& t3_path, const std::string& s3_path, Knobs settings)
        : knobs(settings), backend(knobs.gpu), t3(t3_path, backend, knobs), s3(s3_path, backend, knobs), bpe(t3.vocabulary(), t3.merges()) {}
};
Engine::Engine(std::string t3, std::string s3, Knobs knobs) : impl_(std::make_unique<Impl>(t3, s3, knobs)) {}
Engine::~Engine() = default;
void Engine::synthesize(const std::string& text, std::vector<float>& pcm, SynthesizeStats& stats) {
    stats = {};
    auto tokens = impl_->bpe.tokenize(impl_->bpe.punc_norm(impl_->english.prepare(text)));
    pcm = impl_->s3.synthesize(impl_->t3.generate(tokens, stats));
    Audio::fade(pcm, impl_->knobs.trim_fade);
    stats.units = 1; stats.max_unit_predicted = stats.predicted_count;
}
}
