#include "session.hpp"
#include "audio.hpp"
#include "cli.hpp"

#include <chrono>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#include "tts-cpp/chatterbox/engine.h"

namespace tts {

struct Session::Impl {
    Runtime runtime;
    EngineKnobs knobs;
    int chunk_chars;
    float quiet_amp2;
    int first_chunk_chars;
    std::unique_ptr<tts_cpp::chatterbox::Engine> engine;

    Impl(const std::string& t3_gguf, const std::string& s3gen_gguf, const Runtime& r, const EngineKnobs& k, int cc, float qa, int fcc)
        : runtime(r), knobs(k), chunk_chars(cc), quiet_amp2(qa), first_chunk_chars(fcc) {
        tts_cpp::chatterbox::EngineOptions opts;
        opts.t3_gguf_path = t3_gguf;
        opts.s3gen_gguf_path = s3gen_gguf;
        opts.reference_audio = k.reference;
        opts.n_gpu_layers = r.n_gpu_layers;
        opts.n_threads = r.threads;
        opts.seed = k.seed;
        opts.n_predict = k.max_tokens;
        opts.n_ctx = r.context;
        opts.top_k = k.top_k;
        opts.top_p = k.top_p;
        opts.temperature = k.temperature;
        opts.repeat_penalty = k.repeat_penalty;
        opts.language = k.language;
        opts.exaggeration = k.exaggeration;
        opts.cfg_weight = k.cfg_weight;
        opts.min_p = k.min_p;
        opts.cfm_steps = k.cfm_steps;
        opts.stream_chunk_tokens = r.stream_chunk_tokens;
        opts.stream_first_chunk_tokens = r.stream_first_chunk_tokens;
        opts.stream_cfm_steps = r.stream_cfm_steps;
        opts.fastconv = r.fastconv != 0;
        engine = std::make_unique<tts_cpp::chatterbox::Engine>(opts);
    }

    std::vector<Session::Speech> synthesize_pieces(const std::vector<std::string>& texts, const StreamingSink& sink) {
        std::vector<std::string> pieces;
        pieces.reserve(texts.size());
        for (const auto& t : texts) {
            auto packed = pack_text_staged(t, first_chunk_chars, chunk_chars);
            for (auto& p : packed) pieces.push_back(std::move(p));
        }
        auto raw = engine->synthesize_pieces(pieces,
            [sink](int, const float* pcm, std::size_t n, int ci, bool last) {
                if (n == 0) return;
                if (sink) sink(pcm, n, ci, last);
            });
        std::vector<Session::Speech> out;
        out.reserve(raw.size());
        for (auto& pr : raw) {
            Session::Speech s;
            s.pcm = std::move(pr.pcm);
            s.t3_ms = pr.t3_ms;
            s.s3gen_ms = pr.s3gen_ms;
            s.t3_tokens = pr.t3_tokens;
            s.sample_rate = pr.sample_rate;
            out.push_back(std::move(s));
        }
        return out;
    }
};

Session::Session(const std::string& t3_gguf, const std::string& s3gen_gguf, const Runtime& runtime, const EngineKnobs& knobs, int chunk_chars, float quiet_amp2, int first_chunk_chars)
    : pimpl_(new Impl(t3_gguf, s3gen_gguf, runtime, knobs, chunk_chars, quiet_amp2, first_chunk_chars)) {}

Session::~Session() { delete pimpl_; }

std::vector<Session::Speech> Session::synthesize_pieces(const std::vector<std::string>& texts, const StreamingSink& sink) {
    return pimpl_->synthesize_pieces(texts, sink);
}

void Session::request_cancel() {
    if (pimpl_ && pimpl_->engine) pimpl_->engine->cancel();
}

}
