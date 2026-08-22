#include "session.hpp"

#include "cli.hpp"

#include <tts-cpp/chatterbox/engine.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <stdexcept>
#include <utility>

namespace tts {

struct Session::Impl {
    Runtime runtime;
    EngineKnobs knobs;
    int chunk_chars = 0;
    int first_chunk_chars = 0;
    float quiet_amp2 = kQuietAmp2;
    std::unique_ptr<tts_cpp::chatterbox::Engine> engine;

    Impl(Runtime runtime_in, EngineKnobs knobs_in, int chunk_chars_in, float quiet_amp2_in, int first_chunk_chars_in)
        : runtime(std::move(runtime_in)), knobs(std::move(knobs_in)), chunk_chars(chunk_chars_in),
          first_chunk_chars(first_chunk_chars_in > 0 ? first_chunk_chars_in : chunk_chars_in), quiet_amp2(quiet_amp2_in) {
        if (!std::filesystem::is_regular_file(runtime.t3) || !std::filesystem::is_regular_file(runtime.s3))
            throw std::runtime_error("model file missing");
        if (!std::filesystem::is_regular_file(knobs.reference))
            throw std::runtime_error("reference audio not found: " + knobs.reference);
        if (chunk_chars < 1 || first_chunk_chars < 1) throw std::invalid_argument("chunk chars must be positive");

        log("event=model role=t3 path=" + runtime.t3);
        log("event=model role=s3gen path=" + runtime.s3);
        log("event=model role=reference path=" + knobs.reference);
        log("event=config lang=" + knobs.language + " gpu=" + std::to_string(runtime.gpu) +
            " ctx=" + std::to_string(runtime.context) + " threads=" + std::to_string(runtime.threads) +
            " seed=" + std::to_string(knobs.seed) + " max_tokens=" + std::to_string(knobs.max_tokens) +
            " top_k=" + std::to_string(knobs.top_k) + " top_p=" + std::to_string(knobs.top_p) +
            " min_p=" + std::to_string(knobs.min_p) + " temp=" + std::to_string(knobs.temperature) +
            " repeat=" + std::to_string(knobs.repeat_penalty) + " cfg=" + std::to_string(knobs.cfg_weight) +
            " exag=" + std::to_string(knobs.exaggeration) + " cfm=" + std::to_string(knobs.cfm_steps) +
            " first_chunk=" + std::to_string(first_chunk_chars) + " chunk=" + std::to_string(chunk_chars));

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

        const auto engine_init_started = std::chrono::steady_clock::now();
        engine = std::make_unique<tts_cpp::chatterbox::Engine>(options);
        const double engine_init_ms = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - engine_init_started).count();
        log("event=engine_ready init_ms=" + std::to_string(engine_init_ms) +
            " model_scope=once-per-process reference_conditioning=precomputed-once "
            "t3_kv=incremental-per-utterance");
    }

    Speech synthesize(const std::string& text) {
        if (text.empty()) throw std::runtime_error("text is empty");
        const auto synthesis_started = std::chrono::steady_clock::now();
        const auto pieces = pack_text_staged(text, first_chunk_chars, chunk_chars);
        log("event=pack chunks=" + std::to_string(pieces.size()) + " first_limit=" +
            std::to_string(first_chunk_chars) + " later_limit=" + std::to_string(chunk_chars));
        for (size_t i = 0; i < pieces.size(); ++i) {
            const std::string& piece = pieces[i];
            const std::string head = piece.size() > 48 ? piece.substr(0, 48) + "..." : piece;
            log("event=pack_chunk index=" + std::to_string(i) + " chars=" + std::to_string(utf8_chars(piece)) +
                " bytes=" + std::to_string(piece.size()) + " text=" + head);
        }

        Speech speech;
        speech.chunks = static_cast<int>(pieces.size());
        speech.pcm.reserve(static_cast<size_t>(std::max(1, speech.chunks)) * static_cast<size_t>(kRate) * 8u);
        for (int i = 0; i < speech.chunks; ++i) {
            log("event=chunk_start index=" + std::to_string(i));
            auto result = engine->synthesize(pieces[static_cast<size_t>(i)]);
            if (i == 0) {
                speech.ttfa_ms = std::chrono::duration<double, std::milli>(
                    std::chrono::steady_clock::now() - synthesis_started).count();
                log("event=first_audio ttfa_ms=" + std::to_string(speech.ttfa_ms) +
                    " granularity=whole-s3gen-chunk scope=server-internal client_streaming=0 warm_model=1 warm_voice=1");
            }
            float peak = 0;
            for (float s : result.pcm) peak = std::max(peak, std::abs(s));
            log("event=chunk index=" + std::to_string(i) + " t3_tokens=" + std::to_string(result.t3_tokens) +
                " samples=" + std::to_string(result.pcm.size()) +
                " seconds=" + std::to_string(result.pcm.size() / static_cast<double>(kRate)) +
                " t3_ms=" + std::to_string(result.t3_ms) + " s3gen_ms=" + std::to_string(result.s3gen_ms) +
                " peak=" + std::to_string(peak));
            glue(speech.pcm, result.pcm, quiet_amp2);
            speech.t3_ms += result.t3_ms;
            speech.s3gen_ms += result.s3gen_ms;
            speech.t3_tokens += result.t3_tokens;
        }
        if (speech.pcm.empty()) throw std::runtime_error("synthesis returned no audio");
        float peak = 0;
        for (float s : speech.pcm) peak = std::max(peak, std::abs(s));
        log("event=pcm samples=" + std::to_string(speech.pcm.size()) +
            " seconds=" + std::to_string(speech.pcm.size() / static_cast<double>(kRate)) +
            " peak=" + std::to_string(peak) + " t3_tokens=" + std::to_string(speech.t3_tokens));
        return speech;
    }
};

Session::Session(const Runtime& runtime, const EngineKnobs& knobs, int chunk_chars, float quiet_amp2, int first_chunk_chars)
    : impl_(std::make_unique<Impl>(runtime, knobs, chunk_chars, quiet_amp2, first_chunk_chars)) {}
Session::~Session() = default;
Speech Session::synthesize(const std::string& text) { return impl_->synthesize(text); }
const Runtime& Session::runtime() const noexcept { return impl_->runtime; }
const EngineKnobs& Session::knobs() const noexcept { return impl_->knobs; }
int Session::chunk_chars() const noexcept { return impl_->chunk_chars; }

} 
