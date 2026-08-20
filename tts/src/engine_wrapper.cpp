#include "engine_wrapper.hpp"
#include <tts-cpp/chatterbox/engine.h>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <filesystem>
#include <stdexcept>

namespace tts {

struct EngineWrapper::Impl {
    std::string t3, s3;
    int gpu = 0, threads = 0, context = 0;
};

EngineWrapper::EngineWrapper(const std::string& t3, const std::string& s3, int gpu, int threads, int context)
    : impl_(std::make_unique<Impl>()) {
    if (!std::filesystem::is_regular_file(t3) || !std::filesystem::is_regular_file(s3))
        throw std::runtime_error("model file missing");
    if (gpu < 0 || threads < 1 || context < 1)
        throw std::runtime_error("invalid engine runtime configuration");
    impl_->t3 = t3;
    impl_->s3 = s3;
    impl_->gpu = gpu;
    impl_->threads = threads;
    impl_->context = context;
}

EngineWrapper::~EngineWrapper() = default;

static std::vector<std::string> pack_text(const std::string& text, int limit) {
    if (limit < 40) limit = 40;
    if (text.empty()) return {text};
    auto is_ws = [](unsigned char c) { return std::isspace(c) != 0; };
    auto trim_right = [&](std::string& s) {
        while (!s.empty() && is_ws(static_cast<unsigned char>(s.back()))) s.pop_back();
    };
    auto glue = [&](std::string& dst, const std::string& src) {
        if (src.empty()) return;
        if (!dst.empty() && !is_ws(static_cast<unsigned char>(dst.back())) &&
            !is_ws(static_cast<unsigned char>(src.front())))
            dst += ' ';
        dst += src;
    };

    std::vector<std::string> sentences;
    std::string cur;
    size_t i = 0;
    while (i < text.size()) {
        cur += text[i];
        const char c = text[i];
        const bool at_end = i + 1 == text.size();
        const bool next_ws = !at_end && is_ws(static_cast<unsigned char>(text[i + 1]));
        if ((c == '.' || c == '?' || c == '!') && (at_end || next_ws)) {
            size_t j = i + 1;
            while (j < text.size() && is_ws(static_cast<unsigned char>(text[j]))) cur += text[j++];
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
            const bool next_ws = k + 1 < sentence.size() && is_ws(static_cast<unsigned char>(sentence[k + 1]));
            if ((c == ',' || c == ':' || c == ';') && next_ws && static_cast<int>(acc.size()) > limit / 2) {
                size_t j = k + 1;
                while (j < sentence.size() && is_ws(static_cast<unsigned char>(sentence[j]))) acc += sentence[j++];
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
        trim_right(sentence);
        if (sentence.empty()) continue;
        if (packed.empty()) {
            packed.push_back(std::move(sentence));
            continue;
        }
        const int extra = (!is_ws(static_cast<unsigned char>(packed.back().back())) &&
                           !is_ws(static_cast<unsigned char>(sentence.front()))) ? 1 : 0;
        if (static_cast<int>(packed.back().size()) + extra + static_cast<int>(sentence.size()) <= limit)
            glue(packed.back(), sentence);
        else
            packed.push_back(std::move(sentence));
    }
    if (packed.size() >= 2 && static_cast<int>(packed.back().size()) * 2 < limit) {
        glue(packed[packed.size() - 2], packed.back());
        packed.pop_back();
    }
    return packed.empty() ? std::vector<std::string>{text} : packed;
}

static int quiet_edge(const std::vector<float>& x, bool tail) {
    const int n = static_cast<int>(x.size());
    int i = 0;
    while (i < n) {
        const float sample = tail ? x[n - 1 - i] : x[i];
        if (sample * sample >= 0.0004f) break;
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

Speech EngineWrapper::synthesize(const Voice& voice, const std::string& text) {
    if (!std::filesystem::is_regular_file(voice.reference))
        throw std::runtime_error("reference audio not found: " + voice.reference);
    if (text.empty()) throw std::runtime_error("text is empty");

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

    tts_cpp::chatterbox::Engine engine(options);
    const auto pieces = pack_text(text, voice.chunk_chars);
    Speech speech;
    speech.chunks = static_cast<int>(pieces.size());
    for (const auto& piece : pieces) {
        auto result = engine.synthesize(piece);
        glue(speech.pcm, result.pcm);
        speech.t3_ms += result.t3_ms;
        speech.s3gen_ms += result.s3gen_ms;
    }
    if (speech.pcm.empty()) throw std::runtime_error("synthesis returned no audio");
    return speech;
}

}
