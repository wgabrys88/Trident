#include "cli.hpp"

#include <tts-cpp/chatterbox/engine.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <stdexcept>

namespace tts {

Speech run(const Runtime& runtime, const EngineKnobs& knobs, const std::string& text, int chunk_chars, float quiet_amp2) {
    if (!std::filesystem::is_regular_file(runtime.t3) || !std::filesystem::is_regular_file(runtime.s3))
        throw std::runtime_error("model file missing");
    if (!std::filesystem::is_regular_file(knobs.reference))
        throw std::runtime_error("reference audio not found: " + knobs.reference);
    if (text.empty()) throw std::runtime_error("text is empty");

    log("load t3=" + runtime.t3);
    log("load s3=" + runtime.s3);
    log("load ref=" + knobs.reference);
    log("knobs lang=" + knobs.language + " gpu=" + std::to_string(runtime.gpu) +
        " ctx=" + std::to_string(runtime.context) + " threads=" + std::to_string(runtime.threads) +
        " seed=" + std::to_string(knobs.seed) + " max_tokens=" + std::to_string(knobs.max_tokens) +
        " top_k=" + std::to_string(knobs.top_k) + " top_p=" + std::to_string(knobs.top_p) +
        " min_p=" + std::to_string(knobs.min_p) + " temp=" + std::to_string(knobs.temperature) +
        " repeat=" + std::to_string(knobs.repeat_penalty) + " cfg=" + std::to_string(knobs.cfg_weight) +
        " exag=" + std::to_string(knobs.exaggeration) + " cfm=" + std::to_string(knobs.cfm_steps) +
        " chunk=" + std::to_string(chunk_chars));

    tts_cpp::chatterbox::EngineOptions options;
    options.t3_gguf_path = runtime.t3;
    options.s3gen_gguf_path = runtime.s3;
    options.n_gpu_layers = runtime.gpu;
    options.n_threads = runtime.threads;
    options.n_ctx = runtime.context;
    options.reference_audio = knobs.reference;
    options.language = knobs.language;
    options.seed = knobs.seed;
    options.n_predict = knobs.max_tokens;
    options.top_k = knobs.top_k;
    options.top_p = knobs.top_p;
    options.min_p = knobs.min_p;
    options.temperature = knobs.temperature;
    options.repeat_penalty = knobs.repeat_penalty;
    options.cfg_weight = knobs.cfg_weight;
    options.exaggeration = knobs.exaggeration;
    options.cfm_steps = knobs.cfm_steps;

    tts_cpp::chatterbox::Engine engine(options);
    const auto pieces = pack_text(text, chunk_chars);
    log("pack chunks=" + std::to_string(pieces.size()) + " limit=" + std::to_string(chunk_chars));
    for (size_t i = 0; i < pieces.size(); ++i) {
        const std::string& piece = pieces[i];
        const std::string head = piece.size() > 48 ? piece.substr(0, 48) + "..." : piece;
        log("pack[" + std::to_string(i) + "] chars=" + std::to_string(utf8_chars(piece)) +
            " bytes=" + std::to_string(piece.size()) + " text=" + head);
    }
    Speech speech;
    speech.chunks = static_cast<int>(pieces.size());
    for (int i = 0; i < speech.chunks; ++i) {
        auto result = engine.synthesize(pieces[static_cast<size_t>(i)]);
        float peak = 0;
        for (float s : result.pcm) peak = std::max(peak, std::abs(s));
        log("chunk[" + std::to_string(i) + "] t3_tokens=" + std::to_string(result.t3_tokens) +
            " samples=" + std::to_string(result.pcm.size()) +
            " seconds=" + std::to_string(result.pcm.size() / static_cast<double>(kRate)) +
            " t3_ms=" + std::to_string(result.t3_ms) + " s3gen_ms=" + std::to_string(result.s3gen_ms) +
            " peak=" + std::to_string(peak) +
            " cap=" + (result.t3_tokens >= knobs.max_tokens ? "1" : "0"));
        glue(speech.pcm, result.pcm, quiet_amp2);
        speech.t3_ms += result.t3_ms;
        speech.s3gen_ms += result.s3gen_ms;
        speech.t3_tokens += result.t3_tokens;
    }
    if (speech.pcm.empty()) throw std::runtime_error("synthesis returned no audio");
    float peak = 0;
    for (float s : speech.pcm) peak = std::max(peak, std::abs(s));
    log("pcm samples=" + std::to_string(speech.pcm.size()) +
        " seconds=" + std::to_string(speech.pcm.size() / static_cast<double>(kRate)) + " peak=" + std::to_string(peak) +
        " t3_tokens=" + std::to_string(speech.t3_tokens));
    return speech;
}

int run_job(const Args& args, const EngineKnobs& knobs, int chunk_chars) {
    for (const auto* key : {"--model", "--s3gen-gguf", "--reference", "--text-file"}) require_file(args, key);
    const std::string text = read_text(args.at("--text-file"));
    const Runtime runtime = runtime_from(args);
    const auto started = std::chrono::steady_clock::now();
    const Speech speech = run(runtime, knobs, text, chunk_chars, kQuietAmp2);
    write_wav(args.at("--output"), speech.pcm);
    const double total_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
    print_done(speech, total_ms, runtime, knobs, chunk_chars);
    return 0;
}

}
