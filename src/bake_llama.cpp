#include "trident/llama_engine.h"
#include <stdexcept>

int main(int argc, char** argv) {
    if (argc != 4) throw std::runtime_error("Expected T3.gguf S3.gguf reference.wav");
    trident::llama::Baker(argv[1], argv[2], argv[3]).bake();
}
