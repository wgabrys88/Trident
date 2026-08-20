#include "cli.hpp"

#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

const std::vector<std::string> kRequired = {
    "--model", "--s3gen-gguf", "--reference", "--text-file", "--output", "--language",
    "--n-gpu-layers", "--context", "--threads", "--seed", "--max-tokens",
    "--top-p", "--min-p", "--temperature", "--repeat-penalty", "--cfg-weight",
    "--exaggeration", "--cfm-steps", "--chunk-chars",
};

}

int main(int argc, char** argv) {
    try {
        const tts::Args args = tts::parse_args(argc, argv, kRequired);
        tts::EngineKnobs knobs;
        knobs.reference = args.at("--reference");
        knobs.language = args.at("--language");
        knobs.seed = tts::parse_int(args, "--seed");
        knobs.max_tokens = tts::parse_int(args, "--max-tokens");
        knobs.top_k = 0;
        knobs.top_p = tts::parse_float(args, "--top-p");
        knobs.min_p = tts::parse_float(args, "--min-p");
        knobs.temperature = tts::parse_float(args, "--temperature");
        knobs.repeat_penalty = tts::parse_float(args, "--repeat-penalty");
        knobs.cfg_weight = tts::parse_float(args, "--cfg-weight");
        knobs.exaggeration = tts::parse_float(args, "--exaggeration");
        knobs.cfm_steps = tts::parse_int(args, "--cfm-steps");
        const int chunk_chars = tts::parse_int(args, "--chunk-chars");
        if (knobs.max_tokens < 1 || knobs.cfm_steps < 1 || chunk_chars < 1)
            throw std::invalid_argument("integer sampling values are out of range");
        if (knobs.top_p < 0 || knobs.top_p > 1 || knobs.min_p < 0 || knobs.min_p > 1 || knobs.temperature < 0 ||
            knobs.repeat_penalty <= 0 || knobs.cfg_weight < 0 || knobs.exaggeration < 0)
            throw std::invalid_argument("sampling or voice values are out of range");
        if (knobs.language.size() != 2)
            throw std::invalid_argument("v3 --language must be a 2-letter code");
        tts::log("family=v3");
        return tts::run_job(args, knobs, chunk_chars);
    } catch (const std::invalid_argument& error) {
        std::cerr << "argument error: " << error.what() << std::endl;
        return 2;
    } catch (const std::exception& error) {
        std::cerr << "tts error: " << error.what() << std::endl;
        return 1;
    }
}
