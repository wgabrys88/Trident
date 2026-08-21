#include "cli.hpp"

#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

const std::vector<std::string> kRequired = {
    "--model", "--s3gen-gguf", "--reference", "--text-file", "--output", "--language",
    "--n-gpu-layers", "--context", "--threads", "--seed", "--max-tokens",
    "--top-k", "--top-p", "--min-p", "--temperature", "--repeat-penalty", "--cfg-weight",
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
        knobs.top_k = tts::parse_int(args, "--top-k");
        knobs.top_p = tts::parse_float(args, "--top-p");
        const float requested_min_p = tts::parse_float(args, "--min-p");
        knobs.min_p = 0;
        knobs.temperature = tts::parse_float(args, "--temperature");
        knobs.repeat_penalty = tts::parse_float(args, "--repeat-penalty");
        const float requested_cfg = tts::parse_float(args, "--cfg-weight");
        const float requested_exag = tts::parse_float(args, "--exaggeration");
        knobs.cfg_weight = 0;
        knobs.exaggeration = 0;
        knobs.cfm_steps = tts::parse_int(args, "--cfm-steps");
        const int chunk_chars = tts::parse_int(args, "--chunk-chars");
        if (knobs.max_tokens < 1 || knobs.top_k < 1 || knobs.cfm_steps < 1 || chunk_chars < 1)
            throw std::invalid_argument("integer sampling values are out of range");
        if (knobs.top_p < 0 || knobs.top_p > 1 || requested_min_p < 0 || requested_min_p > 1 ||
            knobs.temperature < 0 || knobs.repeat_penalty <= 0 || requested_cfg < 0 || requested_exag < 0)
            throw std::invalid_argument("sampling values are out of range");
        if (knobs.language != "en")
            throw std::invalid_argument("Turbo/Nano currently support --language en only");
        if (requested_min_p != 0 || requested_cfg != 0 || requested_exag != 0)
            tts::log("Turbo/Nano ignore min_p, cfg_weight, and exaggeration; forcing all three to zero");
        tts::log("family=nano");
        return tts::run_job(args, knobs, chunk_chars);
    } catch (const std::invalid_argument& error) {
        std::cerr << "argument error: " << error.what() << std::endl;
        return 2;
    } catch (const std::exception& error) {
        std::cerr << "tts error: " << error.what() << std::endl;
        return 1;
    }
}
