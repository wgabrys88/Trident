#include "cli.hpp"
#include "engine_wrapper.hpp"

#include <chrono>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

const std::vector<std::string> kRequired = {
    "--model", "--s3gen-gguf", "--reference", "--text-file", "--output",
    "--n-gpu-layers", "--context", "--threads", "--seed", "--max-tokens",
    "--top-k", "--top-p", "--temperature", "--repeat-penalty", "--cfm-steps", "--chunk-chars",
};

} // namespace

int main(int argc, char** argv) {
    try {
        const tts::Args args = tts::parse_args(argc, argv, kRequired);
        for (const auto& key : {"--model", "--s3gen-gguf", "--reference", "--text-file"}) tts::require_file(args, key);
        const std::string text = tts::read_text(args.at("--text-file"));
        const tts::Runtime runtime = tts::runtime_from(args);
        tts::EngineKnobs knobs;
        knobs.reference = args.at("--reference");
        knobs.language = "en";
        knobs.seed = tts::parse_int(args, "--seed");
        knobs.max_tokens = tts::parse_int(args, "--max-tokens");
        knobs.top_k = tts::parse_int(args, "--top-k");
        knobs.top_p = tts::parse_float(args, "--top-p");
        knobs.min_p = 0;
        knobs.temperature = tts::parse_float(args, "--temperature");
        knobs.repeat_penalty = tts::parse_float(args, "--repeat-penalty");
        knobs.cfg_weight = 0;
        knobs.exaggeration = 0;
        knobs.cfm_steps = tts::parse_int(args, "--cfm-steps");
        const int chunk_chars = tts::parse_int(args, "--chunk-chars");
        if (knobs.max_tokens < 1 || knobs.top_k < 1 || knobs.cfm_steps < 1 || chunk_chars < 1)
            throw std::invalid_argument("integer sampling values are out of range");
        if (knobs.top_p < 0 || knobs.top_p > 1 || knobs.temperature < 0 || knobs.repeat_penalty <= 0)
            throw std::invalid_argument("sampling values are out of range");

        tts::log("family=nano");
        tts::EngineWrapper engine;
        engine.LoadChatterboxModel("nano", runtime.t3, runtime.s3);
        engine.initialize(runtime.t3, runtime.s3, runtime.gpu, runtime.threads, runtime.context);
        engine.set_params(knobs);
        const auto started = std::chrono::steady_clock::now();
        double ttfa_ms = -1;
        engine.prepare(tts::knobs_to_voice(knobs, chunk_chars), text);
        while (engine.busy()) {
            engine.step([&](int, int, const std::vector<float>&, const std::vector<float>& playable) {
                if (ttfa_ms < 0 && !playable.empty())
                    ttfa_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
            });
        }
        const tts::Speech speech = engine.finish();
        tts::write_wav(args.at("--output"), speech.pcm);
        const double total_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
        const auto stats = engine.stats();
        tts::log("ttfa_ms=" + std::to_string(ttfa_ms) +
                 " speaker_hits=" + std::to_string(stats.speaker_hits) +
                 " kv_hits=" + std::to_string(stats.kv_hits));
        std::cerr << "family=nano ";
        tts::print_done(speech, total_ms, runtime, knobs, chunk_chars);
        return 0;
    } catch (const std::invalid_argument& error) {
        std::cerr << "argument error: " << error.what() << std::endl;
        return 2;
    } catch (const std::exception& error) {
        std::cerr << "tts error: " << error.what() << std::endl;
        return 1;
    }
}
