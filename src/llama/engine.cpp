#include "engine.h"
#include "common/audio.h"
#include <stdexcept>

namespace trident::llama {
Engine::Engine(const std::string& t3_path, const std::string& s3_path, Knobs knobs)
    : knobs(knobs),
      backend(knobs.gpu), t3(t3_path, backend, knobs), s3(s3_path, backend, knobs),
      tokenizer(t3_path) {}
std::vector<float> Engine::synthesize(const std::string& text, const std::string& language) {
    if (("," + t3.languages() + ",").find(",[" + language + "],") == std::string::npos)
        throw std::runtime_error("Unsupported language: " + language + "; GGUF offers " + t3.languages());
    auto tokens = tokenizer.tokenize(text, language);
    tokens.insert(tokens.begin(), t3.start_text());
    tokens.push_back(t3.stop_text());
    auto pcm = s3.synthesize(t3.generate(tokens));
    if (knobs.end_trim > 0 && pcm.size() > size_t(knobs.end_trim)) pcm.resize(pcm.size() - knobs.end_trim);
    Audio::fade(pcm, knobs.trim_fade);
    return pcm;
}
}
