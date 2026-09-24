#include "engine.h"
#include "common/audio.h"
#include <stdexcept>

namespace trident::llama {
Engine::Engine(const std::string& t3_path, const std::string& s3_path, Flags& flags)
    : knobs(knobs_from(flags, true)),
      backend(knobs.gpu), t3(t3_path, backend, knobs), s3(s3_path, backend, knobs),
      tokenizer(t3_path) {}
std::vector<float> Engine::synthesize(const std::string& text, const std::string& language) {
    if (("," + t3.languages() + ",").find(",[" + language + "],") == std::string::npos)
        throw std::runtime_error("Unsupported language: " + language + "; GGUF offers " + t3.languages());
    auto& spell = numbers.try_emplace(language, language).first->second;
    auto tokens = tokenizer.tokenize(spell.verbalize(MtlTokenizer::punctuation(text)), language);
    tokens.insert(tokens.begin(), t3.start_text());
    tokens.push_back(t3.stop_text());
    auto pcm = s3.synthesize(t3.generate(tokens));
    pcm.resize(pcm.size() - 960);
    Audio::fade(pcm, knobs.trim_fade);
    return pcm;
}
}
