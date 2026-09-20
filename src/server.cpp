#include "engine.h"
#include "common/gguf_file.h"
#include "common/pipe.h"
#include "gpt2/engine.h"
#include "llama/engine.h"
#include <memory>
#include <stdexcept>

int main(int argc, char** argv) {
    trident::Flags flags(argc, argv);
    auto family = trident::GgufFile::architecture(argv[1]);
    std::unique_ptr<trident::Synth> engine;
    if (family == "chatterbox-gpt2")
        engine = std::make_unique<trident::gpt2::Engine>(argv[1], argv[2], flags);
    else if (family == "chatterbox-llama")
        engine = std::make_unique<trident::llama::Engine>(argv[1], argv[2], flags);
    else
        throw std::runtime_error("Unsupported architecture: " + family);
    flags.finish();
    trident::PipeServer(argv[3]).serve(*engine);
}
