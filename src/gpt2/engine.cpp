#include "engine.h"
#include "common/audio.h"

namespace trident::gpt2 {
Engine::Engine(const std::string& t3_path, const std::string& s3_path, Knobs knobs)
    : knobs(knobs),
      backend(knobs.gpu), t3(t3_path, backend, knobs), s3(s3_path, backend, knobs),
      bpe(t3.vocabulary(), t3.types(), t3.merges()) {}
std::vector<float> Engine::synthesize(const std::string& text, const std::string&) {
    auto tokens = bpe.tokenize(text);
    auto pcm = s3.synthesize(t3.generate(tokens));
    Audio::fade(pcm, knobs.trim_fade);
    return pcm;
}
}
