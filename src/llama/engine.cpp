#include "engine.h"
#include "common/audio.h"
#include <stdexcept>

namespace trident::llama {
Engine::Engine(const std::string& t3_path, const std::string& s3_path, Flags& flags)
    : knobs{
          flags.integer("--gpu"), flags.integer("--seed"), flags.integer("--n-predict"),
          flags.integer("--cfm-steps"), flags.integer("--trim-fade-samples"), {},
          flags.real("--temperature"), flags.real("--top-p"), flags.real("--repeat-penalty"),
          flags.real("--min-p"), flags.real("--cfg-weight"), flags.real("--exaggeration"), flags.real("--cfm-cfg")
      },
      backend(knobs.gpu), t3(t3_path, backend, knobs), s3(s3_path, backend, knobs),
      paths{
          flags.string("--tokenizer-python"), flags.string("--tokenizer-script"),
          flags.string("--tokenizer-source"), flags.string("--tokenizer-tts-source"),
          flags.string("--tokenizer-json"), flags.string("--cangjie-json"), flags.string("--dicta-model"),
          flags.string("--language")
      },
      tokenizer(paths), numbers(paths.language) {}
std::vector<float> Engine::synthesize(const std::string& text) {
    if (("," + t3.languages() + ",").find(",[" + paths.language + "],") == std::string::npos)
        throw std::runtime_error("Unsupported language: " + paths.language + "; GGUF offers " + t3.languages());
    auto tokens = tokenizer.tokenize(numbers.verbalize(tokenizer.punctuation(text)));
    tokens.insert(tokens.begin(), t3.start_text());
    tokens.push_back(t3.stop_text());
    auto pcm = s3.synthesize(t3.generate(tokens));
    pcm.resize(pcm.size() - 960);
    Audio::fade(pcm, knobs.trim_fade);
    return pcm;
}
}
