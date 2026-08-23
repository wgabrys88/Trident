#include "session.hpp"

#include "cli.hpp"

#include <tts-cpp/chatterbox/engine.h>

#include <algorithm>
#include <chrono>
#include <utility>

namespace tts {

struct Session::Impl {
    Runtime runtime;
    EngineKnobs knobs;
    int chunk_chars = 0;
    int first_chunk_chars = 0;
    int stream_chunk_tokens = 0;
    int stream_first_chunk_tokens = 0;
    float quiet_amp2 = kQuietAmp2;
    std::unique_ptr<tts_cpp::chatterbox::Engine> engine;

    Impl(Runtime runtime_in, EngineKnobs knobs_in, int chunk_chars_in, int stream_chunk_tokens_in,
         int stream_first_chunk_tokens_in, float quiet_amp2_in, int first_chunk_chars_in)
        : runtime(std::move(runtime_in)), knobs(std::move(knobs_in)), chunk_chars(chunk_chars_in),
          first_chunk_chars(first_chunk_chars_in > 0 ? first_chunk_chars_in : chunk_chars_in),
          stream_chunk_tokens(stream_chunk_tokens_in), stream_first_chunk_tokens(stream_first_chunk_tokens_in),
          quiet_amp2(quiet_amp2_in) {
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
            " first_chunk=" + std::to_string(first_chunk_chars) + " chunk=" + std::to_string(chunk_chars) +
            " stream_first_tokens=" + std::to_string(stream_first_chunk_tokens) +
            " stream_tokens=" + std::to_string(stream_chunk_tokens));

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
        options.stream_chunk_tokens = stream_chunk_tokens;
        options.stream_first_chunk_tokens = stream_first_chunk_tokens;
        options.stream_cfm_steps = knobs.cfm_steps;

        const auto engine_init_started = std::chrono::steady_clock::now();
        engine = std::make_unique<tts_cpp::chatterbox::Engine>(options);
        const double engine_init_ms = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - engine_init_started).count();
        log("event=engine_ready init_ms=" + std::to_string(engine_init_ms) +
            " model_scope=once-per-process reference_conditioning=precomputed-once "
            "t3_kv=incremental-per-utterance");
    }

    Speech synthesize(const std::string& text, const AudioSink& sink) {
        const auto synthesis_started = std::chrono::steady_clock::now();
        const auto pieces = pack_text_staged(text, first_chunk_chars, chunk_chars);
        log("event=pack chunks=" + std::to_string(pieces.size()) + " first_limit=" +
            std::to_string(first_chunk_chars) + " later_limit=" + std::to_string(chunk_chars));
        Speech speech;
        speech.chunks = static_cast<int>(pieces.size());
        speech.pcm.reserve(static_cast<size_t>(std::max(1, speech.chunks)) * static_cast<size_t>(kRate) * 8u);
        std::vector<float> pending;
        pending.reserve(static_cast<std::size_t>(kRate) * 2u);
        bool first_audio = true;
        for (int i = 0; i < speech.chunks; ++i) {
            log("event=chunk_start index=" + std::to_string(i));
            auto result = engine->synthesize(pieces[static_cast<size_t>(i)], [&](
                const float* pcm, std::size_t samples, int stream_index, bool is_last) {
                if (first_audio) {
                    first_audio = false;
                    speech.ttfa_ms = std::chrono::duration<double, std::milli>(
                        std::chrono::steady_clock::now() - synthesis_started).count();
                    log("event=first_audio ttfa_ms=" + std::to_string(speech.ttfa_ms) +
                        " granularity=s3gen-token-stream scope=client-pcm client_streaming=1 warm_model=1 warm_voice=1");
                }
                if (i > 0 && stream_index == 0) {
                    std::vector<float> first(pcm, pcm + samples);
                    glue(pending, first, quiet_amp2);
                } else {
                    pending.insert(pending.end(), pcm, pcm + samples);
                }
                const bool utterance_last = i + 1 == speech.chunks && is_last;
                const std::size_t keep = utterance_last ? 0u : std::min<std::size_t>(pending.size(), static_cast<std::size_t>(kGlue));
                const std::size_t stable = pending.size() - keep;
                if (stable) {
                    if (sink) sink(pending.data(), stable);
                    speech.pcm.insert(speech.pcm.end(), pending.begin(), pending.begin() + static_cast<std::ptrdiff_t>(stable));
                    pending.erase(pending.begin(), pending.begin() + static_cast<std::ptrdiff_t>(stable));
                }
                log("event=stream_chunk text_index=" + std::to_string(i) +
                    " stream_index=" + std::to_string(stream_index) +
                    " samples=" + std::to_string(samples) + " final=" + (is_last ? "1" : "0"));
            });
            speech.t3_ms += result.t3_ms;
            speech.s3gen_ms += result.s3gen_ms;
            speech.t3_tokens += result.t3_tokens;
            log("event=chunk index=" + std::to_string(i) + " t3_tokens=" + std::to_string(result.t3_tokens) +
                " samples=" + std::to_string(result.pcm.size()) +
                " seconds=" + std::to_string(result.pcm.size() / static_cast<double>(kRate)) +
                " t3_ms=" + std::to_string(result.t3_ms) + " s3gen_ms=" + std::to_string(result.s3gen_ms));
        }
        if (!pending.empty()) {
            if (sink) sink(pending.data(), pending.size());
            speech.pcm.insert(speech.pcm.end(), pending.begin(), pending.end());
        }
        return speech;
    }
};

Session::Session(const Runtime& runtime, const EngineKnobs& knobs, int chunk_chars, int stream_chunk_tokens,
                 int stream_first_chunk_tokens, float quiet_amp2, int first_chunk_chars)
    : impl_(std::make_unique<Impl>(runtime, knobs, chunk_chars, stream_chunk_tokens, stream_first_chunk_tokens,
                                  quiet_amp2, first_chunk_chars)) {}
Session::~Session() = default;
Speech Session::synthesize(const std::string& text, const AudioSink& sink) { return impl_->synthesize(text, sink); }
const Runtime& Session::runtime() const noexcept { return impl_->runtime; }
const EngineKnobs& Session::knobs() const noexcept { return impl_->knobs; }
int Session::chunk_chars() const noexcept { return impl_->chunk_chars; }

}
