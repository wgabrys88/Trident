#include "trident/gpt2_engine.h"
#include "common/pipe.h"

int main(int argc, char** argv) {
    trident::Flags flags(argc, argv);
    trident::gpt2::Knobs knobs{
        flags.integer("--gpu"), flags.integer("--seed"), flags.integer("--n-predict"), flags.integer("--cfm-steps"),
        flags.integer("--trim-fade-samples"), flags.integer("--top-k"),
        flags.real("--temperature"), flags.real("--top-p"), flags.real("--repeat-penalty"),
    };
    flags.finish();
    trident::gpt2::Engine engine(argv[1], argv[2], knobs);
    trident::PipeServer(argv[3]).serve(engine);
}
