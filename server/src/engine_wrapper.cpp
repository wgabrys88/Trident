#include "engine_wrapper.hpp"
#include <tts-cpp/chatterbox/engine.h>
#include <filesystem>
#include <memory>
#include <mutex>
#include <stdexcept>

namespace tts {

struct EngineWrapper::Impl {
    std::string t3, s3;
    int gpu = 0, threads = 0, context = 0;
    std::mutex state, synth;
    std::shared_ptr<tts_cpp::chatterbox::Engine> engine;
    Voice voice;
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
    std::vector<std::string> bits;
    std::string cur;
    auto flush = [&] {
        while (!cur.empty() && cur.back() == ' ') cur.pop_back();
        if (!cur.empty()) bits.push_back(cur);
        cur.clear();
    };
    for (size_t i = 0; i < text.size(); ++i) {
        cur.push_back(text[i]);
        const unsigned char c = static_cast<unsigned char>(text[i]);
        if ((c == '.' || c == '!' || c == '?' || c == ';') &&
            (i + 1 == text.size() || text[i + 1] == ' ' || text[i + 1] == '\n'))
            flush();
    }
    flush();
    if (bits.empty()) return {text};
    std::vector<std::string> packed;
    std::string buf = bits[0];
    for (size_t i = 1; i < bits.size(); ++i) {
        if (static_cast<int>(buf.size() + 1 + bits[i].size()) > limit) {
            packed.push_back(buf);
            buf = bits[i];
        } else {
            buf += " " + bits[i];
        }
    }
    packed.push_back(buf);
    return packed;
}

Speech EngineWrapper::speak(const Voice& voice, const std::string& text) {
    std::lock_guard<std::mutex> synth(impl_->synth);
    std::shared_ptr<tts_cpp::chatterbox::Engine> engine;
    {
        std::lock_guard<std::mutex> lock(impl_->state);
        const bool same = impl_->engine
            && impl_->voice.reference == voice.reference
            && impl_->voice.language == voice.language
            && impl_->voice.reference_mtime == voice.reference_mtime;
        if (!same) {
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
            options.repeat_penalty = voice.repeat;
            options.cfg_weight = voice.cfg;
            options.exaggeration = voice.exaggeration;
            options.cfm_steps = voice.cfm_steps;
            impl_->engine = std::make_shared<tts_cpp::chatterbox::Engine>(options);
            impl_->voice = voice;
        }
        engine = impl_->engine;
    }
    Speech out;
    const auto pieces = pack_text(text, voice.chunk_chars);
    out.chunks = static_cast<int>(pieces.size());
    for (size_t i = 0; i < pieces.size(); ++i) {
        auto result = engine->synthesize(pieces[i]);
        if (i) out.pcm.insert(out.pcm.end(), 2880, 0.f);
        out.pcm.insert(out.pcm.end(), result.pcm.begin(), result.pcm.end());
        out.t3_ms += result.t3_ms;
        out.s3gen_ms += result.s3gen_ms;
    }
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
