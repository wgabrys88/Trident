#include "engine_wrapper.hpp"
#include <tts-cpp/chatterbox/engine.h>
#include <algorithm>
#include <cctype>
#include <cmath>
#include <filesystem>
#include <iostream>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <tuple>

namespace tts {

struct EngineWrapper::Impl {
    std::string t3, s3;
    int gpu = 0, threads = 0, context = 0;
    std::mutex state, synth;
    std::shared_ptr<tts_cpp::chatterbox::Engine> engine;
    Voice voice;
    std::unique_lock<std::mutex> job;
    std::vector<std::string> pieces;
    Speech speech;
    int index = 0;
    int handed = 0;
};

EngineWrapper::EngineWrapper() : impl_(std::make_unique<Impl>()) {}
EngineWrapper::~EngineWrapper() = default;

void EngineWrapper::initialize(const std::string& t3, const std::string& s3, int gpu, int threads, int context) {
    if (!std::filesystem::is_regular_file(t3) || !std::filesystem::is_regular_file(s3))
        throw std::runtime_error("model file missing");
    impl_->t3 = t3;
    impl_->s3 = s3;
    impl_->gpu = gpu;
    impl_->threads = threads;
    impl_->context = context;
}

static std::vector<std::string> pack_text(const std::string& text, int limit) {
    if (limit < 40) limit = 40;
    if (text.empty()) return {text};
    auto is_ws = [](unsigned char c) { return std::isspace(c) != 0; };

    std::vector<std::string> sentences;
    std::string cur;
    size_t i = 0;
    while (i < text.size()) {
        cur += text[i];
        const char c = text[i];
        const bool at_end = (i + 1 == text.size());
        const bool nx_ws = !at_end && is_ws(static_cast<unsigned char>(text[i + 1]));
        if ((c == '.' || c == '?' || c == '!') && (at_end || nx_ws)) {
            size_t j = i + 1;
            while (j < text.size() && is_ws(static_cast<unsigned char>(text[j]))) {
                cur += text[j];
                ++j;
            }
            sentences.push_back(cur);
            cur.clear();
            i = j;
        } else {
            ++i;
        }
    }
    if (!cur.empty()) sentences.push_back(cur);

    std::vector<std::string> refined;
    for (auto& sentence : sentences) {
        if (static_cast<int>(sentence.size()) <= limit) {
            refined.push_back(std::move(sentence));
            continue;
        }
        std::string acc;
        size_t k = 0;
        while (k < sentence.size()) {
            acc += sentence[k];
            const char c = sentence[k];
            const bool nx_ws = (k + 1 < sentence.size()) && is_ws(static_cast<unsigned char>(sentence[k + 1]));
            if ((c == ',' || c == ':' || c == ';') && nx_ws && static_cast<int>(acc.size()) > limit / 2) {
                size_t j = k + 1;
                while (j < sentence.size() && is_ws(static_cast<unsigned char>(sentence[j]))) {
                    acc += sentence[j];
                    ++j;
                }
                refined.push_back(acc);
                acc.clear();
                k = j;
                continue;
            }
            ++k;
        }
        if (!acc.empty()) refined.push_back(acc);
    }

    std::vector<std::string> packed;
    for (auto& sentence : refined) {
        if (!packed.empty() && static_cast<int>(packed.back().size() + sentence.size()) <= limit)
            packed.back() += sentence;
        else
            packed.push_back(std::move(sentence));
    }
    for (auto& sentence : packed) {
        while (!sentence.empty() && is_ws(static_cast<unsigned char>(sentence.back())))
            sentence.pop_back();
    }
    packed.erase(std::remove_if(packed.begin(), packed.end(),
        [](const std::string& sentence) { return sentence.empty(); }), packed.end());
    return packed.empty() ? std::vector<std::string>{text} : packed;
}

static int quiet_edge(const std::vector<float>& x, bool tail) {
    const int n = static_cast<int>(x.size());
    int i = 0;
    while (i < n) {
        const float s = tail ? x[n - 1 - i] : x[i];
        if (s * s >= 0.0004f) break;
        ++i;
    }
    return i;
}

static void glue(std::vector<float>& dst, const std::vector<float>& src) {
    if (dst.empty()) { dst = src; return; }
    if (src.empty()) return;
    const int cap = std::min(kGlue, static_cast<int>(std::min(dst.size(), src.size())));
    int n = std::min(quiet_edge(dst, true), quiet_edge(src, false));
    n = std::min(cap, std::max(n, std::min(480, cap)));
    const float step = 1.5707963267948966f / static_cast<float>(std::max(n, 1));
    for (int i = 0; i < n; ++i) {
        const float w = static_cast<float>(i) * step;
        dst[dst.size() - n + i] = dst[dst.size() - n + i] * std::cos(w) + src[i] * std::sin(w);
    }
    dst.insert(dst.end(), src.begin() + n, src.end());
}

void EngineWrapper::prepare(const Voice& voice, const std::string& text) {
    finish();
    impl_->job = std::unique_lock<std::mutex>(impl_->synth);
    {
        std::lock_guard<std::mutex> lock(impl_->state);
        const Voice& have = impl_->voice;
        auto key = [](const Voice& v) {
            return std::tie(v.reference, v.language, v.reference_mtime, v.seed, v.max_tokens,
                v.top_k, v.cfm_steps, v.exaggeration, v.cfg_weight, v.temperature, v.repeat_penalty, v.min_p, v.top_p);
        };
        if (!impl_->engine || key(have) != key(voice)) {
            if (!std::filesystem::is_regular_file(voice.reference))
                throw std::runtime_error("reference audio not found: " + voice.reference);
            tts_cpp::chatterbox::EngineOptions options;
            options.t3_gguf_path = impl_->t3;
            options.s3gen_gguf_path = impl_->s3;
            options.n_gpu_layers = impl_->gpu;
            options.n_threads = impl_->threads;
            options.n_ctx = impl_->context;
            options.reference_audio = voice.reference;
            options.language = voice.language;
            options.seed = voice.seed;
            options.n_predict = voice.max_tokens;
            options.top_k = voice.top_k;
            options.top_p = voice.top_p;
            options.min_p = voice.min_p;
            options.temperature = voice.temperature;
            options.repeat_penalty = voice.repeat_penalty;
            options.cfg_weight = voice.cfg_weight;
            options.exaggeration = voice.exaggeration;
            options.cfm_steps = voice.cfm_steps;
            impl_->engine = std::make_shared<tts_cpp::chatterbox::Engine>(options);
            impl_->voice = voice;
            std::cerr << "tts engine t3=" << impl_->t3
                      << " s3=" << impl_->s3
                      << " gpu=" << impl_->gpu
                      << " threads=" << impl_->threads
                      << " n_ctx=" << impl_->context
                      << " lang=" << voice.language
                      << " seed=" << voice.seed
                      << " max_tokens=" << voice.max_tokens
                      << " top_k=" << voice.top_k
                      << " top_p=" << voice.top_p
                      << " min_p=" << voice.min_p
                      << " temp=" << voice.temperature
                      << " repeat=" << voice.repeat_penalty
                      << " cfg=" << voice.cfg_weight
                      << " exag=" << voice.exaggeration
                      << " cfm=" << voice.cfm_steps
                      << " chunk=" << voice.chunk_chars
                      << std::endl;
        }
    }
    impl_->pieces = pack_text(text, voice.chunk_chars);
    impl_->speech = {};
    impl_->speech.chunks = static_cast<int>(impl_->pieces.size());
    impl_->index = 0;
    impl_->handed = 0;
}

bool EngineWrapper::busy() const {
    return impl_->index < static_cast<int>(impl_->pieces.size());
}

bool EngineWrapper::step(const std::function<void(int, int, const std::vector<float>&, const std::vector<float>&)>& on_pack) {
    if (!busy()) return false;
    std::shared_ptr<tts_cpp::chatterbox::Engine> engine;
    {
        std::lock_guard<std::mutex> lock(impl_->state);
        engine = impl_->engine;
    }
    if (!engine) throw std::runtime_error("tts engine is not loaded");
    auto result = engine->synthesize(impl_->pieces[impl_->index]);
    glue(impl_->speech.pcm, result.pcm);
    impl_->speech.t3_ms += result.t3_ms;
    impl_->speech.s3gen_ms += result.s3gen_ms;
    const bool last = impl_->index + 1 == static_cast<int>(impl_->pieces.size());
    const int from = impl_->handed;
    int hold = last ? 0 : std::min(kGlue, static_cast<int>(impl_->speech.pcm.size()));
    int to = static_cast<int>(impl_->speech.pcm.size()) - hold;
    if (to <= from) {
        hold = 0;
        to = static_cast<int>(impl_->speech.pcm.size());
    }
    std::vector<float> playable;
    if (to > from)
        playable.assign(impl_->speech.pcm.begin() + from, impl_->speech.pcm.begin() + to);
    impl_->handed = from + static_cast<int>(playable.size());
    if (on_pack && !playable.empty())
        on_pack(impl_->index, impl_->speech.chunks, impl_->speech.pcm, playable);
    impl_->index++;
    return busy();
}

Speech EngineWrapper::finish() {
    Speech out = impl_->speech;
    impl_->pieces.clear();
    impl_->index = 0;
    impl_->handed = 0;
    impl_->speech = {};
    if (impl_->job.owns_lock()) impl_->job.unlock();
    return out;
}

void EngineWrapper::cancel() {
    std::shared_ptr<tts_cpp::chatterbox::Engine> engine;
    {
        std::lock_guard<std::mutex> lock(impl_->state);
        engine = impl_->engine;
    }
    if (engine) engine->cancel();
}

}
