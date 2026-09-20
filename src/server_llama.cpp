#include "trident/llama_engine.h"
#include "common/pipe.h"

int main(int argc, char** argv) {
    trident::Flags flags(argc, argv);
    trident::llama::Knobs knobs{
        flags.integer("--gpu"), flags.integer("--seed"), flags.integer("--n-predict"), flags.integer("--cfm-steps"),
        flags.integer("--trim-fade-samples"),
        flags.real("--temperature"), flags.real("--top-p"), flags.real("--repeat-penalty"),
        flags.real("--min-p"), flags.real("--cfg-weight"), flags.real("--exaggeration"), flags.real("--cfm-cfg"),
    };
    auto language = flags.string("--language");
    for (char& c : language) if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
    trident::llama::TokenizerPaths paths{
        flags.string("--tokenizer-python"), flags.string("--tokenizer-script"),
        flags.string("--tokenizer-source"), flags.string("--tokenizer-tts-source"),
        flags.string("--tokenizer-json"), flags.string("--cangjie-json"), flags.string("--dicta-model"),
        language,
    };
    flags.finish();
    trident::llama::Engine engine(argv[1], argv[2], knobs, std::move(paths));
    trident::PipeServer(argv[3]).serve(engine);
}
