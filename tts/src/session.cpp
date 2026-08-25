#include "session.hpp"
#include "cli.hpp"
#include <tts-cpp/chatterbox/engine.h>
#include <algorithm>
#include <chrono>
#include <stdexcept>
#include <utility>

namespace tts {

struct Session::Impl {
    Runtime runtime;
    EngineKnobs knobs;
    int chunk_chars, first_chunk_chars;
    float quiet_amp2;
    std::unique_ptr<tts_cpp::chatterbox::Engine> engine;

    Impl(Runtime r, EngineKnobs k, int chars, float quiet, int first_chars)
        : runtime(std::move(r)), knobs(std::move(k)), chunk_chars(chars),
          first_chunk_chars(first_chars > 0 ? first_chars : chars), quiet_amp2(quiet) {
        tts_cpp::chatterbox::EngineOptions o;
        o.t3_gguf_path = runtime.t3; o.s3gen_gguf_path = runtime.s3; o.reference_audio = knobs.reference;
        o.n_gpu_layers = runtime.gpu; o.n_threads = runtime.threads; o.n_ctx = runtime.context; o.fastconv = runtime.fastconv;
        o.language = knobs.language; o.seed = knobs.seed; o.n_predict = knobs.max_tokens; o.top_k = knobs.top_k;
        o.top_p = knobs.top_p; o.min_p = knobs.min_p; o.temperature = knobs.temperature; o.repeat_penalty = knobs.repeat_penalty;
        o.cfg_weight = knobs.cfg_weight; o.exaggeration = knobs.exaggeration; o.cfm_steps = knobs.cfm_steps;
        const auto t0 = std::chrono::steady_clock::now();
        engine = std::make_unique<tts_cpp::chatterbox::Engine>(o);
        log("event=engine_ready init_ms=" + std::to_string(std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count()) +
            " family_config_resident=1 fastconv=" + (runtime.fastconv ? "1" : "0"));
    }

    void metrics(Speech& speech, const tts_cpp::chatterbox::SynthesisResult& r) {
        speech.t3_ms += r.t3_ms; speech.s3gen_ms += r.s3gen_ms; speech.t3_tokens += r.t3_tokens;
    }

    Speech synthesize(const std::string& text, const AudioSink& sink, bool streaming, JoinMode join) {
        const auto started = std::chrono::steady_clock::now();
        const auto pieces = streaming ? pack_text_staged(text, first_chunk_chars, chunk_chars) : pack_text(text, chunk_chars);
        Speech speech; speech.chunks = static_cast<int>(pieces.size());
        bool first_audio = true;
        auto first = [&] {
            if (!first_audio) return;
            first_audio = false;
            speech.ttfa_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
        };

        std::vector<float> pending;
        for (int i = 0; i < speech.chunks; ++i) {
            try {
                auto r = engine->synthesize(pieces[static_cast<std::size_t>(i)]);
                first();
                metrics(speech, r);

                if (!streaming) {
                    if (join == JoinMode::Crossfade) glue(speech.pcm, r.pcm, quiet_amp2);
                    else speech.pcm.insert(speech.pcm.end(), r.pcm.begin(), r.pcm.end());
                    continue;
                }

                if (join == JoinMode::Chunks) {
                    if (sink) sink(r.pcm.data(), r.pcm.size());
                    speech.pcm.insert(speech.pcm.end(), r.pcm.begin(), r.pcm.end());
                    continue;
                }

                glue(pending, r.pcm, quiet_amp2);
                const bool last = i + 1 == speech.chunks;
                const std::size_t keep = last ? 0 : std::min<std::size_t>(pending.size(), kGlue);
                const std::size_t stable = pending.size() - keep;
                if (stable) {
                    if (sink) sink(pending.data(), stable);
                    speech.pcm.insert(speech.pcm.end(), pending.begin(), pending.begin() + static_cast<std::ptrdiff_t>(stable));
                    pending.erase(pending.begin(), pending.begin() + static_cast<std::ptrdiff_t>(stable));
                }
            } catch (const std::exception& error) {
                throw std::runtime_error("speech unit " + std::to_string(i + 1) + "/" + std::to_string(speech.chunks) + " failed: " + error.what());
            }
        }
        if (!pending.empty()) {
            if (sink) sink(pending.data(), pending.size());
            speech.pcm.insert(speech.pcm.end(), pending.begin(), pending.end());
        }
        return speech;
    }
};

Session::Session(const Runtime& runtime, const EngineKnobs& knobs, int chunk_chars, float quiet_amp2, int first_chunk_chars)
    : impl_(std::make_unique<Impl>(runtime, knobs, chunk_chars, quiet_amp2, first_chunk_chars)) {}
Session::~Session() = default;
Speech Session::synthesize(const std::string& text, const AudioSink& sink, bool streaming, JoinMode join) { return impl_->synthesize(text, sink, streaming, join); }
const Runtime& Session::runtime() const noexcept { return impl_->runtime; }
const EngineKnobs& Session::knobs() const noexcept { return impl_->knobs; }
int Session::chunk_chars() const noexcept { return impl_->chunk_chars; }

}
