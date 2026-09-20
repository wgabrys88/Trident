#include "trident/llama_engine.h"
#include "mtl_numbers.h"
#include "mtl_tokenizer.h"
#include "t3.h"
#include "s3.h"
#include "common/audio.h"

namespace trident::llama {
class Engine::Impl {
public:
    Knobs knobs;
    VulkanBackend backend;
    LlamaT3 t3;
    CfgS3 s3;
    MtlTokenizer tokenizer;
    MtlNumbers numbers;
    Impl(const std::string& t3_path, const std::string& s3_path, Knobs settings, TokenizerPaths paths)
        : knobs(settings), backend(knobs.gpu), t3(t3_path, backend, knobs), s3(s3_path, backend, knobs),
          tokenizer(paths), numbers(paths.language_id) {}
};
Engine::Engine(std::string t3, std::string s3, Knobs knobs, TokenizerPaths paths)
    : impl_(std::make_unique<Impl>(t3, s3, knobs, std::move(paths))) {}
Engine::~Engine() = default;
void Engine::synthesize(const std::string& text, std::vector<float>& pcm, SynthesizeStats& stats) {
    stats = {};
    auto tokens = impl_->tokenizer.tokenize(impl_->numbers.verbalize(impl_->tokenizer.punctuation(text)));
    tokens.insert(tokens.begin(), impl_->t3.start_text());
    tokens.push_back(impl_->t3.stop_text());
    pcm = impl_->s3.synthesize(impl_->t3.generate(tokens, stats));
    pcm.resize(pcm.size() - 960);
    Audio::fade(pcm, impl_->knobs.trim_fade);
    stats.units = 1; stats.max_unit_predicted = stats.predicted_count;
}
}
